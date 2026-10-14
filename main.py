"""FFKit —— ffmpeg 的图形化外壳，入口。

本地、离线、无广告、轻量。
用法：python main.py
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication, QMessageBox

from app import APP_ID, APP_NAME, __version__
from app.config import Config, resolve_initial_theme
from app.resources import app_icon
from app.ui.context import AppContext
from app.ui.main_window import MainWindow
from app.ui.theme import stylesheet
from app.ui.widgets.inputs import install_wheel_guard


def _set_windows_app_id() -> None:
    """声明独立的 AppUserModelID。

    Windows 任务栏按"宿主进程"分组：不设置的话，直接运行时会被归到
    python.exe 名下，任务栏显示的是 Python 图标而不是程序自己的图标。
    必须在创建 QApplication 之前调用。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.Desktop")
    except Exception:
        pass  # 非 Windows 或调用失败都不影响启动


def main() -> int:
    _set_windows_app_id()  # 必须早于 QApplication
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(sys.argv)
    app.setApplicationName(APP_ID)
    app.setApplicationDisplayName(APP_NAME)
    app.setApplicationVersion(__version__)
    # 窗口/任务栏图标（Windows 任务栏读的是应用级图标）
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")  # 跨平台一致的基础风格，QSS 的上色更可控
    app.setFont(QFont("Microsoft YaHei UI", 9))
    # 问题 10：下拉框/数字框禁用滚轮误改（聚焦后才响应）
    install_wheel_guard(app)

    cfg = Config()
    theme = str(cfg.get("theme") or "auto")
    theme = resolve_initial_theme(cfg) if theme not in ("light", "dark") else theme
    app.setStyleSheet(stylesheet(theme))

    ctx = AppContext.create(cfg, theme)
    window = MainWindow(ctx)
    window.apply_theme(theme)
    window.show()

    if not ctx.toolchain.ok:
        QMessageBox.information(
            window,
            "未检测到 ffmpeg",
            "程序可以正常打开，但在安装 ffmpeg 之前无法执行任何处理。\n\n"
            + ctx.install_hint(),
        )

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
