"""文件信息面板（P1）：选中文件后展示 ffprobe 解析的媒体信息。

这是"该不该压""能不能无损切"的判断前提，所以放在文件列表下面常驻显示。

本轮新增：
- 汇总视图（问题 13）：合并等多文件场景展示"共 N 个 / 合计时长 / 合计体积 + 一致性结论"
- 复制能力（新增 4）：双击/右键复制单项，一键复制全部
- 样式统一（问题 18）：固定行高、统一字体与列宽，各页一致
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMenu,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.timeparse import human_size

ROW_HEIGHT = 26


class InfoPanel(QGroupBox):
    def __init__(self, title: str = "文件信息", parent: QWidget | None = None) -> None:
        super().__init__(title, parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 14, 10, 10)
        layout.setSpacing(6)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["项目", "值"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(ROW_HEIGHT)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setSelectionBehavior(QAbstractItemView.SelectItems)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setWordWrap(False)
        self.table.setMinimumHeight(96)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.itemDoubleClicked.connect(lambda item: self._copy_item(item))
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setFixedHeight(ROW_HEIGHT)
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        actions.setSpacing(6)
        actions.addStretch(1)
        self.btn_copy_row = QPushButton("复制选中项")
        self.btn_copy_all = QPushButton("复制全部")
        for btn in (self.btn_copy_row, self.btn_copy_all):
            btn.setObjectName("Flat")
            actions.addWidget(btn)
        layout.addLayout(actions)

        self.note = QLabel("选择文件后显示信息")
        self.note.setObjectName("Notice")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)

        self.btn_copy_row.clicked.connect(self._copy_selected)
        self.btn_copy_all.clicked.connect(self.copy_all)

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------
    def show_paths(self, paths: list[str], prober) -> None:
        """根据当前选择刷新面板（只探测第一个文件，避免批量时卡界面）。"""
        if not paths:
            self.set_rows([("状态", "未选择文件")], "")
            return
        first = paths[0]
        info = prober.get(first) if prober else None
        rows = info.summary_rows() if info else [("状态", "无法解析")]
        note = ""
        if len(paths) > 1:
            total = 0
            for path in paths:
                try:
                    total += os.path.getsize(path)
                except OSError:
                    pass
            note = f"已选择 {len(paths)} 个文件，合计 {human_size(total)}；上表为第一个文件的信息。"
        elif info and not info.ok:
            note = f"⚠ {info.error}"

        if info and info.ok and info.has_video:
            if info.estimated_bit_rate() and info.estimated_bit_rate() < 900_000:
                note = (note + " " if note else "") + "提示：该视频码率偏低，继续压缩收益有限。"
        self.set_rows(rows, note.strip())

    def set_rows(self, rows: list[tuple[str, str]], note: str = "") -> None:
        """直接填表 —— 合并页等信息汇总视图用（问题 13）。"""
        self.table.setRowCount(len(rows))
        for index, (key, value) in enumerate(rows):
            key_item = QTableWidgetItem(str(key))
            value_item = QTableWidgetItem(str(value))
            value_item.setToolTip(str(value))
            self.table.setItem(index, 0, key_item)
            self.table.setItem(index, 1, value_item)
            self.table.setRowHeight(index, ROW_HEIGHT)
        self.note.setText(note or "")
        self.note.setVisible(bool(note))

    # 兼容旧调用名
    _fill = set_rows

    # ------------------------------------------------------------------
    # 复制（新增 4）
    # ------------------------------------------------------------------
    def _on_context_menu(self, pos) -> None:
        menu = QMenu(self)
        item = self.table.itemAt(pos)
        if item is not None:
            act_value = QAction("复制该项的值", self)
            act_value.triggered.connect(lambda: self._copy_item(
                self.table.item(item.row(), 1) or item))
            menu.addAction(act_value)
            act_line = QAction("复制「项目：值」", self)
            act_line.triggered.connect(lambda: self._copy_line(item.row()))
            menu.addAction(act_line)
            menu.addSeparator()
        act_all = QAction("复制全部信息", self)
        act_all.triggered.connect(self.copy_all)
        menu.addAction(act_all)
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _copy_item(self, item) -> None:
        if item is None:
            return
        value = self.table.item(item.row(), 1)
        QGuiApplication.clipboard().setText(value.text() if value else item.text())

    def _copy_line(self, row: int) -> None:
        key = self.table.item(row, 0)
        value = self.table.item(row, 1)
        if key is None or value is None:
            return
        QGuiApplication.clipboard().setText(f"{key.text()}：{value.text()}")

    def _copy_selected(self) -> None:
        """把选中的行按「项目：值」逐行复制。"""
        rows = sorted({index.row() for index in self.table.selectedIndexes()})
        if not rows:
            self.copy_all()
            return
        lines: list[str] = []
        for row in rows:
            key = self.table.item(row, 0)
            value = self.table.item(row, 1)
            if key is not None and value is not None:
                lines.append(f"{key.text()}：{value.text()}")
        if lines:
            QGuiApplication.clipboard().setText("\n".join(lines))

    def copy_all(self) -> None:
        lines: list[str] = []
        for row in range(self.table.rowCount()):
            key = self.table.item(row, 0)
            value = self.table.item(row, 1)
            if key is not None and value is not None:
                lines.append(f"{key.text()}：{value.text()}")
        if self.note.text():
            lines.append(self.note.text())
        if lines:
            QGuiApplication.clipboard().setText("\n".join(lines))

    def clear(self) -> None:
        self.table.setRowCount(0)
        self.note.setText("")
        self.note.setVisible(False)


__all__ = ["InfoPanel"]
