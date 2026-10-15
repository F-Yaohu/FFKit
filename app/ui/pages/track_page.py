"""多轨封装 —— 实测文档"新增 3"（视频 + 外部音频/字幕合并，多音轨/字幕）。

与「合并」页的区别：
- 合并页是把**同类**文件首尾**拼接**（concat），输出时长变长；
- 本页是把**不同类型**的轨道**封装**进同一个容器（mux），输出时长不变，
  相当于"给电影挂上配音轨和字幕"。

实现全部走 ``-c copy``：视频与兼容音轨不重编码，秒级完成、无画质损失；
只有容器装不下的编码（比如往 MP4 里塞 FLAC / srt）才自动转码，并在警告里说明。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ...core.paths import AUDIO_EXTS, SUBTITLE_EXTS, VIDEO_EXTS, ext_of
from ...core.prober import list_tracks
from ...core.spec import OP_MUX, OperationSpec
from .base_page import BasePage

# 常用语言（ISO 639-2/B，ffmpeg 的 language 标签用这套）
LANGUAGES = [
    ("und", "未定义"),
    ("chi", "中文"),
    ("eng", "英语"),
    ("jpn", "日语"),
    ("kor", "韩语"),
    ("yue", "粤语"),
    ("tha", "泰语"),
    ("fra", "法语"),
    ("deu", "德语"),
    ("rus", "俄语"),
    ("spa", "西班牙语"),
]

CONTAINER_CHOICES = [
    ("same", "与源文件相同（推荐）"),
    (".mp4", "MP4 (.mp4)"),
    (".mkv", "MKV (.mkv，轨道兼容性最好)"),
]

MEDIA_EXTS = VIDEO_EXTS | AUDIO_EXTS


class TrackPage(BasePage):
    TITLE = "轨道"
    USES_PREVIEW = True
    ALLOWED_EXTS = MEDIA_EXTS
    ALLOW_REORDER = False
    FILE_HINT = "只放一个主文件（视频或音频）；外部音轨/字幕在下方添加"
    LIST_HEIGHT = 110
    # 本页一次只封装一个主文件：多传的文件以前只是被静默忽略，
    # 用户以为"一次处理了全部"，实际只跑了当前选中那个（需求 2）。
    MAX_FILES = 1

    # ------------------------------------------------------------------
    def _build_params(self, layout: QVBoxLayout) -> None:
        # ---------------- 内嵌轨道 ----------------
        self.add_group_title(layout, "内嵌轨道（勾选 = 保留）")
        self.track_table = QTableWidget(0, 3, self)
        self.track_table.setHorizontalHeaderLabels(["轨道", "语言", "标题"])
        self.track_table.verticalHeader().setVisible(False)
        self.track_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.track_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Fixed)
        self.track_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.track_table.setColumnWidth(1, 90)
        self.track_table.setMinimumHeight(96)
        self.track_table.setToolTip("取消勾选的轨道不会写进输出文件")
        layout.addWidget(self.track_table)

        # ---------------- 外部音轨 / 字幕 ----------------
        self.add_group_title(layout, "外部音轨 / 字幕")
        self.extra_table = QTableWidget(0, 5, self)
        self.extra_table.setHorizontalHeaderLabels(["文件", "类型", "语言", "标题", "默认"])
        self.extra_table.verticalHeader().setVisible(False)
        header = self.extra_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for column in (1, 2, 4):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        self.extra_table.setColumnWidth(1, 52)
        self.extra_table.setColumnWidth(2, 110)
        self.extra_table.setColumnWidth(4, 56)
        self.extra_table.setMinimumHeight(96)
        self.extra_table.setToolTip(
            "语言/标题双击可改；勾选「默认」的轨道会被播放器优先播放"
        )
        layout.addWidget(self.extra_table)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.btn_add_extra = QPushButton("添加音频/字幕文件…")
        self.btn_add_extra.setObjectName("Primary")
        self.btn_remove_extra = QPushButton("移除选中")
        buttons.addWidget(self.btn_add_extra)
        buttons.addWidget(self.btn_remove_extra)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        # ---------------- 输出 ----------------
        self.add_group_title(layout, "输出")
        form = QHBoxLayout()
        form.setSpacing(8)
        form.addWidget(QLabel("输出格式"))
        self.cb_container = QComboBox()
        for value, label in CONTAINER_CHOICES:
            self.cb_container.addItem(label, value)
        form.addWidget(self.cb_container, 1)
        layout.addLayout(form)

        self.lbl_hint = self.hint_label("")
        layout.addWidget(self.lbl_hint)

        # ---------------- 信号 ----------------
        self.btn_add_extra.clicked.connect(self._add_extra)
        self.btn_remove_extra.clicked.connect(self._remove_extra)
        self.cb_container.currentIndexChanged.connect(self.schedule_preview)

    # ------------------------------------------------------------------
    # 文件变化 → 重建内嵌轨道表
    # ------------------------------------------------------------------
    def _on_files_changed(self) -> None:
        super()._on_files_changed()
        self._reload_tracks()

    def _on_current_changed(self, path: str) -> None:
        # 选中项变化 → 内嵌轨道表跟着切换（外部音轨/字幕表是全局设置，保持不动）
        super()._on_current_changed(path)
        self._reload_tracks()

    def _reload_tracks(self) -> None:
        self.track_table.setRowCount(0)
        path = self.current_first()
        if not path:
            return
        info = self.probe(path)
        if info is None or not info.ok:
            self.lbl_hint.setText("无法解析该文件，请重新选择。")
            return
        for track in list_tracks(info):
            if track.is_cover:
                continue  # 封面图流交给"属性"页处理
            if track.kind not in ("video", "audio", "subtitle"):
                continue
            row = self.track_table.rowCount()
            self.track_table.insertRow(row)
            name = QTableWidgetItem(track.label())
            name.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            name.setCheckState(Qt.Checked)
            self.track_table.setItem(row, 0, name)
            self.track_table.setItem(row, 1, QTableWidgetItem(track.language))
            self.track_table.setItem(row, 2, QTableWidgetItem(track.title))
        if not self.track_table.rowCount():
            self.lbl_hint.setText("该文件没有可封装的轨道。")
        else:
            self.lbl_hint.setText(
                "本页一次只封装一个主文件；切换左侧选中项即可编辑它的轨道表。"
            )
        self._refresh_summary()

    # ------------------------------------------------------------------
    def _refresh_summary(self) -> None:
        rows: list[tuple[str, str]] = []
        main = self.current_first()
        if main:
            info = self.probe(main)
            if info is not None and info.ok:
                rows.append(("主文件", os.path.basename(main)))
                kept = self._kept_counts(info)
                rows.append(("内嵌轨道", kept))
        extras = self._extra_rows()
        audio_count = sum(1 for item in extras if item["kind"] == "audio")
        sub_count = sum(1 for item in extras if item["kind"] == "subtitle")
        rows.append(("外部音轨", f"{audio_count} 条" if audio_count else "无"))
        rows.append(("外部字幕", f"{sub_count} 条" if sub_count else "无"))
        self.info_panel.set_rows(rows)

    def _kept_counts(self, info) -> str:
        video = audio = subs = 0
        for row in range(self.track_table.rowCount()):
            item = self.track_table.item(row, 0)
            if item is None or item.checkState() != Qt.Checked:
                continue
            text = item.text()
            if text.startswith("#") and "  视频" in text:
                video += 1
            elif "  音轨" in text:
                audio += 1
            elif "  字幕" in text:
                subs += 1
        parts = []
        if video:
            parts.append(f"视频 {video}")
        if audio:
            parts.append(f"音轨 {audio}")
        if subs:
            parts.append(f"字幕 {subs}")
        return "、".join(parts) or "无"

    # ------------------------------------------------------------------
    def _add_extra(self) -> None:
        patterns = (
            "音频与字幕 (%s);;所有文件 (*.*)"
            % " ".join(f"*{e}" for e in sorted((AUDIO_EXTS | SUBTITLE_EXTS) - {""}))
        )
        files, _ = QFileDialog.getOpenFileNames(self, "添加外部音轨 / 字幕", "", patterns)
        for path in files or []:
            kind = "audio" if ext_of(path) in AUDIO_EXTS else "subtitle"
            row = self.extra_table.rowCount()
            self.extra_table.insertRow(row)
            item = QTableWidgetItem(os.path.basename(path))
            item.setData(Qt.UserRole, path)
            item.setToolTip(path)
            self.extra_table.setItem(row, 0, item)
            self.extra_table.setItem(row, 1, QTableWidgetItem("音轨" if kind == "audio" else "字幕"))
            self.extra_table.setItem(row, 2, QTableWidgetItem("chi"))
            self.extra_table.setItem(row, 3, QTableWidgetItem(""))
            check = QTableWidgetItem()
            check.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
            check.setCheckState(Qt.Checked if kind == "audio" else Qt.Unchecked)
            self.extra_table.setItem(row, 4, check)
        if files:
            self._refresh_summary()
            self.schedule_preview()

    def _remove_extra(self) -> None:
        rows = sorted({index.row() for index in self.extra_table.selectedIndexes()}, reverse=True)
        for row in rows:
            self.extra_table.removeRow(row)
        if rows:
            self._refresh_summary()
            self.schedule_preview()

    def _extra_rows(self) -> list[dict]:
        result: list[dict] = []
        for row in range(self.extra_table.rowCount()):
            path_item = self.extra_table.item(row, 0)
            if path_item is None:
                continue
            kind_item = self.extra_table.item(row, 1)
            lang_item = self.extra_table.item(row, 2)
            title_item = self.extra_table.item(row, 3)
            default_item = self.extra_table.item(row, 4)
            result.append({
                "path": str(path_item.data(Qt.UserRole) or path_item.text()),
                "kind": "audio" if (kind_item and kind_item.text() == "音轨") else "subtitle",
                "language": (lang_item.text().strip() if lang_item else "") or "und",
                "title": title_item.text().strip() if title_item else "",
                "default": bool(default_item and default_item.checkState() == Qt.Checked),
            })
        return result

    # ------------------------------------------------------------------
    # 基类钩子
    # ------------------------------------------------------------------
    def current_first(self) -> str:
        paths = self.paths()
        return paths[0] if paths else ""

    def can_run(self) -> tuple[bool, str]:
        if not self.paths():
            return False, "先添加一个主文件"
        if self.track_table.rowCount() == 0:
            return False, "主文件里没有可封装的轨道"
        for item in self._extra_rows():
            if not os.path.isfile(item["path"]):
                return False, f"外部文件不存在：{item['path']}"
        return True, ""

    def reset_state(self) -> None:
        super().reset_state()
        self.track_table.setRowCount(0)
        self.extra_table.setRowCount(0)

    def build_specs(self) -> list[OperationSpec]:
        src = self.current_first()
        if not src:
            return []
        info = self.probe(src)
        tracks = list_tracks(info) if (info is not None and info.ok) else []

        # 内嵌轨道：勾选状态 → 相对序号
        keep_audio: list[int] = []
        keep_subs: list[int] = []
        audio_seen = sub_seen = 0
        for track in tracks:
            if track.is_cover or track.kind not in ("audio", "subtitle"):
                continue
            row = self._track_row_of(track)
            checked = row is None or self.track_table.item(row, 0).checkState() == Qt.Checked
            if track.kind == "audio":
                if checked:
                    keep_audio.append(audio_seen)
                audio_seen += 1
            else:
                if checked:
                    keep_subs.append(sub_seen)
                sub_seen += 1

        extra_audio = [item for item in self._extra_rows() if item["kind"] == "audio"]
        extra_subs = [item for item in self._extra_rows() if item["kind"] == "subtitle"]

        choice = self.cb_container.currentData() or "same"
        ext = ext_of(src) if choice == "same" else choice
        if not ext:
            ext = ".mkv"
        out = self.make_output_path(src, "mux", ext)

        return [OperationSpec(
            op=OP_MUX,
            inputs=[src],
            output=out,
            params={
                "container": ext,
                "keep_audio": keep_audio,
                "keep_subs": keep_subs,
                "extra_audio": extra_audio,
                "extra_subs": extra_subs,
            },
            label=os.path.basename(src),
            info=info,
        )]

    def _track_row_of(self, track) -> int | None:
        """按 ffprobe 的 stream index 找回表格行。"""
        target = f"#{track.index}  "
        for row in range(self.track_table.rowCount()):
            item = self.track_table.item(row, 0)
            if item is not None and item.text().startswith(target):
                return row
        return None


__all__ = ["TrackPage"]
