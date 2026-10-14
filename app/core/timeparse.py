"""时间与数值格式化的统一实现（需求 5.5）。

时间输入同时支持三种格式：
- ``00:01:23`` / ``00:01:23.456``  （时分秒）
- ``01:23``                         （分:秒）
- ``83`` / ``83.5``                  （纯秒）

输出统一为 ``HH:MM:SS.mmm``。
"""

from __future__ import annotations

import re

_NUM = re.compile(r"^\d+(?:\.\d+)?$")


class TimeParseError(ValueError):
    """时间格式非法。"""


def parse_time(text: str) -> float | None:
    """解析时间字符串为秒。

    - 空字符串返回 None（表示"未填写"）。
    - 非法格式抛 TimeParseError，消息可直接展示给用户。
    """
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    raw = raw.replace("，", ",").replace("．", ".")
    if raw.startswith("-"):
        raise TimeParseError("时间不能为负数")

    if ":" in raw:
        parts = raw.split(":")
        if len(parts) > 3:
            raise TimeParseError(f"时间格式无法识别：{text}")
        secs = 0.0
        for idx, part in enumerate(parts):
            part = part.strip()
            if not _NUM.match(part):
                raise TimeParseError(f"时间格式无法识别：{text}")
            value = float(part)
            power = len(parts) - 1 - idx
            if power == 0:
                if len(parts) > 1 and value >= 60:
                    raise TimeParseError(f"秒数不能 ≥ 60（请写成 01:39 这样的形式）：{text}")
                secs += value
            elif power == 1:
                if value >= 60:
                    raise TimeParseError(f"分钟数不能 ≥ 60：{text}")
                secs += value * 60
            else:
                secs += value * 3600
        if secs < 0:
            raise TimeParseError("时间不能为负数")
        return secs

    if not _NUM.match(raw):
        raise TimeParseError(f"时间格式无法识别：{text}（支持 00:01:23 / 01:23 / 83）")
    return float(raw)


def parse_time_safe(text: str) -> tuple[float | None, str | None]:
    """不抛异常的版本，返回 (秒, 错误消息)。"""
    try:
        return parse_time(text), None
    except TimeParseError as exc:
        return None, str(exc)


def format_hms(seconds: float, ms: bool = False) -> str:
    """秒 → HH:MM:SS 或 HH:MM:SS.mmm。"""
    if seconds is None:
        return ""
    try:
        total = float(seconds)
    except (TypeError, ValueError):
        return ""
    if total < 0:
        total = 0.0
    hours = int(total // 3600)
    minutes = int((total % 3600) // 60)
    secs = total % 60
    if ms:
        whole = int(secs)
        milli = int(round((secs - whole) * 1000))
        if milli >= 1000:
            whole += 1
            milli -= 1000
        if whole >= 60:
            minutes += 1
            whole -= 60
        return f"{hours:02d}:{minutes:02d}:{whole:02d}.{milli:03d}"
    secs_int = int(round(secs))
    if secs_int >= 60:
        minutes += secs_int // 60
        secs_int %= 60
        if minutes >= 60:
            hours += minutes // 60
            minutes %= 60
    return f"{hours:02d}:{minutes:02d}:{secs_int:02d}"


def format_timestamp(seconds: float) -> str:
    """一键复制到剪贴板用的时间戳：00:01:23.456"""
    return format_hms(seconds, ms=True)


def format_short(seconds: float) -> str:
    """UI 上紧凑显示：1:23 / 1:02:03。"""
    text = format_hms(seconds)
    if text.startswith("00:"):
        return text[3:]
    if text.startswith("0"):
        return text[1:]
    return text


def filename_timestamp(seconds: float) -> str:
    """剪辑文件名专用：00.01.23（点分隔，需求 5.4）。"""
    return format_hms(seconds).replace(":", ".")


def human_size(num_bytes: float) -> str:
    try:
        value = float(num_bytes)
    except (TypeError, ValueError):
        return "-"
    if value < 0:
        return "-"
    units = ["B", "KB", "MB", "GB", "TB"]
    idx = 0
    while value >= 1024 and idx < len(units) - 1:
        value /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(value)} {units[idx]}"
    return f"{value:.1f} {units[idx]}"


def human_bitrate(bps: float | int | None) -> str:
    try:
        value = float(bps or 0)
    except (TypeError, ValueError):
        return "-"
    if value <= 0:
        return "-"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f} Mbps"
    if value >= 1000:
        return f"{value / 1000:.0f} kbps"
    return f"{value:.0f} bps"


def size_delta(before: int, after: int) -> str:
    """压缩前后体积对比，如 ``128.0 MB → 41.0 MB (-68%)``。"""
    if before <= 0:
        return f"{human_size(after)}"
    ratio = (after - before) / before * 100.0
    sign = "+" if ratio > 0 else ""
    return f"{human_size(before)} → {human_size(after)} ({sign}{ratio:.0f}%)"


def parse_fps(value: str) -> float:
    """把 '30000/1001' 或 '29.97' 解析成浮点帧率。"""
    if not value:
        return 0.0
    try:
        if "/" in value:
            num, _, den = value.partition("/")
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = [
    "TimeParseError",
    "filename_timestamp",
    "format_hms",
    "format_short",
    "format_timestamp",
    "human_bitrate",
    "human_size",
    "parse_fps",
    "parse_time",
    "parse_time_safe",
    "size_delta",
]
