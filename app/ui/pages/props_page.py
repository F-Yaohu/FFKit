"""封面 / 文件属性 —— 实测文档"新增 1""新增 2"。

两件事放在一页，因为它们的实现方式完全一致：都是 ``-c copy`` 只改容器头，
音视频流不动，秒级完成：

- **设置 / 更换 / 移除封面**：音频写 ID3 attached_pic，视频加一条 attached_pic 图片流
- **编辑文件属性（元数据）**：标题 / 艺术家 / 专辑 / 年份 / 流派 / 电视季集号等
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core.paths import AUDIO_EXTS, VIDEO_EXTS, ext_of
from ...core.spec import OP_EDIT_METADATA, OP_SET_COVER, OperationSpec
from .base_page import BasePage

MODES = [
    ("cover", "设置 / 更换封面图"),
    ("meta", "编辑文件属性（元数据）"),
]

CONTAINER_CHOICES = [
    ("same", "与源文件相同（推荐）"),
    (".mp4", "MP4 (.mp4)"),
    (".mkv", "MKV (.mkv)"),
    (".mp3", "MP3 (.mp3)"),
    (".m4a", "M4A (.m4a)"),
    (".flac", "FLAC (.flac)"),
]

# (元数据键, 界面标签, 提示)
TAG_FIELDS: list[tuple[str, str, str]] = [
    ("title", "标题", "歌曲名 / 影片名"),
    ("artist", "艺术家", "歌手 / 演员"),
    ("album", "专辑", "专辑名"),
    ("album_artist", "专辑艺术家", "常用于合辑"),
    ("genre", "流派", "如 Pop / Rock"),
    ("date", "年份 / 日期", "如 2026 或 2026-10-13"),
    ("comment", "备注", "任意说明文字"),
    ("show", "电视节目名", "剧集专用"),
    ("season_number", "季", "数字，如 2"),
    ("episode_id", "集", "数字，如 15"),
]


class PropsPage(BasePage):
    TITLE = "属性"
    USES_PREVIEW = False
    ALLOWED_EXTS = VIDEO_EXTS | AUDIO_EXTS
    FILE_HINT = "拖入视频/音频文件（支持批量），同一批文件会应用相同的封面或属性"
    LIST_HEIGHT = 150

    def _build_params(self, layout: QVBoxLayout) -> None:
        # ---------------- 操作类型 ----------------
        self.add_group_title(layout, "操作类型")
        head = QFormLayout()
        head.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.setSpacing(8)
        self.cb_mode = QComboBox()
        for value, label in MODES:
            self.cb_mode.addItem(label, value)
        head.addRow("要做的操作", self.cb_mode)
        layout.addLayout(head)

        # ---------------- 封面组 ----------------
        self.add_separator(layout)
        self.cover_title = self.add_group_title(layout, "封面图")
        cover_form = QFormLayout()
        cover_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        cover_form.setSpacing(8)
        self._cover_form = cover_form

        pic_row = QHBoxLayout()
        pic_row.setSpacing(6)
        self.ed_cover = QLineEdit()
        self.ed_cover.setPlaceholderText("选择一张 JPG / PNG 图片")
        self.ed_cover.setReadOnly(True)
        self.btn_pick_cover = QPushButton("选择图片…")
        pic_row.addWidget(self.ed_cover, 1)
        pic_row.addWidget(self.btn_pick_cover)
        self.lbl_cover_row = _right_label("封面文件")
        cover_form.addRow(self.lbl_cover_row, self.wrap_row(pic_row))

        self.lbl_thumb = QLabel("尚无封面")
        self.lbl_thumb.setAlignment(Qt.AlignCenter)
        self.lbl_thumb.setMinimumHeight(96)
        self.lbl_thumb.setObjectName("Thumb")
        self.lbl_thumb_row = _right_label("预览")
        cover_form.addRow(self.lbl_thumb_row, self.lbl_thumb)

        self.chk_remove_cover = QCheckBox("改为「移除已有封面」")
        self.chk_remove_cover.setToolTip("勾选后不写新封面，而是把文件里已有的封面图删掉")
        cover_form.addRow("", self.chk_remove_cover)

        self.cb_container = QComboBox()
        for value, label in CONTAINER_CHOICES:
            self.cb_container.addItem(label, value)
        self.lbl_container_row = _right_label("输出格式")
        cover_form.addRow(self.lbl_container_row, self.cb_container)
        layout.addLayout(cover_form)

        # ---------------- 元数据组 ----------------
        self.add_separator(layout)
        self.meta_title = self.add_group_title(layout, "文件属性")
        meta_form = QFormLayout()
        meta_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        meta_form.setSpacing(6)
        self._meta_form = meta_form
        self.edits: dict[str, QLineEdit] = {}
        self._meta_rows: list[tuple[str, QLabel | None, QWidget]] = []
        for key, label_text, tip in TAG_FIELDS:
            edit = QLineEdit()
            edit.setPlaceholderText(tip)
            label = _right_label(label_text)
            meta_form.addRow(label, edit)
            self.edits[key] = edit
            self._meta_rows.append((key, label, edit))

        self.chk_clear_empty = QCheckBox("把留空的项设为「清除」")
        self.chk_clear_empty.setToolTip(
            "默认留空表示不修改该字段；勾选后，留空的字段会被从文件里删除"
        )
        # 注意：这一行在布局里没有左侧标签，占位必须用 None。
        # 早期版本这里塞了一个 QLabel("")——它没有父窗口也没进布局，
        # 一旦 setVisible(True) 就会变成一个独立的黑色空窗口（用户实测反馈）。
        self._meta_rows.append(("__extra__", None, self.chk_clear_empty))
        meta_form.addRow("", self.chk_clear_empty)

        btns = QHBoxLayout()
        btns.setSpacing(6)
        self.btn_read = QPushButton("读取当前属性")
        self.btn_read.setToolTip("从第一个文件读出已有属性填入下面的输入框")
        self.btn_clear_fields = QPushButton("清空输入")
        btns.addWidget(self.btn_read)
        btns.addWidget(self.btn_clear_fields)
        btns.addStretch(1)
        self._buttons_widget = self.wrap_row(btns)
        meta_form.addRow("", self._buttons_widget)
        layout.addLayout(meta_form)

        self.lbl_hint = self.hint_label("")
        layout.addWidget(self.lbl_hint)

        # ---------------- 信号 ----------------
        self.cb_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.btn_pick_cover.clicked.connect(self._pick_cover)
        self.chk_remove_cover.toggled.connect(self._on_cover_toggled)
        self.cb_container.currentIndexChanged.connect(self.schedule_preview)
        self.btn_read.clicked.connect(self._read_current_tags)
        self.btn_clear_fields.clicked.connect(self._clear_fields)
        for edit in self.edits.values():
            edit.textChanged.connect(self.schedule_preview)
        self.chk_clear_empty.toggled.connect(self.schedule_preview)
        # 这些输入会改变 can_run() 的结论 → 通知主窗口刷新"开始"按钮
        self.cb_mode.currentIndexChanged.connect(self.runAvailabilityChanged)
        self.chk_remove_cover.toggled.connect(self.runAvailabilityChanged)
        self.chk_clear_empty.toggled.connect(self.runAvailabilityChanged)
        for edit in self.edits.values():
            edit.textChanged.connect(self.runAvailabilityChanged)
        self._on_mode_changed()

    # ------------------------------------------------------------------
    @staticmethod
    def _right_label(text: str) -> QLabel:
        return _right_label(text)

    def _on_mode_changed(self) -> None:
        cover = (self.cb_mode.currentData() or "cover") == "cover"
        for widget in (self.cover_title, self.lbl_cover_row, self.lbl_thumb_row,
                       self.lbl_thumb, self.chk_remove_cover, self.lbl_container_row,
                       self.cb_container):
            widget.setVisible(cover)
        pic_row = self.ed_cover.parentWidget()
        if pic_row is not None:
            pic_row.setVisible(cover)

        self.meta_title.setVisible(not cover)
        for key, label, widget in self._meta_rows:
            if label is not None:      # "__extra__" 行没有左侧标签，见上面的注释
                label.setVisible(not cover)
            widget.setVisible(not cover)
        self._buttons_widget.setVisible(not cover)

        self._on_cover_toggled()
        self.lbl_hint.setText(
            "封面与属性都用无损方式写入（-c copy）：只改文件头，音视频流不重编码。"
        )
        self.schedule_preview()

    def _on_cover_toggled(self) -> None:
        remove = self.chk_remove_cover.isChecked()
        for widget in (self.ed_cover, self.btn_pick_cover, self.lbl_thumb):
            widget.setEnabled(not remove)
        self.schedule_preview()

    def _pick_cover(self) -> None:
        start = os.path.dirname(self.ed_cover.text()) if self.ed_cover.text() else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "选择封面图片", start,
            "图片 (*.jpg *.jpeg *.png *.bmp *.webp);;所有文件 (*.*)",
        )
        if not path:
            return
        self.ed_cover.setText(path)
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.lbl_thumb.setText("无法预览该图片")
        else:
            self.lbl_thumb.setPixmap(
                pixmap.scaled(180, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )
        self.schedule_preview()

    def _read_current_tags(self) -> None:
        path = self.current_first()
        info = self.probe(path)
        if not info or not info.ok:
            self.lbl_hint.setText("请先选择一个文件。")
            return
        tags = {key.lower(): value for key, value in (info.tags or {}).items()}
        filled = 0
        for key, edit in self.edits.items():
            value = tags.get(key, "")
            edit.setText(value)
            if value:
                filled += 1
        extra = f"；检测到{'有' if info.has_cover else '没有'}封面图" if info.has_video or info.has_audio else ""
        self.lbl_hint.setText(f"已从 {os.path.basename(path)} 读取 {filled} 项属性{extra}。")

    def _clear_fields(self) -> None:
        for edit in self.edits.values():
            edit.clear()
        self.chk_clear_empty.setChecked(False)

    # ------------------------------------------------------------------
    def can_run(self) -> tuple[bool, str]:
        if (self.cb_mode.currentData() or "cover") == "cover":
            if self.chk_remove_cover.isChecked():
                return True, ""
            cover = self.ed_cover.text().strip()
            if not cover:
                return False, "请先选择一张封面图片（或勾选「移除已有封面」）"
            if not os.path.isfile(cover):
                return False, f"封面图片不存在：{cover}"
            return True, ""
        if not self._collect_tags()[0]:
            return False, "请至少填写一项属性"
        return True, ""

    def _collect_tags(self) -> tuple[dict[str, str], list[str]]:
        tags: dict[str, str] = {}
        clear: list[str] = []
        clear_empty = self.chk_clear_empty.isChecked()
        for key, edit in self.edits.items():
            value = edit.text().strip()
            if value:
                tags[key] = value
            elif clear_empty:
                clear.append(key)
        return tags, clear

    def build_specs(self) -> list[OperationSpec]:
        paths = self.paths()
        if not paths:
            return []
        mode = self.cb_mode.currentData() or "cover"
        specs: list[OperationSpec] = []

        if mode == "cover":
            choice = self.cb_container.currentData() or "same"
            remove = self.chk_remove_cover.isChecked()
            cover = self.ed_cover.text().strip()
            for src in paths:
                info = self.probe(src)
                if info is not None and not info.ok:
                    continue
                ext = ext_of(src) if choice == "same" else choice
                if not ext:
                    ext = ".mp4"
                out = self.make_output_path(src, "cover", ext)
                specs.append(OperationSpec(
                    op=OP_SET_COVER,
                    inputs=[src],
                    output=out,
                    params={"cover": cover, "remove": remove},
                    label=os.path.basename(src),
                    info=info,
                ))
            return specs

        tags, clear = self._collect_tags()
        for src in paths:
            info = self.probe(src)
            if info is not None and not info.ok:
                continue
            out = self.make_output_path(src, "tagged", ext_of(src))
            specs.append(OperationSpec(
                op=OP_EDIT_METADATA,
                inputs=[src],
                output=out,
                params={"tags": tags, "clear": clear},
                label=os.path.basename(src),
                info=info,
            ))
        return specs


def _right_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return label


__all__ = ["PropsPage", "TAG_FIELDS"]
