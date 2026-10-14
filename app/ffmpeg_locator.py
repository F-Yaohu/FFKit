"""ffmpeg / ffprobe 定位与版本检测（需求三）。

检测顺序：PATH → 用户手动指定（配置文件记住）→ 常见安装目录。
注意：手动指定的优先级实际高于 PATH —— 用户指定了就尊重用户。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass

from .core.proc_util import run_capture

MIN_VERSION = (4, 0)

COMMON_DIRS = [
    r"C:\ffmpeg\bin",
    r"C:\Program Files\ffmpeg\bin",
    r"C:\Program Files (x86)\ffmpeg\bin",
    r"C:\ProgramData\chocolatey\bin",
    os.path.join(os.path.expanduser("~"), "scoop", "shims"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Links"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "ffmpeg", "bin"),
    os.path.join(os.environ.get("USERPROFILE", ""), "ffmpeg", "bin"),
    "/usr/local/bin",
    "/usr/bin",
    "/opt/homebrew/bin",
]

INSTALL_HINT = (
    "未检测到 ffmpeg。安装方式（任选其一）：\n"
    "1) winget install Gyan.FFmpeg\n"
    "2) scoop install ffmpeg\n"
    "3) 到 https://www.gyan.dev/ffmpeg/builds/ 下载 release 包，解压后把 bin 目录加入 PATH\n"
    "安装完成后，也可以在「设置 → ffmpeg 路径」里手动指定 ffmpeg.exe。"
)


@dataclass
class Toolchain:
    ffmpeg: str = ""
    ffprobe: str = ""
    version_text: str = ""
    version: tuple[int, ...] = ()
    source: str = ""          # 来源说明：config / PATH / 常见目录
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.ffmpeg and os.path.isfile(self.ffmpeg))

    @property
    def version_str(self) -> str:
        if self.version:
            return ".".join(str(x) for x in self.version)
        return self.version_text or "未知"

    @property
    def outdated(self) -> bool:
        if not self.version:
            return False
        return self.version[:2] < MIN_VERSION

    def summary(self) -> str:
        if not self.ok:
            return self.error or "未找到 ffmpeg"
        return f"ffmpeg {self.version_str}（{self.source}）"


def _exe(name: str) -> str:
    return f"{name}.exe" if os.name == "nt" else name


def _sibling_ffprobe(ffmpeg_path: str) -> str:
    directory = os.path.dirname(ffmpeg_path)
    candidate = os.path.join(directory, _exe("ffprobe"))
    if os.path.isfile(candidate):
        return candidate
    return ""


def detect_version(ffmpeg_path: str) -> tuple[str, tuple[int, ...]]:
    """解析 ffmpeg -version 首行，返回 (原始文本, 版本元组)。"""
    if not ffmpeg_path or not os.path.isfile(ffmpeg_path):
        return "", ()
    code, out, err = run_capture([ffmpeg_path, "-version"], timeout=15)
    text = (out or err or "").strip()
    if code != 0 and not text:
        return "", ()
    first_line = text.splitlines()[0] if text else ""
    match = re.search(r"version\s+([0-9]+)\.([0-9]+)(?:\.([0-9]+))?", first_line)
    if not match:
        return first_line, ()
    parts = [int(x) for x in match.groups() if x is not None]
    return first_line, tuple(parts)


def hw_encoders_available(ffmpeg_path: str) -> set[str]:
    """P2 预留：探测可用的硬件编码器（首版不启用，仅提供接口）。"""
    found: set[str] = set()
    if not ffmpeg_path or not os.path.isfile(ffmpeg_path):
        return found
    code, out, _ = run_capture([ffmpeg_path, "-hide_banner", "-encoders"], timeout=20)
    if code != 0:
        return found
    for name in ("h264_nvenc", "hevc_nvenc", "h264_qsv", "hevc_qsv", "h264_amf", "hevc_amf"):
        if name in out:
            found.add(name)
    return found


def locate(ffmpeg_override: str = "", ffprobe_override: str = "") -> Toolchain:
    """按优先级定位工具链。"""
    # 1) 用户手动指定
    if ffmpeg_override and os.path.isfile(ffmpeg_override):
        ffprobe = ffprobe_override if (ffprobe_override and os.path.isfile(ffprobe_override)) \
            else _sibling_ffprobe(ffmpeg_override)
        if not ffprobe:
            ffprobe = shutil.which("ffprobe") or ""
        text, version = detect_version(ffmpeg_override)
        return Toolchain(ffmpeg_override, ffprobe, text, version, "用户指定",
                         "" if ffprobe else "同目录下未找到 ffprobe，请一并指定")

    # 2) PATH
    ffmpeg = shutil.which("ffmpeg") or ""
    if ffmpeg:
        ffprobe = shutil.which("ffprobe") or _sibling_ffprobe(ffmpeg)
        text, version = detect_version(ffmpeg)
        return Toolchain(ffmpeg, ffprobe, text, version, "PATH",
                         "" if ffprobe else "PATH 中缺少 ffprobe，请一并指定")

    # 3) 常见安装目录
    for directory in COMMON_DIRS:
        if not directory or not os.path.isdir(directory):
            continue
        candidate = os.path.join(directory, _exe("ffmpeg"))
        if os.path.isfile(candidate):
            ffprobe = _sibling_ffprobe(candidate) or os.path.join(directory, _exe("ffprobe"))
            text, version = detect_version(candidate)
            return Toolchain(candidate, ffprobe if os.path.isfile(ffprobe) else "", text, version,
                             directory)

    return Toolchain(error=INSTALL_HINT)


__all__ = [
    "COMMON_DIRS",
    "INSTALL_HINT",
    "MIN_VERSION",
    "Toolchain",
    "detect_version",
    "hw_encoders_available",
    "locate",
]
