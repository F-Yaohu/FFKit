"""音频码率选择组件 —— 转换 / 提取 / 压缩三页共用（需求 5）。

问题：三页都有一个裸的 ``QSpinBox`` 让用户填 kbps，但普通人完全不知道
128 / 192 / 320 差在哪、体积差多少、什么时候够用。填高了白白占体积，
填低了ogg/MP3 的高频会明显发闷。

做法：给一排**有名字的标准档位**，每档都写清楚"适合什么"；确实需要精确
控制的人再选「自定义」。同时按音频编码联动：

- 无损编码（PCM / FLAC）→ 整行隐藏并说明原因，而不是给一个无意义的数字；
- 有损编码 → 显示档位下拉；
- 选「无损复制」→ 同样隐藏（码率由源文件决定，改不了）。

这样默认路径就是"选一个听上去对的档"，而不是"猜一个数字"。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

# (档位值, 界面文案, 说明)——说明写"适合什么 + 体积大致多少"
# 体积按 1 分钟立体声估算，便于用户建立直觉。
# 说明文字刻意写得短：右侧参数栏在 1360 宽的窗口里只有 ~300px，
# 一旦折成三行就把整个参数区撑得很长。
BITRATE_PRESETS: list[tuple[int, str, str]] = [
    (96, "96 kbps · 语音/旁白", "人声播客有声书，体积最小；音乐会发闷。"),
    (128, "128 kbps · 通用标准", "兼容性最好，日常首选。"),
    (160, "160 kbps · 音乐推荐", "细节比 128 略好，体积仍小。"),
    (192, "192 kbps · 高音质", "细节更完整，音质优先选它。"),
    (256, "256 kbps · 很高音质", "接近无损；老设备/车载可能不认。"),
    (320, "320 kbps · MP3 上限", "MP3 最高码率，通常没必要。"),
]

# 无损编码：给码率没有意义
LOSSLESS_CODECS = {"pcm", "pcm_s16le", "flac", "copy", "none", "alac"}

CUSTOM = "custom"


class AudioBitrate(QWidget):
    """档位下拉 + 自定义数值 + 人话说明。"""

    changed = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        default: int = 192,
        presets: list[tuple[int, str, str]] | None = None,
    ) -> None:
        super().__init__(parent)
        self._presets = list(presets or BITRATE_PRESETS)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.cb_preset = QComboBox()
        self.cb_preset.setToolTip("选一个标准档位；不确定就用 128 或 192")
        self.sp_custom = QSpinBox()
        self.sp_custom.setRange(32, 512)
        self.sp_custom.setSuffix(" kbps")
        self.sp_custom.setVisible(False)
        row.addWidget(self.cb_preset, 1)
        row.addWidget(self.sp_custom)
        root.addLayout(row)

        self.lbl_note = QLabel("")
        self.lbl_note.setObjectName("Hint")
        self.lbl_note.setWordWrap(True)
        self.lbl_note.setMinimumWidth(0)
        root.addWidget(self.lbl_note)

        for value, label, _note in self._presets:
            self.cb_preset.addItem(label, value)
        self.cb_preset.addItem(f"自定义…", CUSTOM)

        self.cb_preset.currentIndexChanged.connect(self._on_preset_changed)
        self.sp_custom.valueChanged.connect(self._on_custom_changed)

        self._external_note = ""
        self._controls_visible = True
        self.set_value(default)

    # ------------------------------------------------------------------
    def value(self) -> int:
        """当前码率（kbps）。"""
        data = self.cb_preset.currentData()
        if data == CUSTOM:
            return self.sp_custom.value()
        return int(data or 192)

    def set_value(self, kbps: int) -> None:
        """按数值选中档位；不在表里就落到「自定义」。"""
        kbps = int(kbps or 0)
        for value, _label, _note in self._presets:
            if value == kbps:
                self.cb_preset.blockSignals(True)
                self.cb_preset.setCurrentIndex(self.cb_preset.findData(value))
                self.cb_preset.blockSignals(False)
                self._sync_custom_visible()
                self._refresh_note()
                return
        self.cb_preset.blockSignals(True)
        self.sp_custom.blockSignals(True)
        self.cb_preset.setCurrentIndex(self.cb_preset.findData(CUSTOM))
        self.sp_custom.setValue(max(32, min(512, kbps or 192)))
        self.sp_custom.blockSignals(False)
        self.cb_preset.blockSignals(False)
        self._sync_custom_visible()
        self._refresh_note()

    def _sync_custom_visible(self) -> None:
        self.sp_custom.setVisible(self._controls_visible and self._is_custom())
        self.cb_preset.setVisible(self._controls_visible)

    def _on_preset_changed(self) -> None:
        custom = self._is_custom()
        if custom and self.sp_custom.value() == 32:
            # 从低往高选时不要停在 32 这种极端值，给个中性起点
            self.sp_custom.setValue(192)
        self._sync_custom_visible()
        self._refresh_note()
        self.changed.emit()

    def _on_custom_changed(self) -> None:
        self._refresh_note()
        self.changed.emit()

    def _refresh_note(self) -> None:
        if self._external_note:
            self.lbl_note.setText(self._external_note)
            return
        data = self.cb_preset.currentData()
        note = ""
        for value, _label, text in self._presets:
            if value == data:
                note = f"{text}每分钟约 {value * 60 / 8 / 1024:.1f} MB。"
                break
        else:
            note = f"自定义码率每分钟约 {self.value() * 60 / 8 / 1024:.1f} MB；不建议低于 96。"
        self.lbl_note.setText(note)

    # ------------------------------------------------------------------
    def set_note(self, text: str) -> None:
        """由页面在"无损 / 无需重编码"等场景下说明为什么不用码率。"""
        self._external_note = text or ""
        self._refresh_note()

    def set_visible_controls(self, visible: bool) -> None:
        """只收起下拉与数值框，保留说明文字（用于"为什么不需要码率"）。"""
        self._controls_visible = bool(visible)
        self.cb_preset.setVisible(self._controls_visible)
        self.sp_custom.setVisible(self._controls_visible and self._is_custom())

    def set_row_visible(self, visible: bool) -> None:
        """整块显隐（连同外部那一行的左侧标签由调用方一起控制）。"""
        self.setVisible(visible)
        self.setEnabled(visible)

    def clear_note(self) -> None:
        """恢复正常：显示档位 + 由组件自己说明档位差异。"""
        self._external_note = ""
        self._controls_visible = True
        self.cb_preset.setVisible(True)
        self.sp_custom.setVisible(self._is_custom())
        self._refresh_note()

    def _is_custom(self) -> bool:
        return self.cb_preset.currentData() == CUSTOM


__all__ = ["AudioBitrate", "BITRATE_PRESETS", "CUSTOM", "LOSSLESS_CODECS"]