"""一键打包 FFKit（Windows）。

为什么要这个脚本，而不是直接 pyinstaller FFKit.spec：
PyInstaller 在「图标文件路径含非 ASCII 字符（如中文目录）」时会静默失败——
日志照样打印 "Copying icon to EXE"，但写进去的是它自带的默认图标。
本项目位于中文路径下，所以这里先把 logo.ico 复制到一个 ASCII 临时目录，
再用该路径作为 --icon，保证 exe 图标一定正确。

用法：python tools/build.py
产物：dist/FFKit/FFKit.exe
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
NAME = "FFKit"


def _probe_icon_color(path: Path) -> str | None:
    """读取 exe/ico 首个图标组中心像素颜色，用于验证图标是否正确应用。"""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import byref, wintypes

    user32, gdi32, shell32 = (
        ctypes.windll.user32,
        ctypes.windll.gdi32,
        ctypes.windll.shell32,
    )

    class ICONINFO(ctypes.Structure):
        _fields_ = [
            ("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
            ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
            ("hbmColor", wintypes.HBITMAP),
        ]

    class BITMAP(ctypes.Structure):
        _fields_ = [
            ("bmType", ctypes.c_long), ("bmWidth", ctypes.c_long),
            ("bmHeight", ctypes.c_long), ("bmWidthBytes", ctypes.c_long),
            ("bmPlanes", wintypes.WORD), ("bmBitsPixel", wintypes.WORD),
            ("bmBits", ctypes.c_void_p),
        ]

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    shell32.ExtractIconExW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int,
        ctypes.POINTER(wintypes.HICON), ctypes.POINTER(wintypes.HICON),
        ctypes.c_uint,
    ]
    user32.GetIconInfo.argtypes = [wintypes.HICON, ctypes.POINTER(ICONINFO)]
    gdi32.GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
    gdi32.GetDIBits.argtypes = [
        wintypes.HDC, wintypes.HBITMAP, ctypes.c_uint, ctypes.c_uint,
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint,
    ]

    count = shell32.ExtractIconExW(str(path), -1, None, None, 0)
    if not count:
        return None
    icons = (wintypes.HICON * count)()
    shell32.ExtractIconExW(str(path), 0, icons, None, count)

    info = ICONINFO()
    user32.GetIconInfo(icons[0], byref(info))
    bmp = BITMAP()
    gdi32.GetObjectW(info.hbmColor, ctypes.sizeof(bmp), byref(bmp))
    w, h = bmp.bmWidth, bmp.bmHeight
    header = BITMAPINFOHEADER()
    header.biSize = ctypes.sizeof(header)
    header.biWidth, header.biHeight = w, -h
    header.biPlanes, header.biBitCount = 1, 32
    buf = (wintypes.DWORD * (w * h))()
    hdc = user32.GetDC(0)
    gdi32.GetDIBits(hdc, info.hbmColor, 0, h, buf, byref(header), 0)
    user32.ReleaseDC(0, hdc)

    value = buf[(h // 2) * w + w // 2]
    return f"#{(value >> 16) & 0xFF:02X}{(value >> 8) & 0xFF:02X}{value & 0xFF:02X}"


def main() -> int:
    icon_src = ASSETS / "logo.ico"
    if not icon_src.is_file():
        print("缺少 assets/logo.ico，请先运行 python tools/render_logo.py")
        return 1

    # 关键：把图标放到纯 ASCII 临时目录，绕开 PyInstaller 的非 ASCII 路径缺陷
    tmp_dir = Path(tempfile.mkdtemp(prefix="ffkit_build_"))
    icon_ascii = tmp_dir / "logo.ico"
    shutil.copy2(icon_src, icon_ascii)
    print(f"图标临时副本（ASCII 路径）：{icon_ascii}")

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--onedir", "--windowed",
        "--name", NAME,
        "--icon", str(icon_ascii),
        # 路径一律给绝对值：spec 生成在临时目录，相对路径会被按 spec 所在目录解析
        "--add-data", f"{ROOT / 'app'}{os.pathsep}app",
        "--add-data", f"{ROOT / 'assets'}{os.pathsep}assets",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        # spec 生成到临时目录：避免覆盖仓库里的 FFKit.spec 并写入本机绝对路径
        "--specpath", str(tmp_dir),
        str(ROOT / "main.py"),
    ]
    print("执行：", " ".join(cmd[:6]), "...")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode != 0:
        print(f"打包失败，退出码 {result.returncode}")
        return result.returncode

    exe = ROOT / "dist" / NAME / f"{NAME}.exe"
    print(f"\n产物：{exe}  ({exe.stat().st_size / 1048576:.1f} MB)" if exe.is_file() else "\n未找到 exe")

    # 验证图标是否真的是我们的 logo
    if exe.is_file() and os.name == "nt":
        want = _probe_icon_color(icon_src)
        got = _probe_icon_color(exe)
        print(f"源图标中心色 = {want}   exe 图标中心色 = {got}")
        print("图标校验：", "通过 ✔" if (want and want == got) else "未通过 ✖")

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
