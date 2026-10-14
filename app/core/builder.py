"""CommandBuilder —— 全项目唯一负责生成 ffmpeg argv 的地方（需求第四节 / 5.1）。

所有功能都在这里把 OperationSpec 编译成 Job（1..N 个 Step）。
通用逻辑统一收敛在这里：
- 永远 argv 列表、永远 ``-y -nostdin -hide_banner``
- 永远 ``-progress pipe:1 -nostats``（进度走 stdout，stderr 只做日志）
- 临时文件 + 原子重命名（临时文件仍带真实扩展名的 muxer 由 ``-f`` 显式指定）
- 输出冲突、concat 转义、-safe 0、UTF-8 无 BOM 由 paths 层负责
"""

from __future__ import annotations

import os

from .paths import (
    AUDIO_ONLY_MUXERS,
    audio_codec_choices,
    container_accepts_audio,
    default_audio_codec,
    ext_of,
    muxer_format_for,
    write_concat_list,
)
from .prober import MediaInfo, Prober, list_tracks
from .spec import (
    Job,
    OP_COMPRESS,
    OP_CONVERT,
    OP_EDIT_METADATA,
    OP_EXTRACT_AUDIO,
    OP_EXTRACT_FRAME,
    OP_MERGE,
    OP_MUX,
    OP_SET_COVER,
    OP_STRIP_AUDIO,
    OP_TRIM,
    OperationSpec,
    Step,
)

# ---------------------------------------------------------------------------
# 编码器映射
# ---------------------------------------------------------------------------
VIDEO_ENCODERS = {
    "x264": "libx264",
    "h264": "libx264",
    "x265": "libx265",
    "hevc": "libx265",
    "mpeg4": "mpeg4",
    "vp9": "libvpx-vp9",
    "copy": "copy",
}

AUDIO_ENCODERS = {
    "aac": "aac",
    "mp3": "libmp3lame",
    "wav": "pcm_s16le",
    "pcm": "pcm_s16le",
    "flac": "flac",
    "opus": "libopus",
    "vorbis": "libvorbis",
    "ac3": "ac3",
    "copy": "copy",
}

# P2 预留：硬件编码接口（首版默认软编，UI 不暴露）
HW_ENCODERS: dict[str, dict[str, str]] = {
    "nvenc": {"x264": "h264_nvenc", "h264": "h264_nvenc", "x265": "hevc_nvenc", "hevc": "hevc_nvenc"},
    "qsv": {"x264": "h264_qsv", "h264": "h264_qsv", "x265": "hevc_qsv", "hevc": "hevc_qsv"},
    "amf": {"x264": "h264_amf", "h264": "h264_amf", "x265": "hevc_amf", "hevc": "hevc_amf"},
}
# 三套参数体系不同（需求第九节说明）
HW_QUALITY_FLAG = {"nvenc": "-cq", "qsv": "-global_quality", "amf": "-qp"}

# 容器 → 可无损 copy 的音频编码
AUDIO_COPY_OK = {
    "mp4": {"aac", "mp3", "ac3", "eac3", "alac"},
    "mov": {"aac", "mp3", "ac3", "eac3", "alac"},
    "ipod": {"aac", "alac"},
    "matroska": {"aac", "mp3", "ac3", "eac3", "opus", "vorbis", "flac", "dts", "pcm_s16le"},
    "webm": {"opus", "vorbis"},
    "mpegts": {"aac", "mp3", "ac3", "eac3"},
    "asf": {"wmav2", "mp3", "aac"},
}

# 智能切割的取舍：首尾需要重编码的总长度超过这个值就不值得，直接降级精确切割
SMART_MAX_REENCODE_SEC = 12.0
SMART_COPYABLE_VIDEO = {"h264", "hevc"}


# 容器 → (可直接 copy 的字幕编码集合, 不兼容时要转成的编码)
SUBTITLE_CODECS: dict[str, tuple[set[str], str]] = {
    "mp4": ({"mov_text", "tx3g"}, "mov_text"),
    "mov": ({"mov_text", "tx3g"}, "mov_text"),
    "ipod": ({"mov_text", "tx3g"}, "mov_text"),
    "matroska": (set(), ""),      # mkv 几乎收所有字幕，直接 copy
    "webm": ({"webvtt"}, "webvtt"),
}


class BuildError(RuntimeError):
    """构建阶段就能确定的致命错误（例如源文件没有音轨却要提取音频）。"""


class CommandBuilder:
    def __init__(
        self,
        ffmpeg_path: str | None = None,
        prober: Prober | None = None,
        hw_encoder: str = "none",
    ) -> None:
        self.ffmpeg_path = ffmpeg_path or "ffmpeg"
        self.prober = prober
        self.hw_encoder = hw_encoder or "none"

    # ------------------------------------------------------------------
    # 对外主入口
    # ------------------------------------------------------------------
    def build(self, spec: OperationSpec) -> Job:
        if spec.argv_override:
            # 手动编辑过的命令：原样执行，降级（需求 5.7）
            out = spec.argv_override[-1] if spec.argv_override else None
            step = Step(
                argv=list(spec.argv_override),
                label=spec.op_label,
                duration=spec.duration,
                progress=False,
            )
            return Job(
                spec=spec,
                steps=[step],
                final_output=out,
                warnings=["命令已被手动编辑：进度条不可用，且不再校验输出路径"],
            )

        handler = {
            OP_TRIM: self._build_trim,
            OP_MERGE: self._build_merge,
            OP_CONVERT: self._build_convert,
            OP_EXTRACT_AUDIO: self._build_extract_audio,
            OP_STRIP_AUDIO: self._build_strip_audio,
            OP_EXTRACT_FRAME: self._build_extract_frame,
            OP_COMPRESS: self._build_compress,
            OP_SET_COVER: self._build_set_cover,
            OP_EDIT_METADATA: self._build_edit_metadata,
            OP_MUX: self._build_mux,
        }.get(spec.op)
        if handler is None:
            raise BuildError(f"不支持的操作类型：{spec.op}")
        return handler(spec)

    # ------------------------------------------------------------------
    # 通用底层
    # ------------------------------------------------------------------
    def _base(self, progress: bool = True) -> list[str]:
        args = [self.ffmpeg_path, "-hide_banner", "-nostdin", "-y"]
        if progress:
            args += ["-progress", "pipe:1", "-nostats"]
        return args

    def _temp_for(self, final_path: str) -> str:
        """临时输出路径。

        已知扩展名 → ``xxx.mp4.tmp``，靠显式 ``-f`` 指定 muxer（需求 5.1 的形式）。
        未知扩展名 → 保留真实扩展名（``xxx.xyz.tmp.xyz``），让 ffmpeg 自己推断容器。
        """
        if muxer_format_for(final_path):
            return final_path + ".tmp"
        return f"{final_path}.tmp{ext_of(final_path)}"

    def _output_tail(self, final_path: str) -> tuple[list[str], str]:
        """返回 (输出参数, 临时文件路径)。

        - 已知扩展名 → 用 ``-f <muxer>`` + 临时文件
        - 未知扩展名 → 直接写正式名（没有临时文件，也就没有原子重命名）
        """
        fmt = muxer_format_for(final_path)
        if not fmt:
            # 极少见的扩展名：不折腾临时文件，直接输出
            return [final_path], final_path
        temp = self._temp_for(final_path)
        return ["-f", fmt, temp], temp

    def _make_step(
        self,
        argv: list[str],
        final_path: str,
        label: str,
        duration: float | None,
        cleanup: list[str] | None = None,
    ) -> Step:
        tail, temp = self._output_tail(final_path)
        step = Step(
            argv=list(argv) + tail,
            label=label,
            duration=duration,
            temp_out=temp,
            final_out=final_path,
            cleanup=list(cleanup or []),
        )
        return step

    def _info(self, path: str) -> MediaInfo | None:
        if self.prober is None or not path:
            return None
        info = self.prober.get(path)
        return info if info.ok else None

    # ---------- 编码参数 ----------
    def video_args(
        self,
        codec: str = "x264",
        crf: int | None = 20,
        preset: str = "medium",
        bitrate_k: int | None = None,
        hw: str | None = None,
    ) -> list[str]:
        if codec == "copy":
            return ["-c:v", "copy"]
        hw = (hw if hw is not None else self.hw_encoder) or "none"
        if hw in HW_ENCODERS and codec in HW_ENCODERS[hw]:
            name = HW_ENCODERS[hw][codec]
            args = ["-c:v", name]
            if preset:
                args += ["-preset", preset]
            if crf is not None:
                args += [HW_QUALITY_FLAG.get(hw, "-cq"), str(crf)]
            if bitrate_k:
                args += ["-b:v", f"{bitrate_k}k"]
            return args
        name = VIDEO_ENCODERS.get(codec, codec)
        args = ["-c:v", name]
        if name in ("libx264", "libx265"):
            args += ["-preset", preset or "medium"]
            if bitrate_k:
                args += ["-b:v", f"{bitrate_k}k"]
            elif crf is not None:
                args += ["-crf", str(crf)]
        elif bitrate_k:
            args += ["-b:v", f"{bitrate_k}k"]
        return args

    def audio_args(
        self,
        codec: str = "aac",
        bitrate_k: int = 192,
        channels: int | None = None,
        sample_rate: int | None = None,
    ) -> list[str]:
        if codec == "copy":
            return ["-c:a", "copy"]
        name = AUDIO_ENCODERS.get(codec, codec)
        args = ["-c:a", name]
        if name == "pcm_s16le":
            pass
        elif name == "libmp3lame":
            args += ["-b:a", f"{bitrate_k}k"]
        elif name == "flac":
            pass
        else:
            args += ["-b:a", f"{bitrate_k}k"]
        if channels:
            args += ["-ac", str(channels)]
        if sample_rate:
            args += ["-ar", str(sample_rate)]
        return args

    def _audio_copyable(self, container: str | None, audio_codec: str) -> bool:
        if not container or not audio_codec:
            return False
        allowed = AUDIO_COPY_OK.get(container)
        if allowed is None:
            return True
        return audio_codec in allowed

    def _pix_fmt(self, info: MediaInfo | None, fallback: str = "yuv420p") -> str:
        if info and info.v_pix_fmt:
            return info.v_pix_fmt
        return fallback

    def _faststart(self, final_path: str) -> list[str]:
        if ext_of(final_path) in (".mp4", ".m4v", ".mov"):
            return ["-movflags", "+faststart"]
        return []

    # ------------------------------------------------------------------
    # 1. 剪辑
    # ------------------------------------------------------------------
    def _build_trim(self, spec: OperationSpec) -> Job:
        params = spec.params
        mode = str(params.get("mode", "copy"))
        start = float(params.get("start") or 0.0)
        end = float(params.get("end") or 0.0)
        if end <= start:
            raise BuildError("结束时间必须大于开始时间")
        duration = end - start
        src = spec.inputs[0]
        info = spec.info or self._info(src)
        out = spec.output or ""
        if not out:
            raise BuildError("缺少输出路径")

        if mode == "smart":
            job = self._build_trim_smart(spec, info, start, end)
            note = self._trim_truncation_note(info, end)
            if note:
                job.notes.append(note)
            return job

        if mode == "copy":
            argv = self._base() + ["-ss", _sec(start), "-i", src, "-t", _sec(duration),
                                   "-c", "copy", "-avoid_negative_ts", "make_zero"]
            step = self._make_step(argv, out, "无损切割（-c copy）", duration)
            notes = [
                "无损切割：切割点自动对齐到最近关键帧，最大偏差约一个 GOP 长度。",
                "如需帧级精确，请改用「精确切割」。",
            ]
            note = self._trim_truncation_note(info, end)
            if note:
                notes.append(note)
            return Job(spec=spec, steps=[step], final_output=out, notes=notes)

        # 精确 / 绝对精确
        absolute = mode == "absolute"
        vcodec = str(params.get("vcodec", "x264"))
        crf = int(params.get("crf", 20))
        preset = str(params.get("preset", "medium"))
        ahead = ["-ss", _sec(start), "-i", src] if not absolute else ["-i", src, "-ss", _sec(start)]
        argv = self._base() + ahead + ["-t", _sec(duration)]
        argv += self.video_args(codec=vcodec, crf=crf, preset=preset)
        argv += ["-pix_fmt", self._pix_fmt(info)]
        acodec = "copy" if (info and info.has_audio and info.a_codec in ("aac", "mp3")) else "aac"
        argv += self.audio_args(codec=acodec, bitrate_k=192)
        argv += self._faststart(out)
        label = "绝对精确切割（-ss 后置 + 重编码）" if absolute else "精确切割（-ss 前置 + 重编码）"
        step = self._make_step(argv, out, label, duration)
        notes = ["绝对精确：逐帧定位，速度比精确切割慢。" if absolute else "精确切割：速度与精度平衡。"]
        note = self._trim_truncation_note(info, end)
        if note:
            notes.append(note)
        return Job(spec=spec, steps=[step], final_output=out, notes=notes)

    @staticmethod
    def _trim_truncation_note(info: "MediaInfo | None", end: float) -> str | None:
        """批量切割共用同一时间范围时，短文件的结尾会被自然截断——明说，别让用户猜。"""
        if info is not None and info.duration and end > info.duration + 0.5:
            return (
                f"结束时间超出该文件总时长（{info.duration:.2f} 秒），"
                "实际将在文件结尾处截断。"
            )
        return None

    def _build_trim_smart(
        self,
        spec: OperationSpec,
        info: MediaInfo | None,
        start: float,
        end: float,
        ) -> Job:
        """智能切割：仅对首尾两小段重编码，中间走 -c copy（LosslessCut 机制）。

        实现：切成 3 段 .ts（首/尾重编码、中间复制），再用 concat demuxer 无损拼回。
        条件不满足时自动降级为精确切割，并在 Job 里给出说明。
        """
        src = spec.inputs[0]
        out = spec.output or ""
        duration = end - start

        def fallback(reason: str) -> Job:
            job = self._build_trim(
                OperationSpec(
                    op=OP_TRIM,
                    inputs=spec.inputs,
                    output=out,
                    params={**spec.params, "mode": "precise"},
                    label=spec.label,
                    duration=duration,
                    info=spec.info,
                )
            )
            job.spec = spec
            job.warnings.append(f"智能切割不可用（{reason}），已自动降级为精确切割")
            return job

        if self.prober is None or info is None or not info.has_video:
            return fallback("无法读取源文件编码信息")
        if info.v_codec not in SMART_COPYABLE_VIDEO:
            return fallback(f"源视频编码 {info.v_codec or '未知'} 不支持无损复制段")

        keyframes = self.prober.keyframes(src)
        if len(keyframes) < 2:
            return fallback("未能解析关键帧")

        k1 = _first_ge(keyframes, start)
        k2 = _last_le(keyframes, end)
        if k1 is None or k2 is None or k2 - k1 <= 0.05:
            return fallback("时间区间内没有完整的关键帧区间")

        lead = max(0.0, k1 - start)
        tail = max(0.0, end - k2)
        if lead + tail > SMART_MAX_REENCODE_SEC:
            return fallback(f"首尾需重编码长度 {lead + tail:.1f}s 过长")
        if lead <= 0.01 and tail <= 0.01:
            # 首尾刚好都在关键帧上 → 纯无损
            argv = self._base() + ["-ss", _sec(start), "-i", src, "-t", _sec(duration),
                                   "-c", "copy", "-avoid_negative_ts", "make_zero"]
            step = self._make_step(argv, out, "智能切割（首尾对齐关键帧，纯无损）", duration)
            return Job(spec=spec, steps=[step], final_output=out,
                       notes=["首尾正好落在关键帧上，本次为纯无损复制。"])

        work_dir = os.path.dirname(os.path.abspath(out)) or os.getcwd()
        tag = f"{os.getpid()}"
        lead_ts = os.path.join(work_dir, f".ffkit_smart_{tag}_a.ts")
        main_ts = os.path.join(work_dir, f".ffkit_smart_{tag}_b.ts")
        tail_ts = os.path.join(work_dir, f".ffkit_smart_{tag}_c.ts")

        # 与"复制段"匹配的编码参数（必须一致，concat 才能拼）
        match_args = self.video_args(
            codec="x264" if info.v_codec == "h264" else "x265",
            crf=18,
            preset="veryfast",
        )
        match_args += ["-pix_fmt", self._pix_fmt(info)]
        if info.v_fps:
            match_args += ["-r", f"{info.v_fps:.6f}".rstrip("0").rstrip(".")]

        if info.has_audio and info.a_codec not in ("aac", "mp3", "ac3", "eac3"):
            audio_args = self.audio_args(codec="aac", bitrate_k=192)
        else:
            audio_args = ["-c:a", "copy"]

        steps: list[Step] = []
        intermediates: list[str] = []

        if lead > 0.01:
            argv = self._base() + ["-ss", _sec(start), "-i", src, "-t", _sec(lead)]
            argv += match_args + audio_args + ["-f", "mpegts", lead_ts]
            steps.append(Step(argv=argv, label=f"首段重编码（{lead:.2f}s）", duration=lead))
            intermediates.append(lead_ts)

        main_len = k2 - k1
        argv = self._base() + ["-ss", _sec(k1), "-i", src, "-t", _sec(main_len), "-c", "copy",
                               "-f", "mpegts", main_ts]
        steps.append(Step(argv=argv, label=f"中间段无损复制（{main_len:.2f}s）", duration=main_len))
        intermediates.append(main_ts)

        if tail > 0.01:
            argv = self._base() + ["-ss", _sec(k2), "-i", src, "-t", _sec(tail)]
            argv += match_args + audio_args + ["-f", "mpegts", tail_ts]
            steps.append(Step(argv=argv, label=f"尾段重编码（{tail:.2f}s）", duration=tail))
            intermediates.append(tail_ts)

        # 拼接
        list_file = os.path.join(work_dir, f".ffkit_smart_{tag}_list.txt")
        write_concat_list(intermediates, list_file)
        concat_argv = self._base() + ["-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy"]
        container = muxer_format_for(out)
        if container in ("mp4", "mov", "ipod") and info.has_audio and info.a_codec == "aac":
            concat_argv += ["-bsf:a", "aac_adtstoasc"]
        concat_argv += ["-avoid_negative_ts", "make_zero"]
        steps.append(
            self._make_step(
                concat_argv,
                out,
                f"拼接 {len(intermediates)} 段（无损）",
                duration,
                cleanup=intermediates + [list_file],
            )
        )

        job = Job(
            spec=spec,
            steps=steps,
            final_output=out,
            notes=[
                f"智能切割：中间 {main_len:.2f}s 无损复制，首尾共 {lead + tail:.2f}s 重编码。",
                "接缝处可能有极轻微的画面跳变，属于该方案固有代价。",
            ],
        )
        return job

    # ------------------------------------------------------------------
    # 2. 合并
    # ------------------------------------------------------------------
    def _build_merge(self, spec: OperationSpec) -> Job:
        inputs = list(spec.inputs)
        out = spec.output or ""
        if len(inputs) < 2:
            raise BuildError("至少需要 2 个文件才能合并")
        infos = [self._info(p) for p in inputs]
        container = muxer_format_for(out)
        job_notes: list[str] = []
        job_warnings: list[str] = []

        consistent = True
        reference = None
        for info in infos:
            if info is None:
                consistent = False
                break
            if reference is None:
                reference = info
                continue
            if info.video_signature() != reference.video_signature():
                consistent = False
                break
            if info.has_audio != reference.has_audio:
                consistent = False
                break
            if info.has_audio and info.audio_signature() != reference.audio_signature():
                consistent = False
                break

        audio_out = container in AUDIO_ONLY_MUXERS
        all_have_video = all(i is not None and i.has_video for i in infos)
        none_have_video = all(i is not None and not i.has_video for i in infos)

        def concat_copy_job(note: str) -> Job:
            list_file = os.path.join(
                os.path.dirname(os.path.abspath(out)) or os.getcwd(),
                f".ffkit_concat_{os.getpid()}.txt",
            )
            write_concat_list(inputs, list_file)
            argv = self._base() + ["-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy",
                                   "-avoid_negative_ts", "make_zero"]
            step = self._make_step(argv, out, "concat demuxer 无损合并", None,
                                   cleanup=[list_file])
            return Job(spec=spec, steps=[step], final_output=out, notes=[note])

        # 无损路径：视频输出要求编码一致；音频输出还要求源音频编码能原样放入容器
        copy_ok = consistent and not audio_out
        if consistent and audio_out:
            copy_ok = (
                all(i is not None and i.is_audio_only for i in infos)
                and reference is not None
                and self._audio_copyable(container, reference.a_codec)
            )
        if copy_ok:
            return concat_copy_job("编码参数完全一致 → 走 concat demuxer，无损秒合、无重编码。")

        # 降级：concat filter 重编码
        job_warnings.append("各文件编码参数不一致 → 自动降级为 concat filter 重编码（慢但必定成功）")
        ref = reference or next((i for i in infos if i is not None), None)

        if audio_out:
            # ---- 纯音频输出：只拼音轨，画面一律丢弃 ----
            if not all(i is not None and i.has_audio for i in infos):
                raise BuildError("有文件没有音轨，无法合并为纯音频格式；请移除没有音轨的文件")
            if any(i is not None and i.has_video for i in infos):
                job_warnings.append("输出为纯音频格式：各文件的视频画面会被丢弃，仅合并音轨")
            argv = self._base()
            for path in inputs:
                argv += ["-i", path]
            chains = [f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{idx}]"
                      for idx in range(len(inputs))]
            concat_inputs = "".join(f"[a{idx}]" for idx in range(len(inputs)))
            filter_complex = (
                ";".join(chains) + ";" + concat_inputs
                + f"concat=n={len(inputs)}:v=0:a=1[aout]"
            )
            argv += ["-filter_complex", filter_complex, "-map", "[aout]"]
            argv += self.audio_args(codec=default_audio_codec(container), bitrate_k=192)
            total_duration = sum(i.duration for i in infos if i is not None) or None
            step = self._make_step(argv, out, "concat filter 重编码合并（仅音轨）", total_duration)
            job_notes.append("已统一为 48kHz 立体声后重编码拼接。")
            return Job(spec=spec, steps=[step], final_output=out,
                       warnings=job_warnings, notes=job_notes)

        if not all_have_video and not none_have_video:
            raise BuildError(
                "列表里同时存在纯音频文件与视频文件，无法自动合并；请只保留其中一类"
            )

        width = ref.v_width if ref and ref.v_width else 1920
        height = ref.v_height if ref and ref.v_height else 1080
        if width % 2:
            width += 1
        if height % 2:
            height += 1
        fps = ref.v_fps if ref and ref.v_fps else 30.0
        fps_text = f"{fps:.6f}".rstrip("0").rstrip(".")

        all_have_audio = all(i is not None and i.has_audio for i in infos)
        none_have_audio = all(i is not None and not i.has_audio for i in infos)
        use_audio = all_have_audio
        if not all_have_audio and not none_have_audio:
            job_warnings.append("部分文件没有音轨，本次仅合并视频轨（其余文件音频被忽略）")

        argv = self._base()
        for path in inputs:
            argv += ["-i", path]

        chains: list[str] = []
        concat_inputs: list[str] = []
        if not none_have_video:
            for idx in range(len(inputs)):
                chains.append(
                    f"[{idx}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps_text},format=yuv420p[v{idx}]"
                )
                concat_inputs.append(f"[v{idx}]")
            if use_audio:
                chains += [
                    f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{idx}]"
                    for idx in range(len(inputs))
                ]
                concat_inputs += [f"[a{idx}]" for idx in range(len(inputs))]
        else:
            # 全部是纯音频文件、但输出选了视频容器（mkv 等）→ 只拼音轨
            for idx in range(len(inputs)):
                chains.append(
                    f"[{idx}:a]aformat=sample_rates=48000:channel_layouts=stereo[a{idx}]"
                )
                concat_inputs.append(f"[a{idx}]")
        has_video_out = not none_have_video
        v_flag = 1 if has_video_out else 0
        a_flag = 1 if use_audio else 0
        # 输出标签必须与 v/a 旗标一致，否则 concat 的输出 pad 对不上会直接报错
        out_labels = ("[v]" if has_video_out else "") + ("[a]" if use_audio else "")
        filter_complex = (
            ";".join(chains) + ";" + "".join(concat_inputs)
            + f"concat=n={len(inputs)}:v={v_flag}:a={a_flag}" + out_labels
        )

        argv += ["-filter_complex", filter_complex]
        if has_video_out:
            argv += ["-map", "[v]"]
            argv += self.video_args(codec="x264", crf=20, preset="veryfast")
        if use_audio:
            argv += ["-map", "[a]"]
            argv += self.audio_args(codec="aac", bitrate_k=192)
        argv += self._faststart(out)

        total_duration = sum(i.duration for i in infos if i is not None) or None
        step = self._make_step(
            argv, out,
            "concat filter 重编码合并" if has_video_out else "concat filter 重编码合并（仅音轨）",
            total_duration,
        )
        if has_video_out:
            job_notes.append(f"统一画布 {width}×{height} @ {fps_text}fps，画面不足处留黑边。")
        else:
            job_notes.append("全部文件都没有视频流，输出为无画面的音频文件。")
        return Job(spec=spec, steps=[step], final_output=out,
                   warnings=job_warnings, notes=job_notes)

    # ------------------------------------------------------------------
    # 3. 格式转换
    # ------------------------------------------------------------------
    def _build_convert(self, spec: OperationSpec) -> Job:
        params = spec.params
        src = spec.inputs[0]
        out = spec.output or ""
        container = muxer_format_for(out) or "mp4"
        ext = ext_of(out)
        info = spec.info or self._info(src)
        video_only_container = ext in (".mp4", ".mkv", ".mov", ".webm", ".avi")

        warnings: list[str] = []
        notes: list[str] = []

        vcodec = str(params.get("vcodec", "x264"))
        acodec = str(params.get("acodec", "aac"))
        preset = str(params.get("preset", "medium"))
        crf = int(params.get("crf", 20))
        vbitrate = int(params.get("vbitrate_k") or 0)
        abitrate = int(params.get("abitrate_k") or 192)
        width = int(params.get("width") or 0)
        height = int(params.get("height") or 0)
        fps = float(params.get("fps") or 0)
        rotation = str(params.get("rotation", "none"))
        volume = float(params.get("volume") or 1.0)

        # 音频容器 / 源文件没有视频流 → 都走纯音频路径
        if ext in (".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus") or container in (
            "mp3", "wav", "ipod", "adts", "flac", "ogg", "opus",
        ):
            video_only_container = False
        if info is not None and not info.has_video:
            video_only_container = False

        vfilters: list[str] = []
        if video_only_container:
            if rotation == "cw":
                vfilters.append("transpose=1")
            elif rotation == "ccw":
                vfilters.append("transpose=2")
            elif rotation == "180":
                vfilters.append("transpose=1,transpose=1")
            elif rotation == "hflip":
                vfilters.append("hflip")
            elif rotation == "vflip":
                vfilters.append("vflip")
            if width or height:
                w = width if width else -2
                h = height if height else -2
                vfilters.append(f"scale={w}:{h}")
            if fps:
                vfilters.append(f"fps={fps:g}")

        afilters: list[str] = []
        if abs(volume - 1.0) > 1e-6:
            afilters.append(f"volume={volume:g}")

        if vcodec == "copy" and vfilters:
            warnings.append("使用了画面处理参数，视频无法无损复制，已自动改为重编码（libx264）")
            vcodec = "x264"

        argv = self._base() + ["-i", src]
        if video_only_container:
            if vfilters:
                argv += ["-vf", ",".join(vfilters)]
            argv += self.video_args(codec=vcodec, crf=crf, preset=preset,
                                    bitrate_k=vbitrate or None)
            if vcodec != "copy":
                argv += ["-pix_fmt", self._pix_fmt(info)]
            if vcodec == "x265" and ext in (".mp4", ".m4v", ".mov"):
                argv += ["-tag:v", "hvc1"]
            # 音频
            if acodec == "copy" and not self._audio_copyable(container, info.a_codec if info else ""):
                warnings.append("源音频编码无法放入目标容器，已改为 AAC 重编码")
                acodec = "aac"
            elif acodec not in ("copy", "none") and not container_accepts_audio(container, acodec):
                # 容器与音频编码联动（问题 2）：MP4 里不放 MP3
                fallback = default_audio_codec(container)
                warnings.append(
                    f"{_codec_name(acodec)} 不适配 {container} 容器，已改用 {_codec_name(fallback)}"
                )
                acodec = fallback
            if info is not None and not info.has_audio:
                argv += ["-an"]
            elif acodec == "none":
                argv += ["-an"]
            else:
                if afilters and acodec == "copy":
                    acodec = "aac"
                    warnings.append("使用了音量处理，音频无法无损复制，已改为 AAC 重编码")
                if afilters:
                    argv += ["-af", ",".join(afilters)]
                argv += self.audio_args(codec=acodec, bitrate_k=abitrate)
            argv += self._faststart(out)
        else:
            if info is not None and not info.has_audio:
                raise BuildError(
                    f"该文件没有音轨（{ext.lstrip('.') or container} 是纯音频格式），无法转换；"
                    "图片请转为视频容器格式，或改用「提取」页"
                )
            argv += ["-vn", "-map", "0:a:0"]
            src_audio = info.a_codec if info else ""
            keep_copy = acodec == "copy" and self._audio_copyable(container, src_audio)
            if keep_copy:
                acodec = "copy"
            else:
                # 容器与音频编码联动（问题 1）：音频容器只能装对应编码，
                # 例如 wav/flac 里塞 AAC 会直接失败、mp4 里的 mp3 兼容性差。
                if acodec in ("none", "x264", "x265", ""):
                    acodec = ""
                if not acodec or not container_accepts_audio(container, acodec):
                    fallback = default_audio_codec(container)
                    if acodec:
                        warnings.append(
                            f"{ _codec_name(acodec) } 不能装进 {ext.lstrip('.') or container} 容器，"
                            f"已改用 {_codec_name(fallback)}"
                        )
                        notes.append(
                            f"容器联动：{container} → 音频编码 {_codec_name(fallback)}"
                        )
                    elif afilters:
                        notes.append(
                            f"容器联动：{container} → 音频编码 {_codec_name(fallback)}"
                        )
                    acodec = fallback
                if afilters and acodec == "copy":
                    acodec = default_audio_codec(container)
                    warnings.append("使用了音量处理，音频无法无损复制，已改为重编码")
            if afilters:
                argv += ["-af", ",".join(afilters)]
            argv += self.audio_args(codec=acodec, bitrate_k=abitrate)
            if acodec == "copy":
                notes.append("音轨无损复制（音频参数未被改动）。")
            else:
                notes.append(f"音频格式：{_codec_name(acodec)}")

        if vcodec == "copy":
            notes.append("视频流无损复制（不重编码）。")
        step = self._make_step(argv, out, f"转换为 {ext.lstrip('.') or container}", spec.duration)
        return Job(spec=spec, steps=[step], final_output=out, warnings=warnings, notes=notes)

    # ------------------------------------------------------------------
    # 4. 提取
    # ------------------------------------------------------------------
    def _build_extract_audio(self, spec: OperationSpec) -> Job:
        src = spec.inputs[0]
        out = spec.output or ""
        ext = ext_of(out)
        info = spec.info or self._info(src)
        warnings: list[str] = []
        notes: list[str] = []

        if info is not None and not info.has_audio:
            raise BuildError("该文件没有音轨，无法提取音频")

        src_audio = info.a_codec if info else ""
        argv = self._base() + ["-i", src, "-vn", "-map", "0:a:0"]

        if ext == ".m4a":
            if src_audio in ("aac", "alac"):
                argv += ["-c:a", "copy"]
                notes.append("音轨无损复制（秒级完成）")
            else:
                argv += self.audio_args(codec="aac", bitrate_k=int(spec.params.get("abitrate_k") or 192))
                warnings.append(f"源音频为 {src_audio}，无法放入 m4a，已改为 AAC 重编码")
        elif ext == ".aac":
            if src_audio == "aac":
                argv += ["-c:a", "copy"]
                notes.append("音轨无损复制（秒级完成）")
            else:
                argv += self.audio_args(codec="aac", bitrate_k=int(spec.params.get("abitrate_k") or 192))
                warnings.append(f"源音频为 {src_audio}，已改为 AAC 重编码")
        elif ext == ".mp3":
            argv += self.audio_args(codec="mp3", bitrate_k=int(spec.params.get("abitrate_k") or 192))
        elif ext == ".wav":
            argv += self.audio_args(codec="wav")
        elif ext == ".flac":
            argv += self.audio_args(codec="flac")
        elif ext in (".ogg", ".oga", ".opus"):
            argv += self.audio_args(codec="opus" if ext == ".opus" else "vorbis",
                                    bitrate_k=int(spec.params.get("abitrate_k") or 160))
        else:
            argv += ["-c:a", "copy"] if self._audio_copyable(muxer_format_for(out), src_audio) \
                else self.audio_args(codec="aac", bitrate_k=192)

        step = self._make_step(argv, out, "提取音频", spec.duration)
        return Job(spec=spec, steps=[step], final_output=out, warnings=warnings, notes=notes)

    def _build_strip_audio(self, spec: OperationSpec) -> Job:
        src = spec.inputs[0]
        out = spec.output or ""
        info = spec.info or self._info(src)
        if info is not None and not info.has_video:
            raise BuildError("该文件没有视频流，无法去音频")
        argv = self._base() + ["-i", src, "-map", "0:v:0", "-an", "-c:v", "copy"]
        argv += ["-avoid_negative_ts", "make_zero"]
        step = self._make_step(argv, out, "去音频（视频无损复制）", spec.duration)
        return Job(spec=spec, steps=[step], final_output=out,
                   notes=["视频流无损复制，仅剥离音轨（秒级完成）。"])

    def _build_extract_frame(self, spec: OperationSpec) -> Job:
        params = spec.params
        src = spec.inputs[0]
        out = spec.output or ""
        info = spec.info or self._info(src)
        when = float(params.get("time") or 0.0)
        quality = int(params.get("quality") or 2)
        max_width = int(params.get("max_width") or 0)
        notes: list[str] = []
        if info is not None and info.duration and when > info.duration:
            notes.append(
                f"时间点超出该文件总时长（{info.duration:.2f} 秒），已改为取结尾前一帧。"
            )
            when = max(0.0, info.duration - 0.05)
        argv = self._base() + ["-ss", _sec(when), "-i", src, "-frames:v", "1"]
        vfilters = []
        if max_width:
            vfilters.append(f"scale={max_width}:-2")
        if vfilters:
            argv += ["-vf", ",".join(vfilters)]
        ext = ext_of(out)
        if ext == ".png":
            argv += ["-compression_level", "3"]
        else:
            argv += ["-q:v", str(max(1, min(31, quality)))]
        step = self._make_step(argv, out, f"提取第 {when:.3f} 秒的画面", 0.0)
        return Job(spec=spec, steps=[step], final_output=out, notes=notes)

    # ------------------------------------------------------------------
    # 5. 压缩
    # ------------------------------------------------------------------
    CRF_PRESETS = {
        "x264": {"high": 20, "mid": 23, "low": 26},
        "x265": {"high": 24, "mid": 28, "low": 32},
    }

    def _build_compress(self, spec: OperationSpec) -> Job:
        params = spec.params
        src = spec.inputs[0]
        out = spec.output or ""
        info = spec.info or self._info(src)
        target = str(params.get("target", "video"))
        warnings: list[str] = []
        notes: list[str] = []

        if info is None:
            raise BuildError("无法读取源文件信息，不能安全压缩")
        if target == "audio":
            if not info.has_audio:
                raise BuildError("该文件没有音轨")
            container = muxer_format_for(out) or "mp3"
            codec = str(params.get("acodec", "")) or default_audio_codec(container)
            if not container_accepts_audio(container, codec):
                fallback = default_audio_codec(container)
                warnings.append(
                    f"{_codec_name(codec)} 不适配 {container} 容器，已改用 {_codec_name(fallback)}"
                )
                codec = fallback
            bitrate = int(params.get("abitrate_k") or params.get("bitrate_k") or 128)
            argv = self._base() + ["-i", src, "-vn", "-map", "0:a:0"]
            argv += self.audio_args(codec=codec, bitrate_k=bitrate)
            step = self._make_step(argv, out, f"音频压缩（{_codec_name(codec)} {bitrate} kbps）", spec.duration)
            return Job(spec=spec, steps=[step], final_output=out, warnings=warnings, notes=notes)

        if not info.has_video:
            raise BuildError("该文件没有视频流")

        codec = str(params.get("vcodec", "x264"))
        level = str(params.get("level", "mid"))
        preset = str(params.get("preset", "medium"))
        crf = int(params.get("crf") or self.CRF_PRESETS.get(codec, self.CRF_PRESETS["x264"]).get(level, 23))
        target_mb = float(params.get("target_mb") or 0.0)
        container = muxer_format_for(out)
        abitrate = int(params.get("abitrate_k") or 128)

        source_rate = info.estimated_bit_rate()
        if source_rate and source_rate < 900_000 and not target_mb:
            warnings.append(
                f"源文件码率仅 {source_rate / 1000:.0f} kbps，已接近网络视频水平，继续压缩收益有限且画质损失明显。"
            )

        audio_copy_ok = self._audio_copyable(container, info.a_codec) and info.has_audio
        want_acodec = str(params.get("acodec") or "") or default_audio_codec(container)
        if want_acodec == "copy" and not audio_copy_ok:
            want_acodec = default_audio_codec(container)
            notes.append("源音频与目标容器不兼容，音频将重编码")
        if not container_accepts_audio(container, want_acodec):
            fallback = default_audio_codec(container)
            warnings.append(
                f"{_codec_name(want_acodec)} 不适配 {container} 容器，已改用 {_codec_name(fallback)}"
            )
            want_acodec = fallback
        # 源音频无需重编码时保持无损复制（省时且无音质损失）
        audio_plan_copy = audio_copy_ok and want_acodec in ("copy", info.a_codec)
        if info.has_audio and not audio_plan_copy:
            notes.append(f"音频将重编码为 {_codec_name(want_acodec)} {abitrate}k")

        # ---- 目标体积：两遍编码 ----
        if target_mb > 0:
            duration = info.duration
            if duration <= 0:
                raise BuildError("无法获取时长，不能按目标体积压缩")
            total_kbits = target_mb * 1024 * 8
            audio_kbits = (abitrate * duration) if (info.has_audio and not audio_plan_copy) else 0
            video_kbps = (total_kbits - audio_kbits) / duration
            if video_kbps < 50:
                video_kbps = 50
                warnings.append("目标体积过小，已按最低可用码率 50 kbps 处理，实际体积可能超出目标。")
            log_prefix = os.path.join(
                os.path.dirname(os.path.abspath(out)) or os.getcwd(),
                f".ffkit_pass_{os.getpid()}",
            )
            pass1 = self._base() + ["-i", src]
            pass1 += self.video_args(codec=codec, crf=None, preset=preset, bitrate_k=int(video_kbps))
            pass1 += ["-pass", "1", "-passlogfile", log_prefix, "-an", "-f", "null", os.devnull]
            # 两遍编码由 -b:v 驱动，确保第一遍里没有 -crf 干扰
            pass1 = _strip_conflicting(pass1)
            pass2 = self._base() + ["-i", src]
            pass2 += self.video_args(codec=codec, crf=None, preset=preset, bitrate_k=int(video_kbps))
            pass2 += ["-pass", "2", "-passlogfile", log_prefix]
            pass2 += ["-pix_fmt", self._pix_fmt(info)]
            if codec == "x265" and ext_of(out) in (".mp4", ".m4v", ".mov"):
                pass2 += ["-tag:v", "hvc1"]
            if info.has_audio:
                pass2 += (["-c:a", "copy"] if audio_plan_copy
                          else self.audio_args(codec=want_acodec, bitrate_k=abitrate))
            else:
                pass2 += ["-an"]
            pass2 += self._faststart(out)

            step1 = Step(argv=pass1, label="第一遍编码（分析）", duration=info.duration)
            step2 = self._make_step(pass2, out, "第二遍编码（写成文件）", info.duration)
            notes.append(f"两遍编码：目标 {target_mb:g} MB → 视频码率约 {video_kbps:.0f} kbps")
            return Job(spec=spec, steps=[step1, step2], final_output=out,
                       warnings=warnings, notes=notes, source_sizes=[info.size],
                       passlog_prefixes=[log_prefix])

        # ---- CRF 单遍 ----
        argv = self._base() + ["-i", src]
        argv += self.video_args(codec=codec, crf=crf, preset=preset)
        argv += ["-pix_fmt", self._pix_fmt(info)]
        if codec == "x265" and ext_of(out) in (".mp4", ".m4v", ".mov"):
            argv += ["-tag:v", "hvc1"]
        if info.has_audio:
            argv += (["-c:a", "copy"] if audio_plan_copy
                     else self.audio_args(codec=want_acodec, bitrate_k=abitrate))
        else:
            argv += ["-an"]
        argv += self._faststart(out)
        step = self._make_step(argv, out, f"压缩（{codec} CRF {crf}）", info.duration)
        notes.append(f"{codec} CRF {crf}（档位：{ {'high': '高', 'mid': '中', 'low': '低'}.get(level, level) }）")
        return Job(spec=spec, steps=[step], final_output=out,
                   warnings=warnings, notes=notes, source_sizes=[info.size])

    # ------------------------------------------------------------------
    # 7. 封面
    # ------------------------------------------------------------------
    def _build_mux(self, spec: OperationSpec) -> Job:
        """多轨封装（新增 3）：主文件 + 外部音频/字幕 → 单个文件。

        - 视频与内嵌音轨**一律无损 copy**，秒级完成、无画质损失
        - 外部音轨：容器不收该编码时自动转成容器默认音频编码
        - 外部字幕：MP4 一律 mov_text（MP4 只认 tx3g/mov_text），MKV 直接 copy
        - 每条轨道显式写 language / title / disposition(default/forced)，
          播放器里才能正确显示"第X轨 · 中文 · 默认"
        """
        params = spec.params
        src = spec.inputs[0]
        out = spec.output or ""
        main_info = spec.info or self._info(src)
        if main_info is None or not main_info.ok:
            raise BuildError("无法解析主文件，请重新选择")

        container = str(params.get("container") or ext_of(src))
        ext = container if container.startswith(".") else f".{container}"
        # 注意：不能把裸扩展名直接交给 muxer_format_for——splitext 会把 ".mkv"
        # 当成"没有扩展名的隐藏文件"。拼一个假文件名再查表。
        muxer = muxer_format_for(f"mux_out{ext}")
        if muxer is None:
            raise BuildError(f"多轨封装不支持该输出格式：{container}")

        tracks = list_tracks(main_info)
        main_audio = [t for t in tracks if t.kind == "audio"]
        main_subs = [t for t in tracks if t.kind == "subtitle"]
        has_video = any(t.kind == "video" and not t.is_cover for t in tracks)

        keep_audio = params.get("keep_audio")
        keep_audio = list(range(len(main_audio))) if keep_audio is None else [int(i) for i in keep_audio]
        keep_subs = params.get("keep_subs")
        keep_subs = list(range(len(main_subs))) if keep_subs is None else [int(i) for i in keep_subs]
        extra_audio: list[dict] = list(params.get("extra_audio") or [])
        extra_subs: list[dict] = list(params.get("extra_subs") or [])

        # ---- 输入清单：0 = 主文件，之后是外部文件 ----
        inputs: list[str] = [src]
        for item in extra_audio:
            inputs.append(str(item.get("path") or ""))
        for item in extra_subs:
            inputs.append(str(item.get("path") or ""))
        for path in inputs:
            if not path or not os.path.isfile(path):
                raise BuildError(f"输入文件不存在：{path or '(空)'}")

        # ---- 输出轨道规划 ----
        # audio_plans / sub_plans: (输入序号, 流选择符, 编码, language, title, default, forced)
        audio_plans: list[tuple[int, str, str, str, str, bool, bool]] = []
        sub_plans: list[tuple[int, str, str, str, str, bool, bool]] = []

        copyable = AUDIO_COPY_OK.get(muxer, set())
        fallback_audio = default_audio_codec(muxer)

        for rel in keep_audio:
            if not 0 <= rel < len(main_audio):
                raise BuildError(f"要保留的内嵌音轨序号不合法：{rel}")
            t = main_audio[rel]
            audio_plans.append((0, f"a:{rel}", t.codec, t.language, t.title,
                                t.default, t.forced))
        for offset, item in enumerate(extra_audio):
            input_index = 1 + offset
            e_info = self._info(str(item.get("path") or ""))
            codec = e_info.a_codec if (e_info and e_info.ok) else ""
            audio_plans.append((input_index, "a:0", codec,
                                str(item.get("language") or "und"),
                                str(item.get("title") or ""),
                                bool(item.get("default")), False))

        sub_ok, sub_target = SUBTITLE_CODECS.get(muxer, (set(), ""))
        for rel in keep_subs:
            if not 0 <= rel < len(main_subs):
                raise BuildError(f"要保留的内嵌字幕序号不合法：{rel}")
            t = main_subs[rel]
            sub_plans.append((0, f"s:{rel}", t.codec, t.language, t.title,
                              t.default, t.forced))
        for offset, item in enumerate(extra_subs):
            input_index = 1 + len(extra_audio) + offset
            e_info = self._info(str(item.get("path") or ""))
            codec = e_info.subtitle_codec if (e_info and e_info.ok) else ""
            sub_plans.append((input_index, "s:0", codec,
                              str(item.get("language") or "und"),
                              str(item.get("title") or ""),
                              bool(item.get("default")), bool(item.get("forced"))))

        if not has_video and not audio_plans and not sub_plans:
            raise BuildError("没有任何可封装的轨道：请至少保留一条视频/音轨/字幕")

        # ---- 组装 argv ----
        argv = self._base()
        for path in inputs:
            argv += ["-i", path]

        if has_video:
            # V = 真视频流，自动跳过 attached_pic 封面
            argv += ["-map", "0:V:0", "-c:v", "copy"]
        warnings: list[str] = []

        for j, (input_index, selector, codec, _lang, _title, _d, _f) in enumerate(audio_plans):
            argv += ["-map", f"{input_index}:{selector}"]
            if codec and copyable and codec not in copyable:
                argv += [f"-c:a:{j}", fallback_audio]
                warnings.append(
                    f"第 {j + 1} 条音轨是 {_codec_name(codec)}，"
                    f"{muxer.upper()} 不支持原样封装，将转为 {_codec_name(fallback_audio)}"
                )
            else:
                argv += [f"-c:a:{j}", "copy"]

        for k, (input_index, selector, codec, _lang, _title, _d, _f) in enumerate(sub_plans):
            argv += ["-map", f"{input_index}:{selector}"]
            if sub_target and codec and codec not in sub_ok and codec != sub_target:
                argv += [f"-c:s:{k}", sub_target]
                warnings.append(
                    f"第 {k + 1} 条字幕是 {codec}，{muxer.upper()} 需要 {sub_target}，将自动转换"
                )
            else:
                argv += [f"-c:s:{k}", "copy"]

        # ---- 语言 / 标题 / 默认轨标记 ----
        # 默认轨：用户在界面上显式勾选「默认」的外部轨道优先；否则沿用源文件里
        # 本来就是 default 的那条；都没有就取第一条。
        explicit_audio = {len(keep_audio) + i for i, item in enumerate(extra_audio)
                          if item.get("default")}
        default_audio_index = next(
            (i for i in explicit_audio),                                   # 用户显式指定
            next((i for i, plan in enumerate(audio_plans) if plan[5]),      # 源文件自带
                 0 if audio_plans else -1),
        )
        for j, (_ii, _sel, _codec, lang, title, _d, _f) in enumerate(audio_plans):
            if lang and lang != "und":
                argv += [f"-metadata:s:a:{j}", f"language={lang}"]
            if title:
                argv += [f"-metadata:s:a:{j}", f"title={title}"]
            argv += [f"-disposition:a:{j}", "default" if j == default_audio_index else "0"]

        default_sub_index = next(
            (len(keep_subs) + i for i, item in enumerate(extra_subs) if item.get("default")),
            next((i for i, plan in enumerate(sub_plans) if plan[5]),
                 0 if sub_plans else -1),
        )
        for k, (_ii, _sel, _codec, lang, title, _d, forced) in enumerate(sub_plans):
            if lang and lang != "und":
                argv += [f"-metadata:s:s:{k}", f"language={lang}"]
            if title:
                argv += [f"-metadata:s:s:{k}", f"title={title}"]
            flags = []
            if k == default_sub_index:
                flags.append("default")
            if forced:
                flags.append("forced")
            argv += [f"-disposition:s:{k}", "+".join(flags) if flags else "0"]

        argv += ["-map_metadata", "0", "-map_chapters", "0", "-dn"]
        step = self._make_step(argv, out, "多轨封装", main_info.duration or None)
        return Job(spec=spec, steps=[step], final_output=out, warnings=warnings)

    def _build_set_cover(self, spec: OperationSpec) -> Job:
        """设置 / 更换 / 移除封面图（新增 1）。

        - 音频（mp3）：写 ID3 的 attached_pic 帧
        - 音频/视频（mp4/m4a/mov/mkv）：加一条 attached_pic 图片流
        全程 ``-c copy``：音视频流不重编码，秒级完成、无画质损失。
        """
        params = spec.params
        src = spec.inputs[0]
        out = spec.output or ""
        info = spec.info or self._info(src)
        cover = str(params.get("cover") or "")
        remove = bool(params.get("remove"))
        container = muxer_format_for(out) or "mp4"
        ext = ext_of(out)
        notes: list[str] = []

        if info is not None and not info.has_audio and not info.has_video:
            raise BuildError("该文件既没有音轨也没有视频流，无法设置封面")
        if not remove:
            if not cover:
                raise BuildError("请先选择一张封面图片")
            if not os.path.isfile(cover):
                raise BuildError(f"封面图片不存在：{cover}")
            if ext_of(cover) not in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
                raise BuildError("封面仅支持 JPG / PNG / BMP / WebP 图片")

        argv = self._base() + ["-i", src]

        if remove:
            # 反向映射丢弃封面流。封面流是 attached_pic 的视频流：
            # - mp4/mkv 里通常是"第 2 条视频流"（v:1）；
            # - mp3/m4a 里它是**唯一**的视频流（v:0），负映射 -0:v:1 匹配不上
            #   会被 ffmpeg 静默忽略 → 必须按 ffprobe 给出的全局流索引精确反选。
            argv += ["-map", "0", "-c", "copy", "-map_metadata", "0"]
            cover_index = _attached_pic_index(info)
            if cover_index is not None:
                argv += ["-map", f"-0:{cover_index}"]
                notes.append("已移除附加封面图，音视频流无损复制。")
            elif info is None:
                # 拿不到媒体信息（无 ffprobe）：保留旧行为做兜底
                argv += ["-map", "-0:v:1"]
                notes.append("已移除附加封面图，音视频流无损复制。")
            else:
                notes.append("该文件没有附加封面图，本次仅做重新封装。")
            if ext in (".mp3",):
                argv += ["-id3v2_version", "3"]
            label = "移除封面"
        else:
            argv += ["-i", cover]
            if ext == ".mp3":
                argv += ["-map", "0:a", "-map", "1:v", "-c", "copy",
                         "-id3v2_version", "3",
                         "-metadata:s:v", "title=Album cover",
                         "-metadata:s:v", "comment=Cover (front)"]
                notes.append("已写入 ID3 封面（mp3）。")
            else:
                argv += ["-map", "0", "-map", "1:v:0", "-c", "copy", "-c:v:1", "mjpeg",
                         "-disposition:v:1", "attached_pic",
                         "-metadata:s:v:1", "title=Cover",
                         "-metadata:s:v:1", "comment=Cover (front)"]
                notes.append(f"已写入封面图（{ext.lstrip('.') or container} attached_pic）。")
            notes.append("音视频流无损复制，封面更换不会影响画质。")
            label = "设置封面"

        step = self._make_step(argv, out, label, spec.duration)
        return Job(spec=spec, steps=[step], final_output=out, notes=notes)

    # ------------------------------------------------------------------
    # 8. 文件属性（元数据）
    # ------------------------------------------------------------------
    def _build_edit_metadata(self, spec: OperationSpec) -> Job:
        """编辑元数据（新增 2）：标题 / 艺术家 / 专辑 / 年份 / 季集号等。

        值为空字符串表示**清除**该字段（ffmpeg 的 ``-metadata key=`` 语义）。
        同样走 ``-c copy``，只改容器头里的元数据。
        """
        params = spec.params
        src = spec.inputs[0]
        out = spec.output or ""
        tags: dict[str, str] = dict(params.get("tags") or {})
        clear: list[str] = list(params.get("clear") or [])
        if not tags and not clear:
            raise BuildError("没有要修改的属性，请至少填写一项")

        argv = self._base() + ["-i", src, "-map", "0", "-c", "copy", "-map_metadata", "0"]
        if ext_of(out) == ".mp3":
            argv += ["-id3v2_version", "3"]
        applied: list[str] = []
        for key, value in tags.items():
            argv += ["-metadata", f"{key}={value}"]
            applied.append(f"{key}={value}" if value else f"{key}=（清除）")
        for key in clear:
            argv += ["-metadata", f"{key}="]
            applied.append(f"{key}=（清除）")

        notes = [
            "已写入 " + str(len(applied)) + " 项属性：" + "、".join(applied[:6])
            + ("…" if len(applied) > 6 else ""),
            "仅改写容器元数据，音视频流无损复制。",
        ]
        step = self._make_step(argv, out, "编辑文件属性", spec.duration)
        return Job(spec=spec, steps=[step], final_output=out, notes=notes)


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _attached_pic_index(info: MediaInfo | None) -> int | None:
    """封面流（attached_pic）的全局流索引；找不到返回 None。"""
    if info is None:
        return None
    for i, st in enumerate(info.streams or []):
        if st.get("codec_type") == "video" and st.get("disposition", {}).get("attached_pic"):
            return i
    return None


def _sec(value: float) -> str:
    """秒数转字符串：不带多余小数、不会出现科学计数法。"""
    text = f"{float(value):.6f}".rstrip("0").rstrip(".")
    return text or "0"


def _first_ge(values: list[float], target: float, eps: float = 0.05) -> float | None:
    for value in values:
        if value >= target - eps:
            return value
    return None


def _last_le(values: list[float], target: float, eps: float = 0.05) -> float | None:
    result = None
    for value in values:
        if value <= target + eps:
            result = value
        else:
            break
    return result


def _default_audio_codec_for(ext: str) -> str:
    """兼容旧调用：等价于 paths.default_audio_codec。"""
    return default_audio_codec(ext)


# 音频编码 → 人话（告警与说明文案里用）
CODEC_DISPLAY = {
    "aac": "AAC",
    "mp3": "MP3",
    "wav": "PCM 16bit",
    "pcm": "PCM 16bit",
    "flac": "FLAC",
    "opus": "Opus",
    "vorbis": "Vorbis",
    "ac3": "AC3",
    "copy": "保持原样",
    "none": "无音轨",
}


def _codec_name(codec: str) -> str:
    return CODEC_DISPLAY.get(str(codec).lower(), str(codec).upper() or "未知")


def _strip_conflicting(argv: list[str]) -> list[str]:
    """去掉第一遍里可能出现的 -crf（两遍编码用 -b:v 驱动）。"""
    out: list[str] = []
    skip = False
    for token in argv:
        if skip:
            skip = False
            continue
        if token == "-crf":
            skip = True
            continue
        out.append(token)
    return out


__all__ = [
    "AUDIO_ENCODERS",
    "BuildError",
    "CommandBuilder",
    "HW_ENCODERS",
    "SMART_MAX_REENCODE_SEC",
    "VIDEO_ENCODERS",
]
