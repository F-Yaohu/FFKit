"""路径、输出命名、临时文件与 argv 渲染的统一实现（需求 5.1 / 5.2 / 5.4）。

集中处理这些容易踩坑的地方：
- 输出冲突自动追加序号（不可能出现覆盖用户文件的情况，除非上层显式要求）
- 临时文件 + 原子重命名，避免取消/崩溃留下半个损坏文件
- concat 列表文件转义（' → '\\''）与 UTF-8 without BOM
- 命令行渲染（用于命令预览框，可直接复制到 cmd 执行）
"""

from __future__ import annotations

import os
import random
import re
import subprocess
import tempfile
from pathlib import Path

# 扩展名 → ffmpeg muxer 格式名。
# 因为临时文件名是 xxx.mp4.tmp（需求 5.1 指定的形式），ffmpeg 无法从扩展名推断格式，
# 必须显式传 -f。这里就是那张映射表。
MUXER_FORMAT: dict[str, str] = {
    ".mp4": "mp4",
    ".m4v": "mp4",
    ".mov": "mov",
    ".mkv": "matroska",
    ".webm": "webm",
    ".ts": "mpegts",
    ".flv": "flv",
    ".avi": "avi",
    ".gif": "gif",
    ".mp3": "mp3",
    ".wav": "wav",
    ".m4a": "ipod",
    ".aac": "adts",
    ".flac": "flac",
    ".ogg": "ogg",
    ".oga": "ogg",
    ".opus": "opus",
    ".ac3": "ac3",
    ".wma": "asf",
    ".jpg": "mjpeg",
    ".jpeg": "mjpeg",
    ".png": "png",
    ".bmp": "bmp",
    ".webp": "webp",
    ".tif": "tiff",
    ".tiff": "tiff",
}

VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".flv", ".ts", ".m4v", ".wmv", ".mpg", ".mpeg"}
AUDIO_EXTS = {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wma", ".ac3"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff"}
# 字幕：文本类可转 mov_text/srt；图形类（pgs/dvb）只能装进 mkv
SUBTITLE_EXTS = {".srt", ".ass", ".ssa", ".sub", ".vtt", ".sup", ".idx", ".lrc", ".smi"}

# ---------------------------------------------------------------------------
# 容器 ↔ 音频编码联动表
#
# 存在意义：MP4 里塞 MP3、WAV 里塞 AAC 这类组合很多播放器认不出来（问题 1 / 问题 2）。
# UI 下拉选项与 CommandBuilder 的构建期校验共用这张表，保证"界面上能选的"
# 和"命令里真正生成的"永远是同一套。
# ---------------------------------------------------------------------------
# 容器 → [(音频编码, 显示名)]
CONTAINER_AUDIO_CODECS: dict[str, list[tuple[str, str]]] = {
    "mp4": [("aac", "AAC（MP4 标准音轨）"), ("copy", "保持原样（无损，最快）"), ("none", "不要音轨")],
    "mov": [("aac", "AAC"), ("copy", "保持原样（无损）"), ("none", "不要音轨")],
    "ipod": [("aac", "AAC"), ("copy", "保持原样（无损，最快）"), ("none", "不要音轨")],
    "matroska": [
        ("copy", "保持原样（无损，最快）"),
        ("aac", "AAC"),
        ("mp3", "MP3 (libmp3lame)"),
        ("opus", "Opus"),
        ("flac", "FLAC"),
        ("none", "不要音轨"),
    ],
    "webm": [("opus", "Opus"), ("vorbis", "Vorbis"), ("copy", "保持原样（无损）"), ("none", "不要音轨")],
    "mp3": [("mp3", "MP3 (libmp3lame)")],
    "wav": [("wav", "PCM 16bit")],
    "flac": [("flac", "FLAC（无损压缩）")],
    "adts": [("aac", "AAC")],
    "ogg": [("vorbis", "Vorbis"), ("opus", "Opus")],
    "opus": [("opus", "Opus")],
    "asf": [("copy", "保持原样"), ("mp3", "MP3"), ("aac", "AAC")],
}

# 只能装音频的容器（选中即代表"输出纯音频"）
AUDIO_ONLY_MUXERS = {"mp3", "wav", "ipod", "adts", "flac", "ogg", "opus"}

# 容器默认音频编码（用于"用户没选 / 选了不兼容项"时兜底）
DEFAULT_AUDIO_CODEC: dict[str, str] = {
    "mp4": "aac",
    "mov": "aac",
    "ipod": "aac",
    "matroska": "aac",
    "webm": "opus",
    "mp3": "mp3",
    "wav": "wav",
    "flac": "flac",
    "adts": "aac",
    "ogg": "vorbis",
    "opus": "opus",
    "asf": "mp3",
}


def audio_codec_choices(container_or_ext: str) -> list[tuple[str, str]]:
    """给定容器（muxer 名或扩展名）返回可用的音频编码选项。"""
    key = str(container_or_ext or "").lstrip(".")
    if key in CONTAINER_AUDIO_CODECS:
        return CONTAINER_AUDIO_CODECS[key]
    muxer = MUXER_FORMAT.get("." + key, "")
    if muxer in CONTAINER_AUDIO_CODECS:
        return CONTAINER_AUDIO_CODECS[muxer]
    # 未知容器：宽容处理，允许 copy 与常见重编码
    return [("copy", "保持原样（无损）"), ("aac", "AAC"), ("mp3", "MP3"), ("none", "不要音轨")]


def default_audio_codec(container_or_ext: str) -> str:
    key = str(container_or_ext or "").lstrip(".")
    if key in DEFAULT_AUDIO_CODEC:
        return DEFAULT_AUDIO_CODEC[key]
    muxer = MUXER_FORMAT.get("." + key, "")
    return DEFAULT_AUDIO_CODEC.get(muxer, "aac")


def container_accepts_audio(container_or_ext: str, codec: str) -> bool:
    """该容器是否接受这个音频编码（copy 一律放行，交由 _audio_copyable 判定源编码）。"""
    if codec in ("copy", ""):
        return True
    return codec in {value for value, _ in audio_codec_choices(container_or_ext)}


def ext_of(path: str) -> str:
    return os.path.splitext(path)[1].lower()


def stem_of(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0]


def dir_of(path: str) -> str:
    return os.path.dirname(os.path.abspath(path)) or os.getcwd()


def muxer_format_for(path: str) -> str | None:
    """根据目标扩展名返回要显式传给 ffmpeg 的 -f 值；未知返回 None。"""
    return MUXER_FORMAT.get(ext_of(path))


def unique_output(path: str) -> str:
    """输出冲突时自动追加序号：xxx.mp4 → xxx_1.mp4 → xxx_2.mp4 …"""
    if not os.path.exists(path):
        return path
    directory = dir_of(path)
    base, ext = os.path.splitext(os.path.basename(path))
    index = 1
    while True:
        candidate = os.path.join(directory, f"{base}_{index}{ext}")
        if not os.path.exists(candidate):
            return candidate
        index += 1
        if index > 9999:  # 极端兜底
            return os.path.join(directory, f"{base}_{os.getpid()}{ext}")


def unique_output_multi(paths: list[str]) -> list[str]:
    """批量处理时保证同一批内的输出名互不冲突（含尚未落盘的）。"""
    taken: set[str] = set()
    result: list[str] = []
    for path in paths:
        candidate = path
        if candidate in taken or os.path.exists(candidate):
            directory = dir_of(candidate)
            base, ext = os.path.splitext(os.path.basename(candidate))
            index = 1
            while True:
                probe = os.path.join(directory, f"{base}_{index}{ext}")
                if probe not in taken and not os.path.exists(probe):
                    candidate = probe
                    break
                index += 1
        taken.add(candidate)
        result.append(candidate)
    return result


def make_temp_output(final_path: str) -> str:
    """临时输出路径：正式名 + .tmp（需求 5.1）。

    同目录 → 同一文件系统 → os.replace 是原子操作。
    """
    return final_path + ".tmp"


def drop_temp_files(paths: list[str]) -> None:
    for path in paths:
        if not path:
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass


def cleanup_passlog(prefix: str) -> None:
    """清理两遍编码产生的 ffmpeg2pass-*.log 系列文件。"""
    directory = os.path.dirname(prefix) or "."
    base = os.path.basename(prefix)
    try:
        for name in os.listdir(directory):
            if name.startswith(base):
                try:
                    os.remove(os.path.join(directory, name))
                except OSError:
                    pass
    except OSError:
        pass


def is_writable_dir(path: str) -> bool:
    """真正尝试写一个文件，比 os.access 可靠（Windows 上尤其）。"""
    if not path or not os.path.isdir(path):
        return False
    probe = os.path.join(path, f".ffkit_probe_{os.getpid()}_{random.randint(1000, 9999)}")
    try:
        with open(probe, "wb") as fh:
            fh.write(b"0")
        os.remove(probe)
        return True
    except OSError:
        return False


def free_space(path: str) -> int:
    try:
        import shutil

        return int(shutil.disk_usage(path).free)
    except Exception:
        return -1


# --------------------------------------------------------------------------
# 命令行渲染
# --------------------------------------------------------------------------
def quote_argv(argv: list[str]) -> str:
    """渲染成可直接复制到 Windows cmd / PowerShell 执行的字符串。"""
    if not argv:
        return ""
    if os.name == "nt":
        return subprocess.list2cmdline(list(argv))
    import shlex

    return shlex.join(argv)


def needs_quote(token: str) -> bool:
    return bool(re.search(r"[\s\"'&()^<>|]", token or ""))


# --------------------------------------------------------------------------
# concat 列表文件（需求 5.2）
# --------------------------------------------------------------------------
def escape_concat_path(path: str) -> str:
    """concat demuxer 的路径转义：单引号 → '\\'' 。"""
    return path.replace("'", "'\\''")


def write_concat_list(paths: list[str], list_file: str | None = None) -> str:
    """写 concat 列表文件：UTF-8 without BOM，行尾统一 \\n。

    返回列表文件路径。
    """
    if list_file is None:
        fd, list_file = tempfile.mkstemp(prefix="ffkit_concat_", suffix=".txt")
        os.close(fd)
    lines = [f"file '{escape_concat_path(p)}'" for p in paths]
    body = "\n".join(lines) + "\n"
    # newline="" 防止 Windows 把 \n 变成 \r\n 后影响解析（ffmpeg 能吃 \r\n，但保持干净）
    with open(list_file, "w", encoding="utf-8", newline="") as fh:
        fh.write(body)
    return list_file


def ensure_dir(path: str) -> bool:
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def default_output_dir() -> str:
    """默认输出目录：用户视频目录，退化到桌面，再退化到用户目录。"""
    home = os.path.expanduser("~")
    for candidate in (os.path.join(home, "Videos"), os.path.join(home, "Desktop"), home):
        if os.path.isdir(candidate):
            return candidate
    return home


__all__ = [
    "AUDIO_EXTS",
    "AUDIO_ONLY_MUXERS",
    "CONTAINER_AUDIO_CODECS",
    "DEFAULT_AUDIO_CODEC",
    "IMAGE_EXTS",
    "MUXER_FORMAT",
    "SUBTITLE_EXTS",
    "VIDEO_EXTS",
    "audio_codec_choices",
    "cleanup_passlog",
    "container_accepts_audio",
    "default_audio_codec",
    "default_output_dir",
    "dir_of",
    "drop_temp_files",
    "ensure_dir",
    "escape_concat_path",
    "ext_of",
    "free_space",
    "is_writable_dir",
    "make_temp_output",
    "muxer_format_for",
    "needs_quote",
    "quote_argv",
    "stem_of",
    "unique_output",
    "unique_output_multi",
    "write_concat_list",
]
