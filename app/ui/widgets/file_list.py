"""文件列表组件：支持拖拽加入与（可选）拖拽排序（需求七 输入方式 / 5.8）。"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...core.timeparse import human_size
from ...core.paths import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, ext_of

ALL_MEDIA = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS


def collect_media_files(paths: list[str], allowed: set[str] | None = None) -> list[str]:
    """把拖进来的路径展开成文件列表（支持文件夹，递归一层以上）。"""
    allowed = allowed or ALL_MEDIA
    result: list[str] = []
    for path in paths:
        if not path:
            continue
        if os.path.isfile(path):
            # 直接拖入的文件同样要按扩展名过滤，否则 .txt 之类的文件
            # 会进列表、到执行时才被静默跳过，用户只看到"没有可执行的任务"
            if ext_of(path) in allowed:
                result.append(path)
        elif os.path.isdir(path):
            try:
                for root, _dirs, files in os.walk(path):
                    for name in sorted(files):
                        full = os.path.join(root, name)
                        if ext_of(full) in allowed:
                            result.append(full)
            except OSError:
                continue
    # 去重但保序
    seen: set[str] = set()
    unique: list[str] = []
    for path in result:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


class FileDropList(QListWidget):
    """接受外部文件拖入；可选支持内部拖拽排序。"""

    filesDropped = Signal(list)
    orderChanged = Signal()
    # 右键菜单动作（问题 6）
    requestRemove = Signal()
    requestClear = Signal()
    requestOpenDir = Signal(str)
    requestCopyPath = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        allowed_exts: set[str] | None = None,
        allow_reorder: bool = False,
        height: int = 150,
    ) -> None:
        super().__init__(parent)
        self.allowed_exts = allowed_exts
        self.allow_reorder = allow_reorder
        self.setObjectName("FileList")
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.setMinimumHeight(120)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setTextElideMode(Qt.ElideMiddle)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        if allow_reorder:
            self.setDragDropMode(QAbstractItemView.InternalMove)
            self.setDefaultDropAction(Qt.MoveAction)
            self.setDragEnabled(True)
            self.setDropIndicatorShown(True)  # 拖拽排序时显示插入位置（问题 6）
            self.model().rowsMoved.connect(lambda *_: self.orderChanged.emit())
        else:
            self.setDragDropMode(QAbstractItemView.DropOnly)
            self.setDragEnabled(False)
        self.setProperty("dragActive", "false")
        self.customContextMenuRequested.connect(self._on_context_menu)

    # ---------- 拖拽 ----------
    def _set_active(self, active: bool) -> None:
        self.setProperty("dragActive", "true" if active else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            self._set_active(True)
            event.acceptProposedAction()
            return
        if self.allow_reorder:
            super().dragEnterEvent(event)
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        if self.allow_reorder:
            super().dragMoveEvent(event)
        else:
            event.ignore()

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_active(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_active(False)
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            files = collect_media_files(paths, self.allowed_exts)
            if files:
                self.filesDropped.emit(files)
            event.acceptProposedAction()
            return
        if self.allow_reorder:
            super().dropEvent(event)
        else:
            event.ignore()

    # ---------- 右键菜单（问题 6） ----------
    def _on_context_menu(self, pos) -> None:
        item = self.itemAt(pos)
        menu = QMenu(self)
        selected = self.selectedItems()

        if item is not None:
            if item not in selected:
                self.setCurrentItem(item)
                selected = [item]
            count = len(selected)
            act_remove = QAction(f"移除选中的 {count} 个" if count > 1 else "移除", self)
            act_remove.triggered.connect(self.requestRemove.emit)
            menu.addAction(act_remove)

            act_open = QAction("打开所在目录", self)
            act_open.triggered.connect(
                lambda: self.requestOpenDir.emit(self.current_path())
            )
            menu.addAction(act_open)

            act_copy = QAction("复制文件路径", self)
            act_copy.triggered.connect(self._copy_selected_paths)
            menu.addAction(act_copy)
            menu.addSeparator()

        act_all = QAction("全选", self)
        act_all.triggered.connect(self.selectAll)
        menu.addAction(act_all)

        act_clear = QAction("清空列表", self)
        act_clear.setEnabled(bool(self.count()))
        act_clear.triggered.connect(self.requestClear.emit)
        menu.addAction(act_clear)

        menu.exec(self.viewport().mapToGlobal(pos))

    def _copy_selected_paths(self) -> None:
        paths = self.selected_paths() or ([self.current_path()] if self.current_path() else [])
        if paths:
            QGuiApplication.clipboard().setText("\n".join(paths))

    # ---------- 数据操作 ----------
    def paths(self) -> list[str]:
        return [self.item(i).data(Qt.UserRole) or self.item(i).text() for i in range(self.count())]

    def add_paths(self, paths: list[str]) -> int:
        existing = {os.path.normcase(os.path.abspath(p)) for p in self.paths()}
        added = 0
        for path in paths:
            key = os.path.normcase(os.path.abspath(path))
            if key in existing:
                continue
            existing.add(key)
            item = QListWidgetItem(os.path.basename(path))
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.addItem(item)
            added += 1
        return added

    def remove_selected(self) -> int:
        rows = sorted({self.row(i) for i in self.selectedItems()}, reverse=True)
        for row in rows:
            self.takeItem(row)
        return len(rows)

    def total_size(self) -> int:
        total = 0
        for path in self.paths():
            try:
                total += os.path.getsize(path)
            except OSError:
                pass
        return total

    def clear_all(self) -> None:
        self.clear()

    def selected_paths(self) -> list[str]:
        return [i.data(Qt.UserRole) or i.text() for i in self.selectedItems()]

    def current_path(self) -> str:
        item = self.currentItem()
        if item is None:
            return ""
        return item.data(Qt.UserRole) or item.text()

    def select_first(self) -> None:
        if self.count() and self.currentRow() < 0:
            self.setCurrentRow(0)


class FileListGroup(QWidget):
    """文件列表 + 添加/移除/清空按钮的整块。"""

    filesChanged = Signal()
    currentChanged = Signal(str)
    filesDropped = Signal(list)
    # 超出数量上限时被拒绝（拒绝条数, 当前已有条数, 上限）
    limitReached = Signal(int, int, int)
    # 低于数量下限（仅作提示，不阻止添加）
    underLimit = Signal(int, int)

    def __init__(
        self,
        title: str = "文件列表",
        allowed_exts: set[str] | None = None,
        allow_reorder: bool = False,
        hint: str = "把文件或文件夹拖到这里；支持批量",
        height: int = 150,
        max_files: int = 0,
        min_files: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.allowed_exts = allowed_exts or ALL_MEDIA
        self.allow_reorder = allow_reorder
        self.hint_text = hint
        # max_files = 0 表示不限；min_files 只用于文案提示
        self.max_files = max(0, int(max_files or 0))
        self.min_files = max(0, int(min_files or 0))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.list = FileDropList(self, self.allowed_exts, allow_reorder, height=height)
        layout.addWidget(self.list, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.btn_add = QPushButton("添加文件…")
        self.btn_add.setObjectName("Primary")
        self.btn_remove = QPushButton("移除选中")
        self.btn_clear = QPushButton("清空列表")
        buttons.addWidget(self.btn_add)
        for btn in (self.btn_remove, self.btn_clear):
            btn.setObjectName("Flat")
            buttons.addWidget(btn)
        buttons.addStretch(1)
        self.count_label = QLabel("0 个文件")
        self.count_label.setObjectName("SectionTitle")
        buttons.addWidget(self.count_label)
        layout.addLayout(buttons)

        hint_text = hint or "把文件或文件夹拖到这里"
        # 提示里已经写了"拖拽调整顺序"之类的话就不要再追加一遍（合并页教训）
        if allow_reorder and "拖拽" not in hint_text:
            hint_text = hint_text + "（拖拽条目可调整顺序）"
        self.hint_label = QLabel(hint_text)
        self.hint_label.setObjectName("Hint")
        self.hint_label.setWordWrap(True)
        layout.addWidget(self.hint_label)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_remove.clicked.connect(self._on_remove)
        self.btn_clear.clicked.connect(self._on_clear)
        self.list.filesDropped.connect(self._on_dropped)
        self.list.itemSelectionChanged.connect(self._on_selection)
        self.list.orderChanged.connect(self._on_order)
        # 右键菜单（问题 6）
        self.list.requestRemove.connect(self._on_remove)
        self.list.requestClear.connect(self._on_clear)
        self.list.requestOpenDir.connect(self.open_directory)
        self._update_count()

    # ---------- 槽 ----------
    def _on_add(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        start = ""
        if self.list.count():
            start = os.path.dirname(self.list.paths()[-1])
        patterns = _patterns(self.allowed_exts)
        files, _ = QFileDialog.getOpenFileNames(self, "选择文件", start, patterns)
        if files:
            self.add_files(files)

    def _on_remove(self) -> None:
        if self.list.remove_selected():
            self._update_count()
            self.filesChanged.emit()
            self.currentChanged.emit(self.list.current_path())

    def _on_clear(self) -> None:
        self.clear_all()

    def _on_dropped(self, files: list[str]) -> None:
        self.filesDropped.emit(files)
        self.add_files(files)

    def _on_selection(self) -> None:
        self.currentChanged.emit(self.list.current_path())

    def _on_order(self) -> None:
        self.filesChanged.emit()

    # ---------- API ----------
    def clear_all(self) -> None:
        """清空整个列表（对外统一入口）。

        必须走这里而不是 ``list.clear_all()``——后者只是 QListWidget.clear()，
        不会发出 filesChanged，主窗口就收不到"列表已空"的通知，
        进度/结果/日志这些残留状态也就清不掉（问题 3）。
        """
        self.list.clear_all()
        self._update_count()
        self.filesChanged.emit()
        self.currentChanged.emit("")

    def add_files(self, files: list[str], silent: bool = False) -> int:
        # 数量上限：只收下还装得下的部分，多余的明确告知用户
        # （需求 2：不能批量处理的页要拦住"误传多传"，否则用户以为全部都会跑，
        #   实际只有第一个被处理，是很典型的"结果与预期不符"）
        if self.max_files:
            room = self.max_files - self.list.count()
            if room <= 0:
                self.limitReached.emit(len(files), self.list.count(), self.max_files)
                return 0
            if len(files) > room:
                self.limitReached.emit(len(files) - room, self.list.count() + room, self.max_files)
                files = files[:room]
        added = self.list.add_paths(files)
        if added:
            self._update_count()
            self.list.select_first()
            if not silent:
                self.filesChanged.emit()
        elif self.min_files:
            self.underLimit.emit(self.list.count(), self.min_files)
        return added

    def over_limit(self, total: int) -> bool:
        """给定批次的合计条数是否超出上限（供拖拽前判断用）。"""
        return bool(self.max_files) and total > self.max_files

    def paths(self) -> list[str]:
        return self.list.paths()

    def current_path(self) -> str:
        return self.list.current_path()

    def open_directory(self, path: str) -> None:
        """打开文件所在目录并选中该文件（问题 6 右键菜单）。"""
        path = path or (self.paths()[0] if self.paths() else "")
        if not path or not os.path.exists(path):
            return
        if os.name == "nt":
            import subprocess

            try:
                subprocess.Popen(["explorer", "/select,", os.path.normpath(path)])
                return
            except OSError:
                pass
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    def _update_count(self) -> None:
        total = self.list.count()
        if not total:
            self.count_label.setText("0 个文件")
            self.list.setProperty("empty", "true")
        else:
            size = self.list.total_size()
            text = f"{total} 个文件 · 合计 {human_size(size)}"
            if self.max_files and total >= self.max_files:
                text += f"（上限 {self.max_files}）"
            self.count_label.setText(text)
            self.list.setProperty("empty", "false")

    def hint_text_full(self) -> str:
        return self.hint_text


def _patterns(exts: set[str]) -> str:
    if not exts:
        return "所有文件 (*.*)"
    groups: list[tuple[str, set[str]]] = [
        ("视频/音频/图片", ALL_MEDIA),
        ("视频", VIDEO_EXTS),
        ("音频", AUDIO_EXTS),
        ("图片", IMAGE_EXTS),
    ]
    parts: list[str] = []
    for label, group in groups:
        hit = sorted(exts & group)
        if hit:
            parts.append(f"{label} ({' '.join('*' + e for e in hit)})")
    parts.append("所有文件 (*.*)")
    return ";;".join(parts)


__all__ = ["ALL_MEDIA", "FileDropList", "FileListGroup", "collect_media_files"]
