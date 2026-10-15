"""格式转换（Convert）—— 需求六.3 / 实测问题 1。

核心变化（问题 1：参数区不联动）：
- 参数区按输出类型分成三组：**输出格式 / 音频 / 画面**；
- 选到音频容器（mp3/wav/m4a/flac）时，整个「画面」组隐藏，不会再有
  "转音频还要设 CRF" 的困惑；
- 音频编码下拉由**容器**决定（MP4 → AAC，WAV → PCM，FLAC → FLAC…），
  从源头上杜绝"MP4 里塞 MP3""WAV 里塞 AAC"这种放不进容器/兼容性差的组合；
- CommandBuilder 侧做同样的联动校验，界面与命令永远一致。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from ...core.paths import (
    AUDIO_EXTS,
    AUDIO_ONLY_MUXERS,
    IMAGE_EXTS,
    VIDEO_EXTS,
    audio_codec_choices,
    default_audio_codec,
)
from ...core.spec import OP_CONVERT, OperationSpec
from ..widgets.audio_bitrate import AudioBitrate, LOSSLESS_CODECS
from ..widgets.video_params import VideoParams
from .base_page import BasePage


def _right_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return label

CONTAINERS = [
    (".mp4", "MP4 (.mp4)"),
    (".mkv", "MKV (.mkv)"),
    (".mp3", "MP3 (.mp3) — 仅音频"),
    (".wav", "WAV (.wav) — 仅音频"),
    (".m4a", "M4A (.m4a) — 仅音频"),
    (".flac", "FLAC (.flac) — 仅音频"),
]

ROTATIONS = [
    ("none", "不处理"),
    ("cw", "顺时针旋转 90°"),
    ("ccw", "逆时针旋转 90°"),
    ("180", "旋转 180°"),
    ("hflip", "水平翻转"),
    ("vflip", "垂直翻转"),
]


class ConvertPage(BasePage):
    TITLE = "转换"
    USES_PREVIEW = False
    ALLOWED_EXTS = VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS
    FILE_HINT = "拖入要转换的文件（支持批量）"
    LIST_HEIGHT = 150

    def _build_params(self, layout: QVBoxLayout) -> None:
        # ---------------- 1. 输出格式 ----------------
        self.add_group_title(layout, "输出格式")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)
        self.cb_container = QComboBox()
        for value, label in CONTAINERS:
            self.cb_container.addItem(label, value)
        form.addRow("目标格式", self.cb_container)
        layout.addLayout(form)

        # ---------------- 2. 音频 ----------------
        self.add_separator(layout)
        self.audio_title = self.add_group_title(layout, "音频")
        audio_form = QFormLayout()
        audio_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        audio_form.setSpacing(8)
        self.cb_acodec = QComboBox()
        self.lbl_acodec = _right_label("音频编码")
        audio_form.addRow(self.lbl_acodec, self.cb_acodec)

        self.sp_abitrate = AudioBitrate(default=192)
        self.sp_abitrate.changed.connect(self._on_changed)
        self.lbl_abitrate = _right_label("音频码率")
        audio_form.addRow(self.lbl_abitrate, self.sp_abitrate)

        self.sp_volume = QSpinBox()
        self.sp_volume.setRange(0, 500)
        self.sp_volume.setValue(100)
        self.sp_volume.setSuffix(" %")
        self.sp_volume.setToolTip("调整音量，100% 为保持原样（会触发音频重编码）")
        self.lbl_volume = _right_label("音量")
        audio_form.addRow(self.lbl_volume, self.sp_volume)
        layout.addLayout(audio_form)

        # ---------------- 3. 画面 ----------------
        self.add_separator(layout)
        self.video_title = self.add_group_title(layout, "画面")
        self.video = VideoParams(show_copy_codec=True, crf_default=20)
        layout.addWidget(self.video)

        rotate_form = QFormLayout()
        rotate_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        rotate_form.setSpacing(8)
        self.cb_rotate = QComboBox()
        for value, label in ROTATIONS:
            self.cb_rotate.addItem(label, value)
        rotate_form.addRow("旋转 / 翻转", self.cb_rotate)
        self._rotate_form = rotate_form
        layout.addLayout(rotate_form)

        self.lbl_state = self.hint_label("")
        layout.addWidget(self.lbl_state)

        # ---------------- 信号 ----------------
        self.cb_container.currentIndexChanged.connect(self._on_container_changed)
        for widget in (self.cb_acodec, self.cb_rotate):
            widget.currentIndexChanged.connect(self._on_changed)
        self.sp_volume.valueChanged.connect(self._on_changed)
        self.video.changed.connect(self._on_changed)
        self._on_container_changed()

    # ------------------------------------------------------------------
    def _fill_audio_codecs(self, container: str) -> None:
        """按容器重建音频编码下拉（问题 1/2 的联动核心）。"""
        previous = self.cb_acodec.currentData()
        self.cb_acodec.blockSignals(True)
        self.cb_acodec.clear()
        choices = audio_codec_choices(container)
        for value, label in choices:
            self.cb_acodec.addItem(label, value)
        target = previous
        if target not in [value for value, _ in choices]:
            target = default_audio_codec(container)
        index = self.cb_acodec.findData(target)
        self.cb_acodec.setCurrentIndex(index if index >= 0 else 0)
        self.cb_acodec.blockSignals(False)

    def _container_key(self) -> str:
        return str(self.cb_container.currentData() or ".mp4").lstrip(".")

    def _is_audio_container(self) -> bool:
        ext = self.cb_container.currentData() or ".mp4"
        muxer = {"mp3": "mp3", "wav": "wav", "m4a": "ipod", "flac": "flac"}.get(
            ext.lstrip("."), ext.lstrip(".")
        )
        return muxer in AUDIO_ONLY_MUXERS

    def _on_container_changed(self) -> None:
        container = self._container_key()
        self._fill_audio_codecs(container)
        audio_only = self._is_audio_container()

        # 整个「画面」组隐藏（而不是只置灰）—— 转音频时不该看到视频参数
        for widget in (self.video_title, self.video):
            widget.setVisible(not audio_only)
        self._set_form_visible(self._rotate_form, not audio_only)

        # 无损 / FLAC / PCM / 保持原样 都不需要码率
        codec = self.cb_acodec.currentData() or ""
        show_bitrate = codec not in LOSSLESS_CODECS
        # 标签行在不需要时整体收起；组件内部换成一句"为什么不需要"的说明，
        # 否则用户会以为功能丢了
        self.lbl_abitrate.setVisible(show_bitrate)
        if show_bitrate:
            self.sp_abitrate.setVisible(True)
            self.sp_abitrate.clear_note()
        else:
            reason = {
                "copy": "音频保持原样（不重编码），码率由源文件决定。",
                "none": "输出不含音轨，无需设置码率。",
                "pcm": "PCM 为未压缩音频，不存在码率设置。",
                "flac": "FLAC 为无损压缩，不存在码率设置。",
                "alac": "ALAC 为无损压缩，不存在码率设置。",
            }.get(codec, "该编码不需要设置码率。")
            self.sp_abitrate.set_visible_controls(False)
            self.sp_abitrate.set_note(reason)
        self.lbl_acodec.setVisible(True)
        self.cb_acodec.setVisible(True)
        self._on_changed()

    def _set_form_visible(self, form: QFormLayout, visible: bool) -> None:
        for row in range(form.rowCount()):
            for role in (QFormLayout.LabelRole, QFormLayout.FieldRole):
                item = form.itemAt(row, role)
                if item is None:
                    continue
                widget = item.widget()
                if widget is not None:
                    widget.setVisible(visible)

    def _on_changed(self) -> None:
        audio_only = self._is_audio_container()
        container = self._container_key()
        codec = self.cb_acodec.currentData() or ""

        messages: list[str] = []
        if audio_only:
            messages.append(
                f"输出为纯音频（{container.upper()}）：已隐藏画面参数，音频编码固定为 "
                f"{self.cb_acodec.currentText().split('（')[0]}。"
            )
        if not audio_only and self.video.cb_codec.currentData() == "copy" and self.video.uses_filters():
            messages.append("已设置缩放/帧率，视频无法无损复制，执行时会自动改为重编码。")
        if not audio_only and codec == "copy" and self.sp_volume.value() != 100:
            messages.append("调整了音量，音频无法无损复制，会自动改为重编码。")
        self.lbl_state.setText(" ".join(messages))
        self.schedule_preview()

    # ------------------------------------------------------------------
    def build_specs(self) -> list[OperationSpec]:
        paths = self.paths()
        if not paths:
            return []
        container = self.cb_container.currentData() or ".mp4"
        audio_only = self._is_audio_container()
        values = self.video.values()
        acodec = self.cb_acodec.currentData() or default_audio_codec(self._container_key())

        specs: list[OperationSpec] = []
        for src in paths:
            info = self.probe(src)
            if info is not None and not info.ok:
                continue
            ext = container
            if audio_only or (info is not None and not info.has_video and not audio_only):
                # 纯音频源转视频容器 → 自动落到 m4a，避免造出空视频
                if not audio_only and info is not None and not info.has_video:
                    ext = ".m4a"
                    self.logMessage.emit(
                        f"{os.path.basename(src)} 没有视频画面，"
                        f"输出已自动从 {container} 改为 M4A 音频文件"
                    )
            suffix = f"conv_{ext.lstrip('.')}"
            out = self.make_output_path(src, suffix, ext)

            params: dict = {
                "container": container,
                "acodec": acodec,
                "abitrate_k": self.sp_abitrate.value(),
                "volume": self.sp_volume.value() / 100.0,
            }
            if audio_only:
                # 音频输出不带任何视频参数（问题 1 的"参数不泄漏进命令"）
                params.update({"vcodec": "none", "width": 0, "height": 0, "fps": 0.0,
                               "crf": 20, "vbitrate_k": 0, "rotation": "none"})
            else:
                params.update(values)
                params["rotation"] = self.cb_rotate.currentData() or "none"

            specs.append(
                OperationSpec(
                    op=OP_CONVERT,
                    inputs=[src],
                    output=out,
                    params=params,
                    label=os.path.basename(src),
                    info=info,
                )
            )
        return specs


__all__ = ["ConvertPage"]
