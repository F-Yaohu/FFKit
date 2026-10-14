"""自检脚本：不需要 GUI、不需要真实 ffmpeg 也能跑。

用途：
1. 验证核心层（时间解析 / 路径与转义 / 命令构建 / 校验层）的逻辑正确性；
2. 当作改动后的回归测试。

运行：python selftest.py
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.core.builder import BuildError, CommandBuilder  # noqa: E402
from app.core.paths import (  # noqa: E402
    escape_concat_path,
    make_temp_output,
    muxer_format_for,
    quote_argv,
    unique_output,
    unique_output_multi,
    write_concat_list,
)
from app.core.prober import MediaInfo  # noqa: E402
from app.core.spec import (  # noqa: E402
    OP_COMPRESS,
    OP_CONVERT,
    OP_EXTRACT_AUDIO,
    OP_EXTRACT_FRAME,
    OP_MERGE,
    OP_STRIP_AUDIO,
    OP_TRIM,
    OperationSpec,
)
from app.core.timeparse import (  # noqa: E402
    TimeParseError,
    filename_timestamp,
    format_hms,
    format_timestamp,
    human_size,
    parse_time,
    size_delta,
)
from app.core.validator import Severity, Validator  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(f"{name}" + (f"  → {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} " + "─" * max(0, 46 - len(title)))


# ---------------------------------------------------------------------------
class StubProber:
    """假 prober：让 builder 在没有真实 ffprobe 的情况下也能跑通。"""

    def __init__(self, infos: dict[str, MediaInfo], keyframes: dict[str, list[float]] | None = None):
        self.infos = infos
        self.keyframes_map = keyframes or {}

    def get(self, path: str) -> MediaInfo:
        info = self.infos.get(path)
        if info is None:
            return MediaInfo(path=path, ok=False, error="stub 未提供该文件")
        return info

    def probe(self, path: str) -> MediaInfo:
        return self.get(path)

    def keyframes(self, path: str, timeout: float = 180.0) -> list[float]:
        return self.keyframes_map.get(path, [])


def make_info(path: str, **kwargs) -> MediaInfo:
    base = dict(
        path=path,
        ok=True,
        size=120 * 1024 * 1024,
        duration=120.0,
        format_name="mov,mp4,m4a,3gp,3g2,mj2",
        bit_rate=8_000_000,
        v_codec="h264",
        v_width=1920,
        v_height=1080,
        v_fps=30.0,
        v_fps_raw="30/1",
        v_pix_fmt="yuv420p",
        v_sar="1:1",
        v_bit_rate=7_800_000,
        a_codec="aac",
        a_channels=2,
        a_sample_rate=48000,
        a_bit_rate=192_000,
    )
    base.update(kwargs)
    return MediaInfo(**base)


def joined(argv: list[str]) -> str:
    return " ".join(argv)


# ---------------------------------------------------------------------------
def test_timeparse() -> None:
    section("时间解析（需求 5.5）")
    for text, expected in [("83", 83.0), ("83.5", 83.5), ("01:23", 83.0), ("00:01:23", 83.0),
                           ("1:02:03", 3723.0), ("00:01:23.456", 83.456)]:
        try:
            value = parse_time(text)
        except TimeParseError as exc:
            check(f"解析 {text}", False, str(exc))
            continue
        check(f"解析 {text}", abs((value or 0) - expected) < 0.001, f"得到 {value}")
    check("空字符串 → None", parse_time("  ") is None)

    for bad in ("abc", "1:99", "-5", "1:2:3:4", "12:aa"):
        try:
            parse_time(bad)
            check(f"非法输入被拒绝 {bad}", False, "未抛出异常")
        except TimeParseError:
            check(f"非法输入被拒绝 {bad}", True)

    check("format_hms(83)", format_hms(83) == "00:01:23", format_hms(83))
    check("format_hms 毫秒进位", format_hms(59.9996, ms=True) == "00:01:00.000", format_hms(59.9996, ms=True))
    check("format_timestamp(83.456)", format_timestamp(83.456) == "00:01:23.456", format_timestamp(83.456))
    check("剪辑文件名时间戳", filename_timestamp(83) == "00.01.23", filename_timestamp(83))
    check("跨小时时间戳", filename_timestamp(3723) == "01.02.03", filename_timestamp(3723))
    check("human_size", human_size(128 * 1024 * 1024) == "128.0 MB", human_size(128 * 1024 * 1024))
    check("size_delta", size_delta(128 * 1024 * 1024, 41 * 1024 * 1024) == "128.0 MB → 41.0 MB (-68%)",
          size_delta(128 * 1024 * 1024, 41 * 1024 * 1024))


def test_paths() -> None:
    section("路径 / 转义 / 临时文件（需求 5.1 / 5.2 / 5.4）")
    with tempfile.TemporaryDirectory() as tmp:
        target = os.path.join(tmp, "带 空格'引号'.mp4")
        check("不存在的输出保持原名", unique_output(target) == target)
        open(target, "wb").close()
        resolved = unique_output(target)
        check("冲突时追加序号", resolved.endswith("_1.mp4"), resolved)

        wanted = [os.path.join(tmp, "a.mp4"), os.path.join(tmp, "a.mp4")]
        resolved_multi = unique_output_multi(wanted)
        check("同批内不会互相覆盖", resolved_multi[0] != resolved_multi[1], str(resolved_multi))

        check("临时文件命名", make_temp_output(target).endswith(".mp4.tmp"),
              make_temp_output(target))
        check("muxer 映射 mp4", muxer_format_for("x.mp4") == "mp4")
        check("muxer 映射 mkv", muxer_format_for("x.mkv") == "matroska")
        check("muxer 映射未知扩展名为 None", muxer_format_for("x.zzz") is None)

        check("concat 单引号转义", escape_concat_path("a'b.mp4") == "a'\\''b.mp4",
              escape_concat_path("a'b.mp4"))

        list_file = os.path.join(tmp, "list.txt")
        write_concat_list([os.path.join(tmp, "一'个 文件.mp4")], list_file)
        raw = open(list_file, "rb").read()
        check("concat 列表无 BOM", not raw.startswith(b"\xef\xbb\xbf"), repr(raw[:6]))
        text = raw.decode("utf-8")
        check("concat 列表转义正确", "file '" in text and "'\\''" in text, text.strip())

        argv = ["ffmpeg", "-i", target, os.path.join(tmp, "out put.mp4")]
        rendered = quote_argv(argv)
        check("命令行渲染带引号", '"' in rendered, rendered)


def test_builder(info: MediaInfo, stub: StubProber) -> None:
    section("命令构建（需求六）")
    builder = CommandBuilder("ffmpeg", stub)
    src = info.path
    out_dir = os.path.dirname(src)

    # --- 1. 无损剪辑 ---
    spec = OperationSpec(op=OP_TRIM, inputs=[src], output=os.path.join(out_dir, "t_trim.mp4"),
                         params={"mode": "copy", "start": 10.0, "end": 25.0}, info=info)
    job = builder.build(spec)
    argv = job.steps[0].argv
    check("无损剪辑用 -c copy", "-c" in argv and argv[argv.index("-c") + 1] == "copy")
    check("时长用 -t 而非 -to", "-t" in argv and "-to" not in argv)
    check("无损剪辑带 -ss", argv[argv.index("-ss") + 1] == "10")
    check("明确提示关键帧精度代价", any("关键帧" in n for n in job.notes), str(job.notes))
    check("输出走临时文件 + 原子重命名",
          job.steps[0].temp_out == spec.output + ".tmp" and job.steps[0].final_out == spec.output,
          f"{job.steps[0].temp_out} / {job.steps[0].final_out}")
    check("临时文件显式指定 muxer", "-f" in argv and argv[argv.index("-f") + 1] == "mp4")

    # --- 2. 精确 / 绝对精确 ---
    job_precise = builder.build(OperationSpec(op=OP_TRIM, inputs=[src], output=os.path.join(out_dir, "p.mp4"),
                                             params={"mode": "precise", "start": 1, "end": 5}, info=info))
    argv = job_precise.steps[0].argv
    check("精确切割 -ss 在 -i 之前", argv.index("-ss") < argv.index("-i"))
    check("精确切割在重编码", "libx264" in argv)

    job_abs = builder.build(OperationSpec(op=OP_TRIM, inputs=[src], output=os.path.join(out_dir, "a.mp4"),
                                         params={"mode": "absolute", "start": 1, "end": 5}, info=info))
    argv = job_abs.steps[0].argv
    check("绝对精确 -ss 在 -i 之后", argv.index("-ss") > argv.index("-i"))

    # --- 2b. 批量共用时间范围：超出单文件时长时要有明确说明 ---
    job_over = builder.build(OperationSpec(op=OP_TRIM, inputs=[src], output=os.path.join(out_dir, "over.mp4"),
                                           params={"mode": "precise", "start": 10, "end": 200}, info=info))
    check("切割超出时长给出截断说明", any("截断" in n for n in job_over.notes), str(job_over.notes))
    job_within = builder.build(OperationSpec(op=OP_TRIM, inputs=[src], output=os.path.join(out_dir, "within.mp4"),
                                             params={"mode": "precise", "start": 10, "end": 50}, info=info))
    check("未超时长不误报截断", not any("截断" in n for n in job_within.notes), str(job_within.notes))

    # --- 3. 智能切割 ---
    smart_src = os.path.join(out_dir, "smart.mp4")
    smart_info = make_info(smart_src)
    keyframes = [0.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    smart_stub = StubProber({smart_src: smart_info}, {smart_src: keyframes})
    smart_builder = CommandBuilder("ffmpeg", smart_stub)
    job_smart = smart_builder.build(
        OperationSpec(op=OP_TRIM, inputs=[smart_src], output=os.path.join(out_dir, "smart_out.mp4"),
                      params={"mode": "smart", "start": 12.0, "end": 27.0}, info=smart_info)
    )
    labels = [s.label for s in job_smart.steps]
    check("智能切割拆成多步", len(job_smart.steps) >= 3, str(labels))
    check("智能切割中间段无损复制",
          any("copy" in s.argv and "-c" in s.argv for s in job_smart.steps), str(labels))
    check("智能切割拼接用 concat demuxer",
          any("concat" in s.argv for s in job_smart.steps))
    check("智能切割带 -safe 0",
          any("-safe" in s.argv for s in job_smart.steps))

    # 首尾重编码过长 → 降级
    far_src = os.path.join(out_dir, "far.mp4")
    far_info = make_info(far_src)
    far_stub = StubProber({far_src: far_info}, {far_src: [0.0, 60.0, 120.0]})
    job_far = CommandBuilder("ffmpeg", far_stub).build(
        OperationSpec(op=OP_TRIM, inputs=[far_src], output=os.path.join(out_dir, "far_out.mp4"),
                      params={"mode": "smart", "start": 30.0, "end": 119.0}, info=far_info)
    )
    check("智能切割不划算时自动降级", len(job_far.steps) == 1 and bool(job_far.warnings),
          str(job_far.warnings))

    # --- 4. 合并：一致 → concat demuxer ---
    src_a = os.path.join(out_dir, "a1.mp4")
    src_b = os.path.join(out_dir, "b1.mp4")
    info_a, info_b = make_info(src_a), make_info(src_b, duration=60.0)
    merge_stub = StubProber({src_a: info_a, src_b: info_b})
    job_merge = CommandBuilder("ffmpeg", merge_stub).build(
        OperationSpec(op=OP_MERGE, inputs=[src_a, src_b], output=os.path.join(out_dir, "merged.mp4"))
    )
    argv = job_merge.steps[0].argv
    check("一致参数走 concat demuxer", "concat" in argv and "copy" in argv)
    check("concat 使用 -safe 0", argv[argv.index("-safe") + 1] == "0")

    # --- 5. 合并：不一致 → concat filter ---
    info_c = make_info(src_b, v_width=1280, v_height=720)
    bad_stub = StubProber({src_a: info_a, src_b: info_c})
    job_bad = CommandBuilder("ffmpeg", bad_stub).build(
        OperationSpec(op=OP_MERGE, inputs=[src_a, src_b], output=os.path.join(out_dir, "merged2.mp4"))
    )
    argv = job_bad.steps[0].argv
    check("不一致自动降级 concat filter", "-filter_complex" in argv and "concat=n=2" in joined(argv))
    check("降级时给出明确提示", bool(job_bad.warnings), str(job_bad.warnings))

    # --- 5b. 合并到纯音频容器（mp3）：重编码、仅取音轨、丢弃画面有提示 ---
    job_mp3 = CommandBuilder("ffmpeg", merge_stub).build(
        OperationSpec(op=OP_MERGE, inputs=[src_a, src_b], output=os.path.join(out_dir, "m.mp3"))
    )
    argv = job_mp3.steps[0].argv
    check("mp3 输出重编码为 libmp3lame", "libmp3lame" in argv, joined(argv))
    check("mp3 输出只拼音轨", "concat=n=2:v=0:a=1" in joined(argv), joined(argv))
    check("mp3 输出丢弃画面有提示", any("画面" in w for w in job_mp3.warnings), str(job_mp3.warnings))

    # --- 5c. 视频与纯音频混排：构建期直接报错，不再丢给 ffmpeg 失败 ---
    info_audio = make_info(src_b, v_codec="", v_width=0, v_height=0)
    mixed_stub = StubProber({src_a: info_a, src_b: info_audio})
    try:
        CommandBuilder("ffmpeg", mixed_stub).build(
            OperationSpec(op=OP_MERGE, inputs=[src_a, src_b],
                          output=os.path.join(out_dir, "mixed.mp4")))
        check("混合媒体合并构建期报错", False)
    except BuildError as exc:
        check("混合媒体合并构建期报错", "同时存在" in str(exc), str(exc))

    # --- 6. 提取音频 ---
    job_m4a = builder.build(OperationSpec(op=OP_EXTRACT_AUDIO, inputs=[src],
                                          output=os.path.join(out_dir, "a.m4a"), info=info))
    argv = job_m4a.steps[0].argv
    check("m4a + aac 源 → 无损复制", "-c:a" in argv and argv[argv.index("-c:a") + 1] == "copy")
    check("提取音频带 -vn", "-vn" in argv)
    check("m4a 显式指定 muxer ipod", argv[argv.index("-f") + 1] == "ipod")

    mp3_src = os.path.join(out_dir, "mp3audio.m4a")
    mp3_info = make_info(mp3_src, a_codec="mp3", v_codec="", v_width=0, v_height=0)
    mp3_stub = StubProber({mp3_src: mp3_info})
    job_conv = CommandBuilder("ffmpeg", mp3_stub).build(
        OperationSpec(op=OP_EXTRACT_AUDIO, inputs=[mp3_src],
                      output=os.path.join(out_dir, "x.m4a"), info=mp3_info)
    )
    check("m4a + 非 aac 源 → 自动重编码 AAC",
          "aac" in job_conv.steps[0].argv and bool(job_conv.warnings), str(job_conv.warnings))

    # --- 7. 去音频 ---
    job_strip = builder.build(OperationSpec(op=OP_STRIP_AUDIO, inputs=[src],
                                            output=os.path.join(out_dir, "s.mp4"), info=info))
    argv = job_strip.steps[0].argv
    check("去音频 -an + 视频无损复制", "-an" in argv and "copy" in argv)

    # --- 8. 转换 ---
    job_conv2 = builder.build(OperationSpec(
        op=OP_CONVERT, inputs=[src], output=os.path.join(out_dir, "c.mp4"),
        params={"vcodec": "copy", "acodec": "aac", "width": 1280, "height": 720}, info=info))
    check("有缩放时无损复制被自动取消",
          "libx264" in job_conv2.steps[0].argv and bool(job_conv2.warnings), str(job_conv2.warnings))

    job_audio = builder.build(OperationSpec(
        op=OP_CONVERT, inputs=[src], output=os.path.join(out_dir, "c.mp3"),
        params={"acodec": "mp3", "abitrate_k": 192}, info=info))
    argv = job_audio.steps[0].argv
    check("音频容器不生成视频流", "-vn" in argv and "libmp3lame" in argv)

    # 图片/无音轨源 → 纯音频格式：构建期前置报错，而不是等 ffmpeg 失败
    img_info = make_info(src, a_codec="", a_channels=0, a_sample_rate=0, a_bit_rate=0)
    try:
        builder.build(OperationSpec(
            op=OP_CONVERT, inputs=[src], output=os.path.join(out_dir, "img.mp3"),
            params={"acodec": "mp3", "abitrate_k": 192}, info=img_info))
        check("无音轨转纯音频构建期报错", False)
    except BuildError as exc:
        check("无音轨转纯音频构建期报错", "没有音轨" in str(exc), str(exc))

    # --- 9. 压缩 ---
    job_high = builder.build(OperationSpec(
        op=OP_COMPRESS, inputs=[src], output=os.path.join(out_dir, "hi.mp4"),
        params={"target": "video", "vcodec": "x264", "level": "high"}, info=info))
    check("x264 高档 CRF=20", "20" in job_high.steps[0].argv, joined(job_high.steps[0].argv))
    job_x265 = builder.build(OperationSpec(
        op=OP_COMPRESS, inputs=[src], output=os.path.join(out_dir, "lo.mp4"),
        params={"target": "video", "vcodec": "x265", "level": "low"}, info=info))
    check("x265 低档 CRF=32", "32" in job_x265.steps[0].argv, joined(job_x265.steps[0].argv))
    job_audio_comp = builder.build(OperationSpec(
        op=OP_COMPRESS, inputs=[src], output=os.path.join(out_dir, "ac.mp3"),
        params={"target": "audio", "acodec": "mp3", "abitrate_k": 96}, info=info))
    check("音频压缩用 -b:a",
          "-b:a" in job_audio_comp.steps[0].argv and "96k" in job_audio_comp.steps[0].argv)

    low_rate = make_info(os.path.join(out_dir, "low.mp4"), bit_rate=600_000, v_bit_rate=580_000)
    low_stub = StubProber({low_rate.path: low_rate})
    job_low = CommandBuilder("ffmpeg", low_stub).build(OperationSpec(
        op=OP_COMPRESS, inputs=[low_rate.path], output=os.path.join(out_dir, "low_out.mp4"),
        params={"target": "video", "vcodec": "x264", "level": "mid"}, info=low_rate))
    check("低码率源给出不建议压缩提示", any("不建议" in w or "收益" in w for w in job_low.warnings),
          str(job_low.warnings))

    job_target = builder.build(OperationSpec(
        op=OP_COMPRESS, inputs=[src], output=os.path.join(out_dir, "size.mp4"),
        params={"target": "video", "vcodec": "x264", "level": "mid", "target_mb": 30}, info=info))
    check("目标体积走两遍编码", len(job_target.steps) == 2, str(len(job_target.steps)))
    check("第一遍不进文件", "-pass" in job_target.steps[0].argv and "null" in joined(job_target.steps[0].argv))
    check("两遍编码带 -passlogfile", "-passlogfile" in joined(job_target.steps[1].argv))
    check("两遍编码不会同时出现 -crf", "-crf" not in job_target.steps[0].argv)

    # --- 10. 提取帧 ---
    job_frame = builder.build(OperationSpec(op=OP_EXTRACT_FRAME, inputs=[src],
                                            output=os.path.join(out_dir, "f.jpg"),
                                            params={"time": 12.5, "quality": 2}, info=info))
    argv = job_frame.steps[0].argv
    check("提取帧 -frames:v 1 + -q:v 2",
          "-frames:v" in argv and argv[argv.index("-frames:v") + 1] == "1"
          and argv[argv.index("-q:v") + 1] == "2")
    check("jpg 输出显式 mjpeg", argv[argv.index("-f") + 1] == "mjpeg")

    job_frame_late = builder.build(OperationSpec(op=OP_EXTRACT_FRAME, inputs=[src],
                                                 output=os.path.join(out_dir, "f2.jpg"),
                                                 params={"time": 150.0, "quality": 2}, info=info))
    check("提取帧超出时长截断有说明",
          any("结尾前一帧" in n for n in job_frame_late.notes), str(job_frame_late.notes))
    late_argv = job_frame_late.steps[0].argv
    check("截断后时间点落在时长内", late_argv[late_argv.index("-ss") + 1] == "119.95",
          late_argv[late_argv.index("-ss") + 1])

    # --- 11. 通用约定 ---
    for name, built in [("剪辑", job), ("合并", job_merge), ("压缩", job_high), ("提取", job_audio)]:
        argv = built.steps[0].argv
        check(f"{name}命令带 -y", "-y" in argv)
        check(f"{name}命令带 -nostdin", "-nostdin" in argv)
        check(f"{name}命令带 -progress pipe:1", "-progress" in argv and "pipe:1" in argv)
        check(f"{name}命令首元素是 ffmpeg", argv[0] == "ffmpeg")

    # --- 12. 手动编辑降级 ---
    edited = OperationSpec(op=OP_TRIM, inputs=[src], output=os.path.join(out_dir, "e.mp4"),
                           params={}, info=info, argv_override=["ffmpeg", "-i", src, "out.mp4"])
    job_edited = builder.build(edited)
    check("编辑后的命令原样执行", job_edited.steps[0].argv == edited.argv_override)
    check("编辑后进度降级", job_edited.steps[0].progress is False)
    check("编辑后给出降级说明", bool(job_edited.warnings) and job_edited.editing_degraded)


def test_validator(info: MediaInfo) -> None:
    section("校验层（需求 5.6 / 十二.6）")
    validator = Validator("", "", overwrite_policy="rename")
    issues = validator.check_environment()
    check("ffmpeg 缺失 → 全局错误",
          any(i.severity is Severity.GLOBAL for i in issues), str([i.message for i in issues]))

    with tempfile.TemporaryDirectory() as tmp:
        fake_ffmpeg = os.path.join(tmp, "ffmpeg.exe")
        fake_probe = os.path.join(tmp, "ffprobe.exe")
        for path in (fake_ffmpeg, fake_probe):
            open(path, "wb").close()
        validator.set_paths(fake_ffmpeg, fake_probe)
        check("工具存在时不报全局错误", not validator.check_environment())

        missing = OperationSpec(op=OP_TRIM, inputs=[os.path.join(tmp, "不存在.mp4")],
                                output=os.path.join(tmp, "o.mp4"),
                                params={"mode": "copy", "start": 0, "end": 5}, info=info)
        report = validator.validate_spec(missing)
        check("输入缺失 → 单文件错误",
              any(i.severity is Severity.FILE for i in report.issues), str([i.message for i in report.issues]))
        check("输入缺失不算全局错误", not report.has_global)

        existing = os.path.join(tmp, "exists.mp4")
        open(existing, "wb").close()
        src = os.path.join(tmp, "src.mp4")
        open(src, "wb").close()
        report2 = validator.validate_spec(OperationSpec(
            op=OP_TRIM, inputs=[src], output=existing,
            params={"mode": "copy", "start": 0, "end": 5}, info=info))
        check("输出冲突自动改名", report2.resolved_output is not None
              and report2.resolved_output.endswith("_1.mp4"), str(report2.resolved_output))
        check("输出冲突只是提醒，不阻断", report2.has_warning and not report2.has_file_error)

        bad_dir = os.path.join(tmp, "no_such_dir")
        report3 = validator.validate_spec(OperationSpec(
            op=OP_TRIM, inputs=[src], output=os.path.join(bad_dir, "o.mp4"),
            params={"mode": "copy", "start": 0, "end": 5}, info=info))
        check("输出目录不可写 → 全局错误", report3.has_global or report3.has_file_error,
              str([i.message for i in report3.issues]))

        report4 = validator.validate_spec(OperationSpec(
            op=OP_TRIM, inputs=[src], output=os.path.join(tmp, "t2.mp4"),
            params={"mode": "copy", "start": 50, "end": 20}, info=info))
        check("结束≤开始 → 报错",
              any(i.severity is Severity.FILE for i in report4.issues), str([i.message for i in report4.issues]))

        report5 = validator.validate_spec(OperationSpec(
            op=OP_TRIM, inputs=[src], output=os.path.join(tmp, "t3.mp4"),
            params={"mode": "copy", "start": 0, "end": 9999}, info=info))
        check("时间越界 → 提醒但不阻断",
              any(i.severity is Severity.WARN for i in report5.issues) and not report5.has_file_error)

        merged = validator.preflight([
            OperationSpec(op=OP_TRIM, inputs=[src], output=os.path.join(tmp, "ok.mp4"),
                          params={"mode": "copy", "start": 0, "end": 5}, info=info, label="正常.mp4"),
            OperationSpec(op=OP_TRIM, inputs=[os.path.join(tmp, "坏.mp4")], output=os.path.join(tmp, "bad.mp4"),
                          params={"mode": "copy", "start": 0, "end": 5}, info=info, label="坏.mp4"),
        ])
        check("批量预检能区分两类问题",
              merged.has_file_error and not merged.has_global, str([i.message for i in merged.issues]))


def test_proc_decode() -> None:
    section("中文系统编码解码（需求 5.2）")
    from app.core.proc_util import decode, stderr_tail

    gbk_bytes = "无法打开输出文件：拒绝访问".encode("gbk")
    text = decode(gbk_bytes, "gbk")
    check("GBK 输出不解码异常", "拒绝访问" in text, text)
    check("未知编码不再抛异常", isinstance(decode(gbk_bytes, "utf-8"), str))
    tail = stderr_tail("\n".join(f"line{i}" for i in range(100)), lines=5)
    check("stderr 尾部只取 5 行", tail.count("\n") == 4, tail)


def test_global_error_keywords() -> None:
    section("全局错误关键词判定（需求 5.6）")
    from app.core.validator import is_global_error

    check("无空间 → 全局", is_global_error("Conversion failed", "No space left on device"))
    check("拒绝访问 → 全局", is_global_error("失败", "Permission denied"))
    check("编码失败 → 单文件", not is_global_error("处理失败", "Invalid data found when processing input"))
    check("中文拒绝访问 → 全局", is_global_error("", "拒绝访问"))


def test_round2_fixes() -> None:
    """第二轮实测反馈的回归：容器联动 / 参数不泄漏 / .tmp 不暴露 / 封面 / 元数据。"""
    section("实测反馈修复（问题 1/2/11 + 新增 1/2）")
    from app.core.paths import audio_codec_choices, default_audio_codec
    from app.core.spec import OP_EDIT_METADATA, OP_SET_COVER

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "sample.mp4")
        open(src, "wb").close()
        cover = os.path.join(tmp, "cover.jpg")
        open(cover, "wb").close()
        info = make_info(src)
        builder = CommandBuilder("ffmpeg", StubProber({src: info}))

        # --- 容器 ↔ 音频编码联动表 ---
        check("MP4 音频选项只有 AAC",
              [v for v, _ in audio_codec_choices("mp4")] == ["aac", "copy", "none"],
              str(audio_codec_choices("mp4")))
        check("WAV 只接受 PCM", [v for v, _ in audio_codec_choices("wav")] == ["wav"])
        check("MKV 允许多种编码",
              {"aac", "mp3", "opus", "flac"} <= {v for v, _ in audio_codec_choices("matroska")})
        check("默认音频编码：mp4→aac", default_audio_codec("mp4") == "aac")
        check("默认音频编码：m4a→aac", default_audio_codec("m4a") == "aac")

        # --- 问题 1：音频输出不带任何视频参数 ---
        audio_job = builder.build(OperationSpec(
            op=OP_CONVERT, inputs=[src], output=os.path.join(tmp, "out.wav"),
            params={"container": ".wav", "vcodec": "none", "acodec": "aac",
                    "width": 1280, "height": 720, "fps": 30, "crf": 20,
                    "vbitrate_k": 4000, "rotation": "cw"},
            info=info))
        argv = audio_job.steps[0].argv
        check("音频输出带 -vn", "-vn" in argv)
        check("音频输出不含 -c:v/-vf/-crf",
              "-c:v" not in argv and "-vf" not in argv and "-crf" not in argv, joined(argv))
        check("WAV 容器里的 AAC 被改成 PCM",
              argv[argv.index("-c:a") + 1] == "pcm_s16le", joined(argv))
        check("不兼容组合给出说明", any("WAV" in w or "PCM" in w for w in audio_job.warnings),
              str(audio_job.warnings))

        # --- 问题 1：m4a 里 AAC 源仍无损复制 ---
        m4a_job = builder.build(OperationSpec(
            op=OP_CONVERT, inputs=[src], output=os.path.join(tmp, "out.m4a"),
            params={"container": ".m4a", "acodec": "copy"}, info=info))
        check("m4a + AAC 源 → 无损复制",
              m4a_job.steps[0].argv[m4a_job.steps[0].argv.index("-c:a") + 1] == "copy")

        # --- 问题 2：MP4 容器里不放 MP3 ---
        flac_src = os.path.join(tmp, "flac_audio.mkv")
        open(flac_src, "wb").close()
        flac_info = make_info(flac_src, a_codec="flac")
        builder2 = CommandBuilder("ffmpeg", StubProber({flac_src: flac_info}))

        mp4_job = builder.build(OperationSpec(
            op=OP_COMPRESS, inputs=[src], output=os.path.join(tmp, "c.mp4"),
            params={"target": "video", "vcodec": "x264", "level": "mid", "acodec": "mp3"},
            info=info))
        mp4_audio = mp4_job.steps[0].argv[mp4_job.steps[0].argv.index("-c:a") + 1]
        check("MP4 + 源码 AAC：不转 MP3（无损复制）", mp4_audio == "copy", mp4_audio)

        mp4_flac = builder2.build(OperationSpec(
            op=OP_COMPRESS, inputs=[flac_src], output=os.path.join(tmp, "c2.mp4"),
            params={"target": "video", "vcodec": "x264", "level": "mid", "acodec": "mp3"},
            info=flac_info))
        mp4_audio2 = mp4_flac.steps[0].argv[mp4_flac.steps[0].argv.index("-c:a") + 1]
        check("MP4 容器拒绝 MP3（自动改 AAC）", mp4_audio2 == "aac", mp4_audio2)
        check("容器联动会给出说明",
              any("AAC" in w or "不适配" in w for w in mp4_flac.warnings), str(mp4_flac.warnings))

        mkv_job = builder2.build(OperationSpec(
            op=OP_COMPRESS, inputs=[flac_src], output=os.path.join(tmp, "c3.mkv"),
            params={"target": "video", "vcodec": "x264", "level": "mid", "acodec": "mp3"},
            info=flac_info))
        check("MKV 容器接受 MP3",
              mkv_job.steps[0].argv[mkv_job.steps[0].argv.index("-c:a") + 1] == "libmp3lame",
              joined(mkv_job.steps[0].argv))

        # --- 问题 11：命令预览不暴露 .tmp ---
        preview = mp4_job.preview()
        check("命令预览不含 .tmp", ".tmp" not in preview, preview[:120])
        check("命令预览显示最终输出名", "c.mp4" in preview, preview[:120])
        check("内部 argv 仍然写临时文件", mp4_job.steps[0].argv[-1].endswith(".mp4.tmp"),
              mp4_job.steps[0].argv[-1])

        # --- 问题 12：说明文案随实际路径动态生成 ---
        copy_job = builder.build(OperationSpec(
            op=OP_CONVERT, inputs=[src], output=os.path.join(tmp, "copy.mp4"),
            params={"vcodec": "copy", "acodec": "copy"}, info=info))
        check("无损复制的说明随参数变化",
              any("无损" in n for n in copy_job.notes) and copy_job.notes != mp4_job.notes,
              str(copy_job.notes))

        # --- 新增 1：封面 ---
        cover_job = builder.build(OperationSpec(
            op=OP_SET_COVER, inputs=[src], output=os.path.join(tmp, "with_cover.mp4"),
            params={"cover": cover}, info=info))
        argv = cover_job.steps[0].argv
        check("封面：第二路输入是图片", "../" not in argv and cover in argv)
        check("封面：attached_pic 标记", "attached_pic" in argv)
        check("封面：音频/视频流无损复制", "copy" in argv)

        mp3_cover = builder.build(OperationSpec(
            op=OP_SET_COVER, inputs=[src], output=os.path.join(tmp, "with_cover.mp3"),
            params={"cover": cover}, info=info))
        argv = mp3_cover.steps[0].argv
        check("mp3 封面走 ID3v2", "-id3v2_version" in argv and "3" in argv)
        check("mp3 封面标注 Album cover", any("Album cover" in t for t in argv))

        remove_job = builder.build(OperationSpec(
            op=OP_SET_COVER, inputs=[src], output=os.path.join(tmp, "no_cover.mp4"),
            params={"remove": True}, info=info))
        check("无封面信息时仅重新封装", "-0:v" not in remove_job.steps[0].argv,
              joined(remove_job.steps[0].argv))

        # mp4：封面是第 2 条视频流（全局索引 1）→ 按全局索引反选
        mp4_info = make_info(src)
        mp4_info.streams = [
            {"codec_type": "video", "codec_name": "h264", "disposition": {}},
            {"codec_type": "video", "codec_name": "png", "disposition": {"attached_pic": 1}},
        ]
        remove_mp4 = builder.build(OperationSpec(
            op=OP_SET_COVER, inputs=[src], output=os.path.join(tmp, "no_cover2.mp4"),
            params={"remove": True}, info=mp4_info))
        argv = remove_mp4.steps[0].argv
        check("mp4 移除封面按全局索引反选", "-map" in argv and "-0:1" in argv,
              joined(argv))

        # mp3：封面是唯一视频流（全局索引 1，但 v:0）→ 旧逻辑 -0:v:1 会失效
        mp3_info = make_info(src, v_codec="", v_width=0, v_height=0)
        mp3_info.streams = [
            {"codec_type": "audio", "codec_name": "mp3", "disposition": {}},
            {"codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
        ]
        remove_mp3 = builder.build(OperationSpec(
            op=OP_SET_COVER, inputs=[src], output=os.path.join(tmp, "no_cover3.mp3"),
            params={"remove": True}, info=mp3_info))
        argv = remove_mp3.steps[0].argv
        check("mp3 移除封面不再依赖 v:1", "-0:1" in argv and "-0:v:1" not in argv,
              joined(argv))

        try:
            builder.build(OperationSpec(
                op=OP_SET_COVER, inputs=[src], output=os.path.join(tmp, "x.mp4"),
                params={}, info=info))
            check("缺少封面图时报错", False)
        except BuildError as exc:
            check("缺少封面图时报错", "封面" in str(exc), str(exc))

        # --- 新增 2：元数据 ---
        meta_job = builder.build(OperationSpec(
            op=OP_EDIT_METADATA, inputs=[src], output=os.path.join(tmp, "tagged.mp4"),
            params={"tags": {"title": "测试标题", "artist": "张三"}, "clear": ["comment"]},
            info=info))
        argv = meta_job.steps[0].argv
        rendered = joined(argv)
        check("元数据 title 写入", "title=测试标题" in rendered, rendered)
        check("元数据 artist 写入", "artist=张三" in rendered)
        check("元数据支持清除字段", "comment=" in rendered)
        check("元数据保留原有其它字段", "-map_metadata" in argv)
        check("元数据不重编码", "copy" in argv)


def test_mux_builder() -> None:
    """多轨封装（新增 3）：轨道选择、外部音轨/字幕、容器适配转码。"""
    from app.core.spec import OP_MUX

    with tempfile.TemporaryDirectory() as tmp:
        main = os.path.join(tmp, "电影.mkv")
        flac = os.path.join(tmp, "评论音轨.flac")
        srt = os.path.join(tmp, "简中.srt")
        for path in (main, flac, srt):
            open(path, "wb").close()

        # 主文件：视频 + 双音轨（chi/aac 默认、eng/flac）+ 一条内嵌字幕
        info = make_info(main)
        info.streams = [
            {"index": 0, "codec_type": "video", "codec_name": "h264",
             "width": 1920, "height": 1080, "disposition": {}},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2,
             "sample_rate": 48000, "disposition": {"default": 1},
             "tags": {"language": "chi", "title": "原声"}},
            {"index": 2, "codec_type": "audio", "codec_name": "flac",
             "disposition": {}, "tags": {"language": "eng"}},
            {"index": 3, "codec_type": "subtitle", "codec_name": "subrip",
             "disposition": {}, "tags": {"language": "chi"}},
        ]

        builder = CommandBuilder("ffmpeg", prober=StubProber({}))

        def ext_info(path: str, **kwargs) -> MediaInfo:
            return MediaInfo(path=path, ok=True, **kwargs)

        # ---- 场景 A：MKV，丢掉 flac 内嵌轨 + 挂外部音轨和字幕 ----
        builder._info = lambda path: ext_info(path) if path == flac else None  # type: ignore[method-assign]
        spec = OperationSpec(
            op=OP_MUX, inputs=[main], output=os.path.join(tmp, "out.mkv"),
            params={
                "container": ".mkv",
                "keep_audio": [0],          # 只保留 chi/aac
                "keep_subs": [0],
                "extra_audio": [{"path": flac, "language": "eng", "title": "评论",
                                 "default": True}],
                "extra_subs": [{"path": srt, "language": "chi", "title": "简中",
                                "default": True, "forced": False}],
            },
            info=info,
        )
        job = builder.build(spec)
        argv = job.steps[0].argv
        check("多轨：视频流用 0:V:0（自动跳过封面）", "-map 0:V:0" in " ".join(argv))
        check("多轨：保留指定内嵌音轨", "-map 0:a:0" in " ".join(argv))
        check("多轨：外部音轨作为独立输入映射", "-map 1:a:0" in " ".join(argv))
        check("多轨：外部字幕作为独立输入映射", "-map 2:s:0" in " ".join(argv))
        check("多轨：视频无损复制", "-c:v copy" in " ".join(argv))
        check("多轨：兼容音轨原样 copy", "-c:a:0 copy" in " ".join(argv))
        check("多轨：MKV 收 srt 无需转码", "-c:s:0 copy" in " ".join(argv))
        check("多轨：用户勾选的默认音轨生效", "-disposition:a:1 default" in " ".join(argv))
        check("多轨：非默认音轨显式清零", "-disposition:a:0 0" in " ".join(argv))
        check("多轨：字幕默认标记", "-disposition:s:1 default" in " ".join(argv))
        check("多轨：写语言标签", "-metadata:s:a:1 language=eng" in " ".join(argv))
        check("多轨：写轨道标题", "title=评论" in " ".join(argv))
        check("多轨：显式 -f matroska", "-f matroska" in " ".join(argv))
        check("多轨：无转码警告", not job.warnings, str(job.warnings))

        # ---- 场景 B：MP4，flac 音轨必须转 AAC、srt 必须转 mov_text ----
        def mux_info(path: str) -> MediaInfo:
            if path == flac:
                return ext_info(path, a_codec="flac", v_codec="")
            if path == srt:
                payload = ext_info(path, v_codec="", a_codec="")
                payload.streams = [{"codec_type": "subtitle", "codec_name": "subrip"}]
                return payload
            return info

        builder._info = mux_info  # type: ignore[method-assign]
        spec_mp4 = OperationSpec(
            op=OP_MUX, inputs=[main], output=os.path.join(tmp, "out.mp4"),
            params={"container": ".mp4", "keep_audio": [0], "keep_subs": [0],
                    "extra_audio": [{"path": flac, "language": "eng", "default": True}],
                    "extra_subs": [{"path": srt, "language": "chi"}]},
            info=info,
        )
        job_mp4 = builder.build(spec_mp4)
        argv_mp4 = job_mp4.steps[0].argv
        check("多轨：MP4 下 flac 自动转 AAC", "-c:a:1 aac" in " ".join(argv_mp4))
        check("多轨：MP4 下 srt 自动转 mov_text", "-c:s:0 mov_text" in " ".join(argv_mp4))
        check("多轨：MP4 下兼容的 aac 仍 copy", "-c:a:0 copy" in " ".join(argv_mp4))
        check("多轨：转码有警告提示", bool(job_mp4.warnings), str(job_mp4.warnings))

        # ---- 场景 C：纯音频主文件且不保留任何轨道 → 构建期报错 ----
        audio_only = make_info(os.path.join(tmp, "song.m4a"), v_codec="", v_width=0, v_height=0)
        audio_only.streams = [
            {"index": 0, "codec_type": "audio", "codec_name": "aac", "disposition": {}},
        ]
        try:
            builder.build(OperationSpec(
                op=OP_MUX, inputs=[audio_only.path], output=os.path.join(tmp, "x.mkv"),
                params={"container": ".mkv", "keep_audio": [], "keep_subs": []},
                info=audio_only,
            ))
            check("多轨：没有任何轨道时报错", False)
        except BuildError:
            check("多轨：没有任何轨道时报错", True)


# ---------------------------------------------------------------------------
def main() -> int:
    print("=" * 60)
    print("FFKit · 核心层自检")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "示例 视频'main'.mp4")
        open(src, "wb").close()
        info = make_info(src)
        stub = StubProber({src: info})

        test_timeparse()
        test_paths()
        test_builder(info, stub)
        test_round2_fixes()
        test_mux_builder()
        test_validator(info)
        test_proc_decode()
        test_global_error_keywords()

    print("\n" + "=" * 60)
    if FAILED:
        print(f"结果：{PASSED} 项通过，{len(FAILED)} 项失败")
        for item in FAILED:
            print(f"  ✖ {item}")
        return 1
    print(f"结果：{PASSED} 项全部通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
