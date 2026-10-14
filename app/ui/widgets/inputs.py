"""下拉框 / 数字输入框的滚轮防护（问题 10）。

问题：参数区可滚动，用户滚动页面时鼠标经过下拉框或数字框，会**静默改掉参数**，
而且往往很久之后才发现。

策略（需求"禁用滚轮事件，或加焦点判断"）：
- 未聚焦时：滚轮事件直接吞掉，不做任何修改，交还给父级滚动区域继续滚动；
- 已聚焦（用户明确点进来过）时：保留滚轮调节，这是"有意为之"的操作。
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject
from PySide6.QtWidgets import (
    QAbstractScrollArea,
    QAbstractSpinBox,
    QApplication,
    QComboBox,
    QSlider,
)


class WheelGuard(QObject):
    """装在 QApplication 上的全局事件过滤器。"""

    def eventFilter(self, obj, event) -> bool:  # noqa: N802
        if event.type() != QEvent.Wheel:
            return False
        if not isinstance(obj, (QComboBox, QAbstractSpinBox, QSlider)):
            return False
        if obj.hasFocus():
            return False
        # 把滚轮事件转交给外层滚动区域，这样"滚动页面"的手感不变，
        # 但参数不会被误改。
        area = obj.parent()
        while area is not None and not isinstance(area, QAbstractScrollArea):
            area = area.parent()
        if area is not None:
            target = area.viewport() or area
            QApplication.sendEvent(target, event)
        return True


_guard: WheelGuard | None = None


def install_wheel_guard(app) -> WheelGuard:
    """在 QApplication 上安装一次（重复调用安全）。"""
    global _guard
    if _guard is None:
        _guard = WheelGuard(app)
        app.installEventFilter(_guard)
    return _guard


__all__ = ["WheelGuard", "install_wheel_guard"]
