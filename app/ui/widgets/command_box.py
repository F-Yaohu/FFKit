"""命令预览框（P1 只读 / P3 可编辑）。

- 默认只读，作为"高级参数"的唯一出口，同时充当开发期调试工具。
- 勾选"手动编辑"后才可以改；一旦编辑过，执行时按需求 5.7 降级：
  无进度条、不做输出路径校验、仅显示 stderr 尾部日志。
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CommandBox(QGroupBox):
    editedChanged = Signal(bool)

    def __init__(self, expanded: bool = False, parent: QWidget | None = None) -> None:
        super().__init__("命令预览（只读）", parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 14, 10, 10)
        root.setSpacing(6)

        self.view = QPlainTextEdit(self)
        self.view.setObjectName("CommandView")
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.view.setMinimumHeight(56)
        self.view.setMaximumHeight(160)
        root.addWidget(self.view)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.chk_edit = QCheckBox("手动编辑命令后执行（高级）")
        self.chk_edit.setToolTip(
            "勾选后可修改下面的命令。编辑过的命令将无法解析进度，也不再校验输出路径，\n"
            "出错时只显示 stderr 尾部日志。"
        )
        row.addWidget(self.chk_edit)
        row.addStretch(1)
        self.btn_copy = QPushButton("复制命令")
        self.btn_copy.setObjectName("Flat")
        self.btn_reset = QPushButton("还原")
        self.btn_reset.setObjectName("Flat")
        self.btn_reset.setVisible(False)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_reset)
        row_widget = QWidget()
        row_layout = QVBoxLayout(row_widget)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(6)
        row_layout.addLayout(row)
        self.warn = QLabel("⚠ 已手动编辑：执行时降级为「无进度条 + 不校验输出路径 + 仅显示 stderr 尾部日志」")
        self.warn.setObjectName("NoticeWarn")
        self.warn.setWordWrap(True)
        self.warn.setVisible(False)
        row_layout.addWidget(self.warn)
        root.addWidget(row_widget)

        self._generated = ""
        self._dirty = False

        # 可折叠：默认收起，把纵向空间让给预览画面与文件列表（用户实测问题 3）
        self.setCheckable(True)
        self.setTitle("命令预览（点击标题展开 / 收起）")
        self.setChecked(bool(expanded))
        self._body = [self.view, row_widget]
        self.toggled.connect(self._on_collapsed_toggled)

        self.chk_edit.toggled.connect(self._on_toggle_edit)
        self.view.textChanged.connect(self._on_text_changed)
        self.btn_copy.clicked.connect(self.copy)
        self.btn_reset.clicked.connect(self.reset)
        self._on_collapsed_toggled(bool(expanded))

    def _on_collapsed_toggled(self, checked: bool) -> None:
        for widget in self._body:
            widget.setVisible(checked)
        self.setToolTip("点击标题可展开 / 收起命令预览")

    @property
    def expanded(self) -> bool:
        return self.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self.setChecked(bool(expanded))

    # ------------------------------------------------------------------
    def _on_toggle_edit(self, checked: bool) -> None:
        self.view.setReadOnly(not checked)
        self.setTitle("命令预览（可编辑）" if checked else "命令预览（只读）")
        if checked:
            self.view.setFocus()
        else:
            self.reset()
        self._update_warn()
        self.editedChanged.emit(self.is_edited)

    def _on_text_changed(self) -> None:
        if self.view.isReadOnly():
            return
        current = self.view.toPlainText().strip()
        self._dirty = bool(current) and current != self._generated.strip()
        self.btn_reset.setVisible(self._dirty)
        self._update_warn()
        self.editedChanged.emit(self.is_edited)

    def _update_warn(self) -> None:
        self.warn.setVisible(self.is_edited)

    # ---------- API ----------
    @property
    def is_edited(self) -> bool:
        return bool(self.chk_edit.isChecked() and self._dirty)

    def set_command(self, text: str) -> None:
        self._generated = text or ""
        if self.is_edited:
            # 用户正在手改，不要把手改的内容冲掉
            return
        self.view.setPlainText(self._generated)

    def command_text(self) -> str:
        return self.view.toPlainText().strip()

    def reset(self) -> None:
        self._dirty = False
        self.view.setPlainText(self._generated)
        self.btn_reset.setVisible(False)
        self._update_warn()
        self.editedChanged.emit(False)

    def copy(self) -> None:
        text = self.command_text()
        if text:
            QGuiApplication.clipboard().setText(text)

    def set_enabled_state(self, enabled: bool) -> None:
        self.chk_edit.setEnabled(enabled)
        self.btn_copy.setEnabled(enabled)


__all__ = ["CommandBox"]
