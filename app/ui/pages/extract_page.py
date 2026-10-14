"""提取（Extract）—— 需求六.4。

三种子功能：
- 提取音频：按输出扩展名决定行为（m4a/aac → 无损 copy；mp3 → libmp3lame；wav → pcm_s16le）
- 去音频：``-an -c:v copy``（无损、秒级）
- 提取某一帧为图片：默认取当前预览时间，无预览时取时长 10%，并提供秒数/百分比两种输入
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
)

from ...core.builder import BuildError
from ...core.paths import AUDIO_EXTS, VIDEO_EXTS
from ...core.spec import OP_EXTRACT_AUDIO, OP_EXTRACT_FRAME, OP_STRIP_AUDIO, OperationSpec
from ...core.timeparse import filename_timestamp, format_hms, parse_time_safe
from .base_page import BasePage

MODES = [
    ("audio", "提取音频"),
    ("strip", "去音频"),
    ("frame", "提取某一帧为图片"),
]

AUDIO_CONTAINERS = [
    (".m4a", "M4A (.m4a) — 音轨无损复制，秒级完成"),
    (".aac", "AAC (.aac) — 音轨无损复制，秒级完成"),
    (".mp3", "MP3 (.mp3) — 重编码"),
    (".wav", "WAV (.wav) — 重编码为 PCM"),
    (".flac", "FLAC (.flac) — 无损压缩"),
]

STRIP_CONTAINERS = [(".mp4", "MP4 (.mp4)"), (".mkv", "MKV (.mkv)"), (".mov", "MOV (.mov)")]

FRAME_FORMATS = [(".jpg", "JPG (.jpg)"), (".png", "PNG (.png)")]


class ExtractPage(BasePage):
    TITLE = "提取"
    USES_PREVIEW = True
    ALLOWED_EXTS = VIDEO_EXTS | AUDIO_EXTS
    FILE_HINT = "拖入视频/音频文件（支持批量，共用同一套参数）；提取帧时可用播放器定位时间点"
    LIST_HEIGHT = 140

    def _build_params(self, layout: QVBoxLayout) -> None:
        self.add_group_title(layout, "提取方式")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)

        self.cb_mode = QComboBox()
        for _value, label in MODES:
            self.cb_mode.addItem(label)
        form.addRow("提取方式", self.cb_mode)
        layout.addLayout(form)

        self.add_separator(layout)
        self.add_group_title(layout, "参数")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)

        self.cb_audio_container = QComboBox()
        for value, label in AUDIO_CONTAINERS:
            self.cb_audio_container.addItem(label, value)
        self.lbl_audio_row = self._label("输出格式")
        form.addRow(self.lbl_audio_row, self.cb_audio_container)

        self.sp_abitrate = QSpinBox()
        self.sp_abitrate.setRange(32, 512)
        self.sp_abitrate.setValue(192)
        self.sp_abitrate.setSuffix(" kbps")
        self.lbl_abr_row = self._label("音频码率")
        form.addRow(self.lbl_abr_row, self.sp_abitrate)

        self.cb_strip_container = QComboBox()
        for value, label in STRIP_CONTAINERS:
            self.cb_strip_container.addItem(label, value)
        self.lbl_strip_row = self._label("输出格式")
        form.addRow(self.lbl_strip_row, self.cb_strip_container)

        self.ed_time = QLineEdit()
        self.ed_time.setPlaceholderText("秒数或 00:01:23")
        self.btn_now = QPushButton("取当前")
        self.btn_now.setObjectName("Flat")
        time_row = QHBoxLayout()
        time_row.addWidget(self.ed_time, 1)
        time_row.addWidget(self.btn_now)
        self._time_widget = self.wrap_row(time_row)
        self.lbl_time_row = self._label("时间点")
        form.addRow(self.lbl_time_row, self._time_widget)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 1000)
        self.slider.setValue(100)
        self.lbl_percent = QLabel("10%")
        self.lbl_percent.setObjectName("Notice")
        percent_row = QHBoxLayout()
        percent_row.addWidget(self.slider, 1)
        percent_row.addWidget(self.lbl_percent)
        self._percent_widget = self.wrap_row(percent_row)
        self.lbl_percent_row = self._label("按百分比")
        form.addRow(self.lbl_percent_row, self._percent_widget)

        self.cb_frame_format = QComboBox()
        for value, label in FRAME_FORMATS:
            self.cb_frame_format.addItem(label, value)
        self.lbl_format_row = self._label("图片格式")
        form.addRow(self.lbl_format_row, self.cb_frame_format)

        self.sp_quality = QSpinBox()
        self.sp_quality.setRange(1, 31)
        self.sp_quality.setValue(2)
        self.sp_quality.setToolTip("JPG 质量，数值越小质量越好（2 为高画质）")
        self.lbl_quality_row = self._label("图片质量")
        form.addRow(self.lbl_quality_row, self.sp_quality)

        self.sp_max_width = QSpinBox()
        self.sp_max_width.setRange(0, 7680)
        self.sp_max_width.setValue(0)
        self.sp_max_width.setSuffix(" px")
        self.sp_max_width.setSpecialValueText("原始尺寸")
        self.lbl_width_row = self._label("最大宽度")
        form.addRow(self.lbl_width_row, self.sp_max_width)

        layout.addLayout(form)

        self.lbl_behaviour = self.hint_label("")
        layout.addWidget(self.lbl_behaviour)

        self.cb_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.cb_audio_container.currentIndexChanged.connect(self._on_mode_changed)
        self.cb_strip_container.currentIndexChanged.connect(self.schedule_preview)
        self.cb_frame_format.currentIndexChanged.connect(self.schedule_preview)
        self.sp_quality.valueChanged.connect(self.schedule_preview)
        self.sp_max_width.valueChanged.connect(self.schedule_preview)
        self.sp_abitrate.valueChanged.connect(self.schedule_preview)
        self.ed_time.textChanged.connect(self._on_time_edited)
        self.ed_time.textEdited.connect(self._on_time_manually_edited)
        self.slider.valueChanged.connect(self._on_slider)
        self.btn_now.clicked.connect(self._take_current)
        self._time_user_edited = False
        self._on_mode_changed()

    @staticmethod
    def _label(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    # ------------------------------------------------------------------
    def _mode(self) -> str:
        return MODES[self.cb_mode.currentIndex()][0]

    def _on_mode_changed(self) -> None:
        mode = self._mode()
        is_audio = mode == "audio"
        is_strip = mode == "strip"
        is_frame = mode == "frame"

        for widget in (self.lbl_audio_row, self.cb_audio_container):
            widget.setVisible(is_audio)
        for widget in (self.lbl_abr_row, self.sp_abitrate):
            widget.setVisible(is_audio)
            widget.setEnabled(is_audio)
        for widget in (self.lbl_strip_row, self.cb_strip_container):
            widget.setVisible(is_strip)
        for widget in (self.lbl_time_row, self._time_widget, self.lbl_percent_row,
                       self._percent_widget, self.lbl_format_row, self.cb_frame_format,
                       self.lbl_quality_row, self.sp_quality, self.lbl_width_row, self.sp_max_width):
            widget.setVisible(is_frame)
        self.sp_quality.setVisible(is_frame and self.cb_frame_format.currentData() != ".png")

        if is_audio:
            value = self.cb_audio_container.currentData()
            text = {
                ".m4a": "选择 m4a/aac 时，只要源音轨是 AAC 就无损复制（-vn -c:a copy），秒级完成、无音质损失。",
                ".aac": "选择 m4a/aac 时，只要源音轨是 AAC 就无损复制（-vn -c:a copy），秒级完成、无音质损失。",
                ".mp3": "选择 mp3 时会用 libmp3lame 重编码，速度取决于时长。",
                ".wav": "选择 wav 时会重编码为 PCM 16bit，文件体积会显著变大。",
                ".flac": "选择 flac 时为无损压缩，体积约为 WAV 的一半。",
            }.get(value, "")
            self.lbl_behaviour.setText(text)
        elif is_strip:
            self.lbl_behaviour.setText("去音频使用 -an -c:v copy，视频流不重编码，秒级完成。")
        else:
            info = self.probe(self.current_first())
            hint = "默认取当前预览时间；没有预览时间时取视频时长的 10%。"
            if info and info.duration:
                hint += f" 当前文件时长 {format_hms(info.duration)}。"
            self.lbl_behaviour.setText(hint)
            self._sync_default_time()
        self.schedule_preview()

    def _sync_default_time(self) -> None:
        if self.ed_time.text().strip():
            return
        info = self.probe(self.current_first())
        if info and info.duration:
            seconds = info.duration * 0.1
            self.ed_time.setText(format_hms(seconds, ms=True))
            self.slider.setValue(100)

    def _on_time_manually_edited(self, _text: str) -> None:
        """用户亲手输入过时间点后，切换选中文件时不再用默认值覆盖。"""
        self._time_user_edited = True

    def _on_time_edited(self) -> None:
        value, err = parse_time_safe(self.ed_time.text())
        info = self.probe(self.current_first())
        if value is not None and info and info.duration:
            ratio = max(0.0, min(1.0, value / info.duration))
            self.slider.blockSignals(True)
            self.slider.setValue(int(ratio * 1000))
            self.lbl_percent.setText(f"{ratio * 100:.1f}%")
            self.slider.blockSignals(False)
        elif err:
            self.lbl_percent.setText("—")
        self.schedule_preview()

    def _on_slider(self, value: int) -> None:
        info = self.probe(self.current_first())
        self.lbl_percent.setText(f"{value / 10:.1f}%")
        if info and info.duration:
            seconds = info.duration * value / 1000.0
            self.ed_time.blockSignals(True)
            self.ed_time.setText(format_hms(seconds, ms=True))
            self.ed_time.blockSignals(False)
            self.schedule_preview()

    def _take_current(self) -> None:
        if self.preview is None:
            return
        self._apply_preview_mark("frame", self.preview.position())

    def _apply_preview_mark(self, kind: str, seconds: float) -> None:
        self.ed_time.setText(format_hms(seconds, ms=True))
        self.schedule_preview()

    def _on_current_changed(self, path: str) -> None:
        super()._on_current_changed(path)
        info = self.probe(path)
        if self._mode() == "frame" and not getattr(self, "_time_user_edited", False):
            if info and info.duration:
                seconds = info.duration * 0.1
                self.ed_time.setText(format_hms(seconds, ms=True))
            else:
                self.ed_time.setText("")
        self._on_mode_changed()

    # ------------------------------------------------------------------
    def build_specs(self) -> list[OperationSpec]:
        paths = self.paths()
        if not paths:
            return []
        mode = self._mode()
        specs: list[OperationSpec] = []

        if mode == "frame":
            when, err = parse_time_safe(self.ed_time.text())
            if err:
                raise BuildError(f"时间点：{err}")
            if when is None:
                raise BuildError("请填写要提取的时间点")
            ext = self.cb_frame_format.currentData() or ".jpg"

        for src in paths:
            info = self.probe(src)
            if info is not None and not info.ok:
                continue
            if mode == "audio":
                container = self.cb_audio_container.currentData() or ".m4a"
                suffix = f"audio_{container.lstrip('.')}"
                out = self.make_output_path(src, suffix, container)
                specs.append(OperationSpec(
                    op=OP_EXTRACT_AUDIO,
                    inputs=[src],
                    output=out,
                    params={"abitrate_k": self.sp_abitrate.value()},
                    label=os.path.basename(src),
                    info=info,
                ))
            elif mode == "strip":
                container = self.cb_strip_container.currentData() or ".mp4"
                suffix = "noaudio"
                out = self.make_output_path(src, suffix, container)
                specs.append(OperationSpec(
                    op=OP_STRIP_AUDIO,
                    inputs=[src],
                    output=out,
                    params={},
                    label=os.path.basename(src),
                    info=info,
                ))
            else:
                duration = info.duration if info and info.duration else 0.0
                if duration and when > duration:
                    actual = max(0.0, duration - 0.05)
                else:
                    actual = when
                suffix = f"frame_{filename_timestamp(actual)}"
                out = self.make_output_path(src, suffix, ext)
                specs.append(OperationSpec(
                    op=OP_EXTRACT_FRAME,
                    inputs=[src],
                    output=out,
                    params={
                        "time": actual,
                        "quality": self.sp_quality.value(),
                        "max_width": self.sp_max_width.value(),
                    },
                    label=os.path.basename(src),
                    info=info,
                    duration=0.0,
                ))
        return specs


__all__ = ["ExtractPage"]
