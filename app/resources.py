"""应用资源定位与图标加载。

同时支持两种运行环境：
- 源码运行：资源在项目根目录下的 assets/
- PyInstaller 打包：资源被解压到 sys._MEIPASS 下的 assets/

对外只暴露三个函数：app_icon() / logo_pixmap() / asset_path()。
图标缺失时静默降级，绝不因缺图导致程序打不开。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ICON_FILES = ("logo.ico", "logo.png", "logo.svg")


def app_root() -> Path:
    """资源根目录。打包后为 PyInstaller 的临时解压目录。"""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        return Path(base)
    frozen = getattr(sys, "frozen", False)
    if frozen:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def asset_path(name: str) -> Path:
    """返回 assets 目录下某个文件的绝对路径（不保证存在）。"""
    return app_root() / "assets" / name


def first_existing_asset(names: tuple[str, ...] = ICON_FILES) -> Path | None:
    for name in names:
        p = asset_path(name)
        if p.is_file():
            return p
    return None


def app_icon():
    """返回应用图标 QIcon；图标缺失时返回空 QIcon（不抛异常）。"""
    from PySide6.QtGui import QIcon

    path = first_existing_asset()
    if path is None:
        return QIcon()
    icon = QIcon(str(path))
    if icon.isNull() and path.suffix.lower() == ".svg":
        # 极少数环境 QIcon 不认 SVG，退化为位图渲染
        pixmap = logo_pixmap(256)
        if not pixmap.isNull():
            return QIcon(pixmap)
    return icon


def logo_pixmap(size: int = 96):
    """返回指定尺寸的方形 logo 位图；缺失时返回空 QPixmap。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QPixmap

    path = first_existing_asset(("logo.png", "logo.svg", "logo.ico"))
    if path is None:
        return QPixmap()

    if path.suffix.lower() == ".svg":
        rendered = _render_svg(path, size)
        if not rendered.isNull():
            return rendered

    pixmap = QPixmap(str(path))
    if pixmap.isNull():
        return pixmap
    return pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _render_svg(path: Path, size: int):
    """把 SVG 按 4 倍超采样渲染成位图，保证与矢量一致。"""
    try:
        from PySide6.QtCore import QRectF, Qt
        from PySide6.QtGui import QColor, QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
    except Exception:
        from PySide6.QtGui import QPixmap

        return QPixmap()

    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        from PySide6.QtGui import QPixmap

        return QPixmap()

    scale = 4
    image = QImage(size * scale, size * scale, QImage.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
    painter.end()

    from PySide6.QtGui import QPixmap

    return QPixmap.fromImage(
        image.scaled(size, size, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    )


def assets_available() -> bool:
    """资源目录是否随程序一起分发（打包校验用）。"""
    return first_existing_asset() is not None
