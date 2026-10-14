"""日志区：默认折叠、可点开、可全选复制、限制最大行数（需求七 日志区）。

保留最近 N 条是关键 —— 长时间任务（压缩、合并）会产生海量 stderr，
不截断会把内存吃干净。
"""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class LogPanel(QWidget):
    def __init__(self, max_lines: int = 2000, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.max_lines = max(200, int(max_lines or 2000))

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(6)
        # 行数直接写进按钮文案 —— 点它就能展开（问题 20）
        self.toggle = QPushButton("▸ 运行日志（0 行）")
        self.toggle.setObjectName("Flat")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(False)
        self.toggle.setToolTip("点击展开/收起运行日志（Ctrl+L）")
        header.addWidget(self.toggle)
        header.addStretch(1)
        self.btn_export = QPushButton("导出日志…")
        self.btn_export.setObjectName("Flat")
        self.btn_export.setToolTip("把完整日志另存为文本文件")
        header.addWidget(self.btn_export)
        self.btn_copy = QPushButton("复制全部")
        self.btn_copy.setObjectName("Flat")
        self.btn_clear = QPushButton("清空")
        self.btn_clear.setObjectName("Flat")
        header.addWidget(self.btn_copy)
        header.addWidget(self.btn_clear)
        root.addLayout(header)

        self.view = QPlainTextEdit(self)
        self.view.setObjectName("LogView")
        self.view.setReadOnly(True)
        self.view.setUndoRedoEnabled(False)
        self.view.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.view.setMinimumHeight(130)
        self.view.setMaximumBlockCount(self.max_lines)  # Qt 原生截断，不会内存爆
        self.view.setPlaceholderText("执行 ffmpeg 时的输出会显示在这里")
        root.addWidget(self.view)
        self.view.setVisible(False)

        self.toggle.toggled.connect(self._on_toggle)
        self.btn_copy.clicked.connect(self.copy_all)
        self.btn_clear.clicked.connect(self.clear)
        self.btn_export.clicked.connect(self.export_to_file)

    # ------------------------------------------------------------------
    def _on_toggle(self, checked: bool) -> None:
        self.view.setVisible(checked)
        self._refresh_toggle_text()
        if checked:
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def _refresh_toggle_text(self) -> None:
        arrow = "▾" if self.toggle.isChecked() else "▸"
        self.toggle.setText(f"{arrow} 运行日志（{self.view.blockCount()} 行）")

    @property
    def expanded(self) -> bool:
        return self.toggle.isChecked()

    def set_expanded(self, expanded: bool) -> None:
        self.toggle.setChecked(bool(expanded))

    def append(self, line: str) -> None:
        if not line:
            return
        self.view.appendPlainText(line)
        self._refresh_toggle_text()
        if self.expanded:
            self.view.verticalScrollBar().setValue(self.view.verticalScrollBar().maximum())

    def append_lines(self, lines: list[str]) -> None:
        for line in lines:
            self.append(line)

    def text(self) -> str:
        return self.view.toPlainText()

    def copy_all(self) -> None:
        text = self.text()
        if not text:
            return
        QGuiApplication.clipboard().setText(text)

    def export_to_file(self) -> None:
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        text = self.text()
        if not text.strip():
            QMessageBox.information(self, "导出日志", "当前没有日志内容。")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出运行日志", "ffkit_log.txt", "文本文件 (*.txt);;所有文件 (*.*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(text)
        except OSError as exc:
            QMessageBox.warning(self, "导出失败", f"无法写入文件：{exc}")
            return
        QMessageBox.information(self, "导出日志", f"已导出到：\n{path}")

    def clear(self) -> None:
        self.view.clear()
        self._refresh_toggle_text()

    def set_max_lines(self, value: int) -> None:
        self.max_lines = max(200, int(value or 2000))
        self.view.setMaximumBlockCount(self.max_lines)


__all__ = ["LogPanel"]
