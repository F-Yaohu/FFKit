"""视频编码参数组件 —— 转换页与压缩页共用（问题 21）。

问题：压缩页和转换页各自写了一套"视频编码器 / 质量 / 分辨率 / 帧率"，
参数名与默认值容易不一致，改一处忘一处。

这个组件把"视频侧参数"的 UI 与取值逻辑收敛到一处，两页只声明需要哪几项。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

VIDEO_CODECS = [
    ("copy", "保持原样（无损，最快）"),
    ("x264", "H.264 / libx264（兼容性最好）"),
    ("x265", "H.265 / libx265（体积更小）"),
]

RESOLUTIONS = [
    ("", "保持原样"),
    ("1920x1080", "1920×1080"),
    ("1280x720", "1280×720"),
    ("854x480", "854×480"),
    ("640x360", "640×360"),
    ("custom", "自定义…"),
]

FPS_CHOICES = [("", "保持原样"), ("60", "60"), ("30", "30"), ("25", "25"), ("24", "24")]

PRESETS = ["ultrafast", "veryfast", "faster", "fast", "medium", "slow", "slower"]


class VideoParams(QWidget):
    """视频侧参数：编码器 / 分辨率 / 帧率 / CRF / 码率 / 编码速度。"""

    changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        codecs: list[tuple[str, str]] | None = None,
        show_copy_codec: bool = True,
        show_resolution: bool = True,
        show_fps: bool = True,
        show_crf: bool = True,
        show_bitrate: bool = True,
        show_preset: bool = True,
        crf_default: int = 20,
        bitrate_default: int = 0,
        preset_default: str = "medium",
        crf_hint: str = "",
    ) -> None:
        super().__init__(parent)
        self._show_resolution = show_resolution
        self._show_fps = show_fps
        self._show_crf = show_crf
        self._show_bitrate = show_bitrate
        self._show_preset = show_preset

        choices = list(codecs or VIDEO_CODECS)
        if not show_copy_codec:
            choices = [item for item in choices if item[0] != "copy"]

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.form.setSpacing(8)
        root.addLayout(self.form)

        self.cb_codec = QComboBox()
        for value, label in choices:
            self.cb_codec.addItem(label, value)
        self.form.addRow("视频编码", self.cb_codec)

        self.cb_resolution = QComboBox()
        self.sp_width = QSpinBox()
        self.sp_height = QSpinBox()
        self._size_widget = None
        if show_resolution:
            for value, label in RESOLUTIONS:
                self.cb_resolution.addItem(label, value)
            self.form.addRow("分辨率", self.cb_resolution)

            row = QHBoxLayout()
            row.setSpacing(6)
            self.sp_width.setRange(16, 7680)
            self.sp_width.setValue(1280)
            self.sp_width.setSuffix(" 宽")
            self.sp_height.setRange(16, 4320)
            self.sp_height.setValue(720)
            self.sp_height.setSuffix(" 高")
            row.addWidget(self.sp_width)
            row.addWidget(self.sp_height)
            row.addStretch(1)
            self._size_widget = _wrap(row)
            self.form.addRow("自定义尺寸", self._size_widget)
            self._size_row = self.form.rowCount() - 1

        self.cb_fps = QComboBox()
        if show_fps:
            for value, label in FPS_CHOICES:
                self.cb_fps.addItem(label, value)
            self.form.addRow("帧率", self.cb_fps)

        self.sp_crf = QSpinBox()
        if show_crf:
            self.sp_crf.setRange(0, 51)
            self.sp_crf.setValue(crf_default)
            self.sp_crf.setToolTip(
                crf_hint or "数值越小画质越好、体积越大。18~23 为常用范围，23 为通用默认。"
            )
            self.form.addRow("CRF 质量", self.sp_crf)

        self.sp_vbitrate = QSpinBox()
        if show_bitrate:
            self.sp_vbitrate.setRange(0, 80000)
            self.sp_vbitrate.setValue(bitrate_default)
            self.sp_vbitrate.setSuffix(" kbps")
            self.sp_vbitrate.setSpecialValueText("不限制（用 CRF）")
            self.form.addRow("视频码率", self.sp_vbitrate)

        self.cb_preset = QComboBox()
        if show_preset:
            for name in PRESETS:
                self.cb_preset.addItem(name)
            self.cb_preset.setCurrentText(preset_default)
            self.form.addRow("编码速度", self.cb_preset)

        for widget in (self.cb_codec, self.cb_resolution, self.cb_fps, self.cb_preset):
            widget.currentIndexChanged.connect(self._on_changed)
        for spin in (self.sp_width, self.sp_height, self.sp_crf, self.sp_vbitrate):
            spin.valueChanged.connect(self._on_changed)

        self.sync_enabled()

    # ------------------------------------------------------------------
    def _on_changed(self) -> None:
        self.sync_enabled()
        self.changed.emit()

    def sync_enabled(self) -> None:
        """无损复制时质量参数没意义；自定义尺寸只在选了"自定义"时可编辑。"""
        codec = self.cb_codec.currentData() or "copy"
        copy_mode = codec == "copy"
        if self._show_resolution:
            self.cb_resolution.setEnabled(not copy_mode)
            custom = self.cb_resolution.currentData() == "custom"
            self._size_widget.setEnabled(not copy_mode and custom)
            self.sp_width.setEnabled(not copy_mode and custom)
            self.sp_height.setEnabled(not copy_mode and custom)
            # 没选"自定义"时整行隐藏，参数区更清爽（只置灰的话两个死框很碍眼）
            try:
                self.form.setRowVisible(self._size_row, not copy_mode and custom)
            except AttributeError:  # 旧版 Qt 没有 setRowVisible
                self._size_widget.setVisible(not copy_mode and custom)
        if self._show_fps:
            self.cb_fps.setEnabled(not copy_mode)
        if self._show_crf:
            self.sp_crf.setEnabled(not copy_mode)
        if self._show_bitrate:
            self.sp_vbitrate.setEnabled(not copy_mode)

    # ------------------------------------------------------------------
    def uses_filters(self) -> bool:
        """是否设置了缩放/帧率等必须重编码的画面处理。"""
        if not self._show_resolution:
            return False
        resolution = self.cb_resolution.currentData()
        if resolution == "custom":
            return True
        return bool(resolution) or bool(self.cb_fps.currentData())

    def values(self) -> dict:
        width = height = 0
        fps = 0.0
        if self._show_resolution:
            resolution = self.cb_resolution.currentData()
            if resolution == "custom":
                width = self.sp_width.value()
                height = self.sp_height.value()
            elif resolution:
                try:
                    width_text, _, height_text = str(resolution).partition("x")
                    width, height = int(width_text), int(height_text)
                except ValueError:
                    width = height = 0
        if self._show_fps:
            try:
                fps = float(self.cb_fps.currentData() or 0)
            except (TypeError, ValueError):
                fps = 0.0
        return {
            "vcodec": self.cb_codec.currentData() or "copy",
            "width": width,
            "height": height,
            "fps": fps,
            "crf": self.sp_crf.value() if self._show_crf else 20,
            "vbitrate_k": self.sp_vbitrate.value() if self._show_bitrate else 0,
            "preset": self.cb_preset.currentText() if self._show_preset else "medium",
        }

    def set_enabled(self, enabled: bool) -> None:
        for widget in (
            self.cb_codec, self.cb_resolution, self.sp_width, self.sp_height,
            self.cb_fps, self.sp_crf, self.sp_vbitrate, self.cb_preset,
        ):
            widget.setEnabled(widget.isEnabled() and enabled)
        if enabled:
            self.sync_enabled()


def _wrap(layout) -> QWidget:
    widget = QWidget()
    wrapper = QVBoxLayout(widget)
    wrapper.setContentsMargins(0, 0, 0, 0)
    wrapper.addLayout(layout)
    return widget


__all__ = ["FPS_CHOICES", "PRESETS", "RESOLUTIONS", "VIDEO_CODECS", "VideoParams"]
