"""子进程调用的统一封装。

约定（需求 5.2）：
- 永远用 argv 列表，绝不 shell=True。
- Windows 下隐藏控制台窗口（GUI 程序不得闪黑框）。
- stderr/stdout 一律按本地 locale 解码，errors='replace'，避免中文系统 GBK 输出炸掉。
"""

from __future__ import annotations

import locale
import os
import subprocess
from typing import Sequence

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# Windows 上给 subprocess 用的额外 flag；非 Windows 传 0 即可
NOCONSOLE = {"creationflags": CREATE_NO_WINDOW} if os.name == "nt" else {}


def preferred_encoding() -> str:
    """系统本地编码（中文 Windows 通常是 cp936/mbcs）。"""
    try:
        enc = locale.getpreferredencoding(False)
    except Exception:
        enc = "utf-8"
    return enc or "utf-8"


def decode(data: bytes | None, encoding: str | None = None) -> str:
    if not data:
        return ""
    enc = encoding or preferred_encoding()
    try:
        return data.decode(enc, errors="replace")
    except (LookupError, UnicodeDecodeError):
        return data.decode("utf-8", errors="replace")


def run_capture(
    argv: Sequence[str],
    timeout: float = 60.0,
    cwd: str | None = None,
) -> tuple[int, str, str]:
    """同步执行并捕获输出。返回 (returncode, stdout, stderr)。失败返回 (-1, "", 错误信息)。"""
    try:
        proc = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=cwd,
            timeout=timeout,
            **NOCONSOLE,  # type: ignore[arg-type]
        )
    except FileNotFoundError:
        return -1, "", f"找不到可执行文件：{argv[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", f"命令超时（{timeout:.0f}s）：{' '.join(argv[:2])} …"
    except OSError as exc:
        return -1, "", f"无法执行：{exc}"
    return proc.returncode, decode(proc.stdout), decode(proc.stderr)


def stderr_tail(text: str, lines: int = 40) -> str:
    """取 stderr 尾部若干行（错误展示用）。"""
    if not text:
        return ""
    parts = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(parts[-lines:])


__all__ = [
    "CREATE_NO_WINDOW",
    "NOCONSOLE",
    "decode",
    "preferred_encoding",
    "run_capture",
    "stderr_tail",
]
