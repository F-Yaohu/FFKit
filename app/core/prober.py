"""ffprobe 元信息解析层（需求第四节模块 2）。

职责：
- 解析单个文件的格式/流信息为 ``MediaInfo``。
- 为合并功能提供"编码参数一致性"比对（signature）。
- 为进度解析提供 duration 缓存。
- 为智能切割提供关键帧时间戳。

所有结果按 (路径, mtime, size) 缓存，避免重复调用 ffprobe。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from .proc_util import run_capture, stderr_tail
from .timeparse import human_bitrate, human_size, parse_fps


class ProbeError(RuntimeError):
    pass


@dataclass
class MediaInfo:
    path: str = ""
    ok: bool = True
    error: str = ""

    size: int = 0
    duration: float = 0.0
    format_name: str = ""
    format_long_name: str = ""
    bit_rate: int = 0

    # 视频流
    v_codec: str = ""
    v_codec_long: str = ""
    v_width: int = 0
    v_height: int = 0
    v_fps: float = 0.0
    v_fps_raw: str = ""
    v_pix_fmt: str = ""
    v_sar: str = ""
    v_bit_rate: int = 0
    v_profile: str = ""
    v_nb_frames: int = 0

    # 音频流
    a_codec: str = ""
    a_codec_long: str = ""
    a_channels: int = 0
    a_sample_rate: int = 0
    a_bit_rate: int = 0
    a_channel_layout: str = ""

    streams: list[dict[str, Any]] = field(default_factory=list)
    # 容器级元数据（tags），供"文件属性"页回显
    tags: dict[str, str] = field(default_factory=dict)
    # 附加封面图数量（attached_pic）
    attached_pic_count: int = 0

    # ---------- 便捷属性 ----------
    @property
    def has_video(self) -> bool:
        return bool(self.v_codec)

    @property
    def video_stream_count(self) -> int:
        """真正的视频流数量（不含 attached_pic 封面）。"""
        return sum(
            1
            for st in self.streams
            if st.get("codec_type") == "video" and not st.get("disposition", {}).get("attached_pic")
        )

    @property
    def has_cover(self) -> bool:
        return self.attached_pic_count > 0

    @property
    def has_audio(self) -> bool:
        return bool(self.a_codec)

    @property
    def subtitle_codec(self) -> str:
        """第一条字幕流的编码名（外部 .srt/.ass 文件用）。"""
        for st in self.streams:
            if st.get("codec_type") == "subtitle":
                return str(st.get("codec_name") or "")
        return ""

    @property
    def resolution(self) -> str:
        if self.v_width and self.v_height:
            return f"{self.v_width}×{self.v_height}"
        return "-"

    @property
    def fps_text(self) -> str:
        if not self.v_fps:
            return "-"
        return f"{self.v_fps:.3f}".rstrip("0").rstrip(".")

    @property
    def total_bit_rate(self) -> int:
        """优先用容器总码率，缺失时用各流码率之和。"""
        if self.bit_rate:
            return self.bit_rate
        return (self.v_bit_rate or 0) + (self.a_bit_rate or 0)

    @property
    def is_audio_only(self) -> bool:
        return self.has_audio and not self.has_video

    def estimated_bit_rate(self) -> int:
        """源文件实际码率（压缩前展示用）。"""
        rate = self.total_bit_rate
        if not rate and self.duration > 0 and self.size > 0:
            rate = int(self.size * 8 / self.duration)
        return int(rate or 0)

    # ---------- 合并一致性 ----------
    def video_signature(self) -> tuple:
        return (
            self.v_codec,
            self.v_width,
            self.v_height,
            self.v_fps_raw or round(self.v_fps, 3),
            self.v_sar,
            self.v_pix_fmt,
        )

    def audio_signature(self) -> tuple:
        return (
            self.a_codec,
            self.a_sample_rate,
            self.a_channels,
        )

    def summary_rows(self) -> list[tuple[str, str]]:
        """文件信息面板展示用（P1）。"""
        if not self.ok:
            return [("状态", self.error or "无法解析")]
        rows: list[tuple[str, str]] = [
            ("文件", os.path.basename(self.path)),
            ("体积", human_size(self.size)),
            ("时长", f"{self.duration:.3f} 秒"),
            ("容器", self.format_name or "-"),
            ("总码率", human_bitrate(self.estimated_bit_rate())),
        ]
        if self.has_video:
            rows += [
                ("视频编码", f"{self.v_codec}" + (f" ({self.v_profile})" if self.v_profile else "")),
                ("分辨率", self.resolution),
                ("帧率", self.fps_text),
                ("像素格式", self.v_pix_fmt or "-"),
                ("采样宽高比", self.v_sar or "-"),
                ("视频码率", human_bitrate(self.v_bit_rate) if self.v_bit_rate else "未标注"),
            ]
        if self.has_audio:
            rows += [
                ("音频编码", self.a_codec),
                ("采样率", f"{self.a_sample_rate} Hz" if self.a_sample_rate else "-"),
                ("声道", f"{self.a_channels}" if self.a_channels else "-"),
                ("音频码率", human_bitrate(self.a_bit_rate) if self.a_bit_rate else "未标注"),
            ]
        if not self.has_audio:
            rows.append(("音频", "无音轨"))
        if not self.has_video:
            rows.append(("视频", "无视频流"))
        return rows


def _pick_stream(streams: list[dict[str, Any]], kind: str, skip_attached_pic: bool = False) -> dict[str, Any] | None:
    for st in streams:
        if st.get("codec_type") != kind:
            continue
        if skip_attached_pic and st.get("disposition", {}).get("attached_pic"):
            continue
        return st
    return None


@dataclass
class TrackInfo:
    """单条轨道的可读摘要（多轨封装页用）。"""

    index: int          # ffprobe 的全局 stream index（-map 0:<index> 直接可用）
    kind: str           # "video" / "audio" / "subtitle"
    codec: str = ""
    language: str = "und"
    title: str = ""
    default: bool = False
    forced: bool = False
    detail: str = ""    # 人读的附加信息，如 "立体声 · 48kHz"
    is_cover: bool = False

    @property
    def kind_text(self) -> str:
        return {"video": "视频", "audio": "音轨", "subtitle": "字幕"}.get(self.kind, self.kind)

    def label(self) -> str:
        parts = [f"#{self.index}", self.kind_text, self.codec or "?"]
        if self.title:
            parts.append(self.title)
        if self.language and self.language != "und":
            parts.append(self.language)
        if self.detail:
            parts.append(self.detail)
        flags = [name for name, on in (("默认", self.default), ("强制", self.forced)) if on]
        if flags:
            parts.append("/".join(flags))
        return "  ".join(parts)


def _stream_track(st: dict[str, Any], index: int) -> TrackInfo:
    kind = str(st.get("codec_type") or "")
    disposition = st.get("disposition") or {}
    tags = st.get("tags") or {}
    language = str(tags.get("language") or "")
    title = str(tags.get("title") or "")
    detail = ""
    if kind == "audio":
        channels = _to_int(st.get("channels"))
        rate = _to_int(st.get("sample_rate"))
        if channels:
            detail += {1: "单声道", 2: "立体声"}.get(channels, f"{channels} 声道")
        if rate:
            detail += (" · " if detail else "") + f"{rate} Hz"
    elif kind == "video":
        w, h = _to_int(st.get("width")), _to_int(st.get("height"))
        if w and h:
            detail = f"{w}×{h}"
    return TrackInfo(
        index=index,
        kind=kind,
        codec=str(st.get("codec_name") or ""),
        language=language or "und",
        title=title,
        default=bool(disposition.get("default")),
        forced=bool(disposition.get("forced")),
        detail=detail,
        is_cover=bool(disposition.get("attached_pic")),
    )


def list_tracks(info: MediaInfo) -> list[TrackInfo]:
    """把 ffprobe 的原始流列表整理成可读的轨道列表（封面图流会被标出但不剔除）。"""
    return [_stream_track(st, i) for i, st in enumerate(info.streams or [])]


class Prober:
    """ffprobe 包装。ffprobe 路径可动态设置（用户在设置里手改）。"""

    def __init__(self, ffprobe_path: str | None = None) -> None:
        self.ffprobe_path = ffprobe_path or ""
        self._cache: dict[tuple, MediaInfo] = {}
        self._kf_cache: dict[tuple, list[float]] = {}

    def set_path(self, ffprobe_path: str | None) -> None:
        if ffprobe_path != self.ffprobe_path:
            self.ffprobe_path = ffprobe_path or ""
            self.clear()

    def clear(self) -> None:
        self._cache.clear()
        self._kf_cache.clear()

    def invalidate(self, path: str) -> None:
        for key in [k for k in self._cache if k[0] == path]:
            self._cache.pop(key, None)

    # ---------- 主入口 ----------
    def get(self, path: str) -> MediaInfo:
        """不抛异常版本；失败时返回 ok=False 的 MediaInfo。"""
        try:
            return self.probe(path)
        except ProbeError as exc:
            info = MediaInfo(path=path, ok=False, error=str(exc))
            return info

    def probe(self, path: str) -> MediaInfo:
        if not path:
            raise ProbeError("文件路径为空")
        if not os.path.isfile(path):
            raise ProbeError(f"文件不存在：{path}")

        try:
            stat = os.stat(path)
            key = (os.path.abspath(path), int(stat.st_mtime), int(stat.st_size))
        except OSError as exc:
            raise ProbeError(f"无法读取文件信息：{exc}") from exc

        cached = self._cache.get(key)
        if cached is not None:
            return cached

        if not self.ffprobe_path or not os.path.isfile(self.ffprobe_path):
            raise ProbeError("未找到 ffprobe，无法解析媒体信息")

        argv = [
            self.ffprobe_path,
            "-v", "error",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            "-i", path,
        ]
        code, out, err = run_capture(argv, timeout=60)
        if code != 0 or not out.strip():
            raise ProbeError(stderr_tail(err) or "ffprobe 解析失败")
        try:
            payload = json.loads(out)
        except json.JSONDecodeError as exc:
            raise ProbeError(f"ffprobe 输出无法解析：{exc}") from exc

        info = self._build(path, payload)
        self._cache[key] = info
        return info

    # ---------- 解析 ----------
    def _build(self, path: str, payload: dict[str, Any]) -> MediaInfo:
        fmt = payload.get("format") or {}
        streams = payload.get("streams") or []
        info = MediaInfo(path=path, ok=True)
        info.streams = streams

        info.size = _to_int(fmt.get("size")) or (os.path.getsize(path) if os.path.isfile(path) else 0)
        info.duration = _to_float(fmt.get("duration"))
        info.format_name = str(fmt.get("format_name") or "")
        info.format_long_name = str(fmt.get("format_long_name") or "")
        info.bit_rate = _to_int(fmt.get("bit_rate"))
        raw_tags = fmt.get("tags") or {}
        if isinstance(raw_tags, dict):
            info.tags = {str(k): str(v) for k, v in raw_tags.items()}
        info.attached_pic_count = sum(
            1 for st in streams if st.get("disposition", {}).get("attached_pic")
        )

        # 封面图（attached_pic）不算真正的视频流 —— 优先取非封面的那条
        v = _pick_stream(streams, "video", skip_attached_pic=True)
        if v is None:
            v = _pick_stream(streams, "video")
        if v:
            info.v_codec = str(v.get("codec_name") or "")
            info.v_codec_long = str(v.get("codec_long_name") or "")
            info.v_width = _to_int(v.get("width"))
            info.v_height = _to_int(v.get("height"))
            info.v_fps_raw = str(v.get("r_frame_rate") or "")
            info.v_fps = parse_fps(info.v_fps_raw)
            info.v_pix_fmt = str(v.get("pix_fmt") or "")
            info.v_sar = str(v.get("sample_aspect_ratio") or "")
            info.v_bit_rate = _to_int(v.get("bit_rate"))
            info.v_profile = str(v.get("profile") or "")
            info.v_nb_frames = _to_int(v.get("nb_frames"))
            # attached_pic 属于封面图，不算真正的视频流
            if v.get("disposition", {}).get("attached_pic"):
                info.v_codec = ""

        a = _pick_stream(streams, "audio")
        if a:
            info.a_codec = str(a.get("codec_name") or "")
            info.a_codec_long = str(a.get("codec_long_name") or "")
            info.a_channels = _to_int(a.get("channels"))
            info.a_sample_rate = _to_int(a.get("sample_rate"))
            info.a_bit_rate = _to_int(a.get("bit_rate"))
            info.a_channel_layout = str(a.get("channel_layout") or "")

        if not info.duration:
            # 容器没有 duration 时，尝试从流里取
            for st in streams:
                info.duration = max(info.duration, _to_float(st.get("duration")))
        return info

    # ---------- 关键帧 ----------
    def keyframes(self, path: str, timeout: float = 180.0) -> list[float]:
        """返回视频关键帧时间戳（秒，升序）。用于智能切割。"""
        if not path or not os.path.isfile(path):
            return []
        try:
            stat = os.stat(path)
            key = (os.path.abspath(path), int(stat.st_mtime), int(stat.st_size))
        except OSError:
            return []
        cached = self._kf_cache.get(key)
        if cached is not None:
            return cached
        if not self.ffprobe_path or not os.path.isfile(self.ffprobe_path):
            return []

        # 包级扫描比解码帧扫描快得多
        argv = [
            self.ffprobe_path,
            "-v", "error",
            "-select_streams", "v:0",
            "-skip_frame", "nokey",
            "-show_entries", "packet=pts_time,flags",
            "-of", "csv=p=0",
            path,
        ]
        code, out, _err = run_capture(argv, timeout=timeout)
        times: list[float] = []
        if code == 0 and out.strip():
            for line in out.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split(",")
                if len(parts) < 2:
                    continue
                stamp, flags = parts[0].strip(), parts[1].strip()
                if "K" not in flags:
                    continue
                try:
                    times.append(float(stamp))
                except ValueError:
                    continue

        if not times:
            # 退化方案：解码帧扫描
            argv2 = [
                self.ffprobe_path,
                "-v", "error",
                "-select_streams", "v:0",
                "-skip_frame", "nokey",
                "-show_entries", "frame=pts_time",
                "-of", "csv=p=0",
                path,
            ]
            code2, out2, _ = run_capture(argv2, timeout=timeout)
            if code2 == 0 and out2.strip():
                for line in out2.splitlines():
                    try:
                        times.append(float(line.strip().split(",")[0]))
                    except (ValueError, IndexError):
                        continue

        times = sorted({round(t, 4) for t in times if t >= 0})
        self._kf_cache[key] = times
        return times


def _to_int(value: Any) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["MediaInfo", "ProbeError", "Prober", "TrackInfo", "list_tracks"]
