"""真实媒体全链路测试：用本机 ffmpeg 生成测试视频，走完整管线执行。

用法：python realtest.py <ffmpeg目录>
      或设置环境变量 FFMPEG_DIR 指向 ffmpeg 所在目录。
覆盖场景：mkv->mp4 转换（含音轨转码）、无损切割、精确切割、智能切割、
批量 + 单文件失败跳过、中间产物清理。
"""

import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QCoreApplication

from app.core.builder import CommandBuilder
from app.core.paths import drop_temp_files
from app.core.prober import Prober, list_tracks
from app.core.queue import TaskQueue
from app.core.spec import (
    OP_CONVERT,
    OP_EDIT_METADATA,
    OP_MERGE,
    OP_MUX,
    OP_SET_COVER,
    OP_TRIM,
    OperationSpec,
)
from app.core.validator import Validator

FFDIR = sys.argv[1] if len(sys.argv) > 1 else os.environ.get("FFMPEG_DIR", "")
FFMPEG = os.path.join(FFDIR, "ffmpeg.exe")
FFPROBE = os.path.join(FFDIR, "ffprobe.exe")

passed: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    passed.append(ok)
    mark = "✓" if ok else "✖"
    print(f"{mark} {name}" + (f"  [{detail}]" if detail and not ok else ""))


def make_samples(workdir: str) -> dict[str, str]:
    """用 ffmpeg 生成测试视频：mkv(h264+aac, 4s) 和 mp4(h264+aac, 3s)。"""

    def gen(args: list[str]) -> None:
        subprocess.run([FFMPEG, "-y", "-nostdin", "-loglevel", "error"] + args, check=True)

    a = os.path.join(workdir, "sample_a.mkv")
    gen([
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=4",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "96k",
        "-f", "matroska", a,
    ])
    b = os.path.join(workdir, "sample_b.mp4")
    gen([
        "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        b,
    ])
    return {"mkv": a, "mp4": b}


def main() -> int:
    app = QCoreApplication(sys.argv)
    prober = Prober(FFPROBE)
    builder = CommandBuilder(FFMPEG, prober=prober)
    validator = Validator(FFMPEG, FFPROBE)
    queue = TaskQueue(validator)

    workdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".realtest_tmp")
    outdir = os.path.join(workdir, "out")
    shutil.rmtree(workdir, ignore_errors=True)
    os.makedirs(outdir)
    samples = make_samples(workdir)

    info = prober.get(samples["mkv"])
    check("ffprobe 能解析生成的 mkv", bool(info.ok and info.duration and 3.5 < info.duration < 4.5),
          f"ok={info.ok} dur={info.duration}")

    results: list = []
    queue.queueFinished.connect(lambda s: (results.append(s), app.quit()))

    def run(specs: list[OperationSpec]):
        jobs = [builder.build(sp) for sp in specs]
        results.clear()
        queue.start(jobs)
        app.exec()
        return results[0] if results else None

    def check_common(summary, name: str, out_file: str) -> bool:
        ok = summary is not None and summary.fail_count == 0
        detail = str(summary.failures[0].message) if summary and summary.failures else (
            "no summary" if summary is None else "")
        if ok and not os.path.isfile(out_file):
            ok, detail = False, "输出文件不存在"
        elif ok:
            ok, detail = True, ""
        check(name, ok, detail)
        return ok

    # ---------- 场景 1：mkv -> mp4（视频保留原样 + AAC 音频转码）----------
    out1 = os.path.join(outdir, "conv.mp4")
    s = run([OperationSpec(
        op=OP_CONVERT, inputs=[samples["mkv"]], output=out1,
        params={"vcodec": "copy", "acodec": "aac", "abitrate_k": 128},
        label="sample_a.mkv", info=info)])
    check_common(s, "转换执行成功（无失败）", out1)
    if os.path.isfile(out1):
        info2 = prober.get(out1)
        check("输出为 mp4 且含音视频流", bool(info2.ok and info2.v_codec and info2.a_codec),
              f"v={info2.v_codec} a={info2.a_codec}")

    # ---------- 场景 2：无损切割（copy）----------
    out2 = os.path.join(outdir, "trim_copy.mp4")
    s = run([OperationSpec(
        op=OP_TRIM, inputs=[samples["mkv"]], output=out2,
        params={"mode": "copy", "start": 1.0, "end": 3.0, "vcodec": "copy"},
        label="sample_a.mkv", info=info)])
    check_common(s, "无损切割成功", out2)
    if os.path.isfile(out2):
        info3 = prober.get(out2)
        # 无损切割只能对齐关键帧：起点向前扩到最近关键帧，时长 >= 请求值属正常
        dur = info3.duration or 0
        check("无损切割时长 >= 请求区间且不越界", bool(info3.ok and 2.0 <= dur <= 4.5),
              f"dur={dur}")

    # ---------- 场景 3：精确切割（重编码）----------
    out3 = os.path.join(outdir, "trim_exact.mp4")
    s = run([OperationSpec(
        op=OP_TRIM, inputs=[samples["mkv"]], output=out3,
        params={"mode": "exact", "start": 0.5, "end": 2.5, "vcodec": "libx264",
                "crf": 23, "preset": "ultrafast"},
        label="sample_a.mkv", info=info)])
    check_common(s, "精确切割成功", out3)

    # ---------- 场景 4：智能切割（3 段 ts + concat，验证中间产物清理）----------
    out4 = os.path.join(outdir, "trim_smart.mp4")
    s = run([OperationSpec(
        op=OP_TRIM, inputs=[samples["mkv"]], output=out4,
        params={"mode": "smart", "start": 0.5, "end": 3.5, "vcodec": "copy"},
        label="sample_a.mkv", info=info)])
    check_common(s, "智能切割成功", out4)
    leftovers = [f for f in os.listdir(outdir) if f.endswith(".ts") or ".tmp" in f]
    check("智能切割中间产物已清理", not leftovers, str(leftovers))
    if os.path.isfile(out4):
        info5 = prober.get(out4)
        check("智能切割时长约 3 秒", bool(info5.ok and 2.5 < (info5.duration or 0) < 3.6),
              f"dur={info5.duration}")

    # ---------- 场景 5：批量 + 单文件失败跳过 ----------
    bad = os.path.join(workdir, "不存在.mp4")
    s = run([
        OperationSpec(op=OP_CONVERT, inputs=[samples["mp4"]],
                      output=os.path.join(outdir, "batch_ok.mkv"),
                      params={"vcodec": "copy", "acodec": "copy"},
                      label="sample_b.mp4", info=prober.get(samples["mp4"])),
        OperationSpec(op=OP_CONVERT, inputs=[bad],
                      output=os.path.join(outdir, "batch_bad.mkv"),
                      params={"vcodec": "copy", "acodec": "copy"},
                      label="缺失文件", info=None),
    ])
    check("批量：好文件成功", os.path.isfile(os.path.join(outdir, "batch_ok.mkv")))
    check("批量：坏文件被记录为失败", s is not None and s.fail_count == 1,
          f"fail_count={s.fail_count if s else 'NA'}")
    check("批量：坏文件未中断队列（好文件仍执行）",
          s is not None and s.ok_count == 1 and not s.aborted)

    # ---------- 场景 6：HEVC 10bit mkv -> mp4（用户实际遇到的高清片源场景）----------
    hevc = os.path.join(workdir, "sample_hevc.mkv")
    gen_ok = True
    try:
        subprocess.run(
            [FFMPEG, "-y", "-nostdin", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=2",
             "-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
             "-f", "matroska", hevc],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        gen_ok = False
        print("- 跳过 HEVC 场景（本机 ffmpeg 无 libx265）")
    if gen_ok:
        out6 = os.path.join(outdir, "hevc_conv.mp4")
        s = run([OperationSpec(
            op=OP_CONVERT, inputs=[hevc], output=out6,
            params={"vcodec": "copy", "acodec": "aac", "abitrate_k": 128},
            label="sample_hevc.mkv", info=prober.get(hevc))])
        check_common(s, "HEVC mkv -> mp4（视频流原样封装）", out6)

    # ---------- 场景 7：封面写入（真实执行 + ffprobe 验证）----------
    cover = os.path.join(workdir, "cover.jpg")
    subprocess.run(
        [FFMPEG, "-y", "-nostdin", "-loglevel", "error",
         "-f", "lavfi", "-i", "color=c=red:size=240x240:duration=1",
         "-frames:v", "1", cover],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out7 = os.path.join(outdir, "with_cover.mp4")
    s = run([OperationSpec(
        op=OP_SET_COVER, inputs=[samples["mkv"]], output=out7,
        params={"cover": cover}, label="sample_a.mkv", info=info)])
    check_common(s, "封面写入成功", out7)
    if os.path.isfile(out7):
        info7 = prober.get(out7)
        check("输出仍有音视频流",
              bool(info7.ok and info7.v_codec and info7.a_codec),
              f"v={info7.v_codec} a={info7.a_codec}")
        check("检测到附加封面图", bool(info7.has_cover), f"attached={info7.attached_pic_count}")

    out7b = os.path.join(outdir, "no_cover.mp4")
    s = run([OperationSpec(
        op=OP_SET_COVER, inputs=[out7], output=out7b,
        params={"remove": True}, label="with_cover.mp4", info=prober.get(out7))])
    check_common(s, "移除封面成功", out7b)
    if os.path.isfile(out7b):
        check("移除后不再有封面图", not prober.get(out7b).has_cover)

    # ---------- 场景 8：元数据写入（真实执行 + ffprobe 读回）----------
    out8 = os.path.join(outdir, "tagged.mkv")
    s = run([OperationSpec(
        op=OP_EDIT_METADATA, inputs=[samples["mkv"]], output=out8,
        params={"tags": {"title": "测试标题", "artist": "张三"}, "clear": []},
        label="sample_a.mkv", info=info)])
    check_common(s, "元数据写入成功", out8)
    if os.path.isfile(out8):
        tags = {k.lower(): v for k, v in (prober.get(out8).tags or {}).items()}
        check("回读 title 一致", tags.get("title") == "测试标题", str(tags))
        check("回读 artist 一致", tags.get("artist") == "张三", str(tags))

    # ---------- 场景 9：mkv → mp3（验证视频参数不进入音频命令）----------
    out9 = os.path.join(outdir, "audio_only.mp3")
    s = run([OperationSpec(
        op=OP_CONVERT, inputs=[samples["mkv"]], output=out9,
        params={"container": ".mp3", "vcodec": "none", "acodec": "mp3",
                "width": 1280, "height": 720, "fps": 30, "crf": 18,
                "vbitrate_k": 5000, "rotation": "cw", "abitrate_k": 192,
                "volume": 1.0},
        label="sample_a.mkv", info=info)])
    check_common(s, "视频转纯音频（MP3）成功", out9)
    if os.path.isfile(out9):
        info9 = prober.get(out9)
        check("输出只有音轨、没有视频流",
              bool(info9.ok and info9.a_codec and not info9.v_codec),
              f"a={info9.a_codec} v={info9.v_codec}")

    # ---------- 场景 10：多轨封装（视频 + 外部音轨 + 外部字幕，新增 3）----------
    ext_audio = os.path.join(workdir, "extra_audio.m4a")
    subprocess.run(
        [FFMPEG, "-y", "-nostdin", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=660:duration=4",
         "-c:a", "aac", "-b:a", "96k", ext_audio],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ext_sub = os.path.join(workdir, "简中.srt")
    with open(ext_sub, "w", encoding="utf-8") as fh:
        fh.write("1\n00:00:00,000 --> 00:00:02,000\n测试字幕第一行\n\n"
                 "2\n00:00:02,000 --> 00:00:04,000\n测试字幕第二行\n")
    out10 = os.path.join(outdir, "muxed.mkv")
    s = run([OperationSpec(
        op=OP_MUX, inputs=[samples["mkv"]], output=out10,
        params={"container": ".mkv",
                "keep_audio": [0], "keep_subs": [],
                "extra_audio": [{"path": ext_audio, "language": "yue",
                                 "title": "粤语配音", "default": True}],
                "extra_subs": [{"path": ext_sub, "language": "chi",
                                "title": "简体中文", "default": True, "forced": False}]},
        label="sample_a.mkv", info=info)])
    check_common(s, "多轨封装执行成功", out10)
    if os.path.isfile(out10):
        info10 = prober.get(out10)
        kinds = [st.get("codec_type") for st in info10.streams]
        check("多轨：输出有 2 条音轨", kinds.count("audio") == 2, str(kinds))
        check("多轨：输出有 1 条字幕", kinds.count("subtitle") == 1, str(kinds))
        check("多轨：视频流仍在", kinds.count("video") >= 1, str(kinds))
        tracks = list_tracks(info10)
        yue = [t for t in tracks if t.kind == "audio" and t.language == "yue"]
        check("多轨：外部音轨语言标签写入", bool(yue), str([(t.kind, t.language) for t in tracks]))
        check("多轨：外部音轨为默认轨", bool(yue and yue[0].default), str(yue))
        chi_sub = [t for t in tracks if t.kind == "subtitle" and t.language == "chi"]
        check("多轨：字幕语言与默认标记", bool(chi_sub and chi_sub[0].default), str(chi_sub))

    # ---------- 场景 11：mp3 封面移除（封面是唯一视频流，回归第四轮修复）----------
    mp3_cov = os.path.join(workdir, "song.mp3")
    subprocess.run(
        [FFMPEG, "-y", "-nostdin", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-i", cover,
         "-map", "0:a", "-map", "1:v", "-c:a", "libmp3lame", "-c:v", "copy",
         "-id3v2_version", "3",
         "-metadata:s:v", "title=Album cover",
         "-metadata:s:v", "comment=Cover (front)", mp3_cov],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    check("mp3 封面样本已带 attached_pic", prober.get(mp3_cov).has_cover)
    out11 = os.path.join(outdir, "song_nocover.mp3")
    s = run([OperationSpec(
        op=OP_SET_COVER, inputs=[mp3_cov], output=out11,
        params={"remove": True}, label="song.mp3", info=prober.get(mp3_cov))])
    check_common(s, "mp3 移除封面执行成功", out11)
    if os.path.isfile(out11):
        info11 = prober.get(out11)
        check("mp3 移除后封面确实消失", not info11.has_cover,
              f"attached={info11.attached_pic_count}")
        check("mp3 移除后音轨仍在", bool(info11.ok and info11.a_codec),
              f"a={info11.a_codec}")

    # ---------- 场景 12：两个 mp4 合并为 mp3（自动重编码、仅取音轨）----------
    out12 = os.path.join(outdir, "merged.mp3")
    s = run([OperationSpec(
        op=OP_MERGE, inputs=[samples["mkv"], samples["mp4"]], output=out12,
        params={"container": ".mp3"},
        label="合并 2 个文件", info=None)])
    check_common(s, "mp4×2 合并为 mp3 执行成功", out12)
    if os.path.isfile(out12):
        info12 = prober.get(out12)
        check("mp3 合并输出只有音轨",
              bool(info12.ok and info12.a_codec and not info12.v_codec),
              f"a={info12.a_codec} v={info12.v_codec}")
        dur12 = info12.duration or 0
        check("mp3 合并时长约等于两段之和", 6.0 < dur12 < 9.0, f"dur={dur12}")

    # ---------- 清理 ----------
    drop_temp_files([samples["mkv"], samples["mp4"]])
    shutil.rmtree(workdir, ignore_errors=True)

    total = len(passed)
    fails = passed.count(False)
    print(f"\n=== 真实媒体全链路测试：{total - fails}/{total} 通过 ===")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
