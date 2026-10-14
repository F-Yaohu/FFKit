"""用 QtSvg 把 assets 里的 SVG 渲染成 PNG / ICO。

保证位图与矢量源文件完全一致（Qt 的 SVG 渲染支持 stroke-linejoin 圆角描边，
Pillow 手绘无法复现）。

用法：python tools/render_logo.py
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
SCALE = 4  # 超采样倍数


def render(svg_path: str, out_path: str, w: int, h: int) -> None:
    renderer = QSvgRenderer(svg_path)
    if not renderer.isValid():
        raise SystemExit(f"SVG 无效: {svg_path}")

    big = QImage(w * SCALE, h * SCALE, QImage.Format_ARGB32)
    big.fill(QColor(0, 0, 0, 0))

    painter = QPainter(big)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setRenderHint(QPainter.TextAntialiasing, True)
    renderer.render(painter, QRectF(0, 0, w * SCALE, h * SCALE))
    painter.end()

    small = big.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    if not small.save(out_path):
        raise SystemExit(f"保存失败: {out_path}")


def main() -> int:
    app = QGuiApplication(sys.argv)

    render(
        os.path.join(ASSETS, "logo.svg"),
        os.path.join(ASSETS, "logo.png"),
        512,
        512,
    )
    render(
        os.path.join(ASSETS, "logo-horizontal.svg"),
        os.path.join(ASSETS, "logo-horizontal.png"),
        960,
        320,
    )

    from PIL import Image

    icon = Image.open(os.path.join(ASSETS, "logo.png"))
    icon.save(
        os.path.join(ASSETS, "logo.ico"),
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )

    for name in ("logo.png", "logo-horizontal.png", "logo.ico"):
        p = os.path.join(ASSETS, name)
        print(f"{name}  {os.path.getsize(p)} bytes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
