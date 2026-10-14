"""压缩（Compress）—— 需求六.5 / 实测问题 2、21。

本轮变化：
- **容器与音频编码联动**（问题 2）：MP4 → 只能 AAC；MKV → AAC/MP3/Opus/FLAC。
  以前 MP4 容器默认塞 MP3，苹果生态/车载/电视认不出音轨。
- 增加"输出格式"选择（MP4 / MKV），音频压缩可选 MP3 / M4A / FLAC / WAV。
- 视频编码器与编码速度改用公共组件（问题 21），与转换页共用一套选项。
- 音频编码选项由容器推导，界面与命令永远一致。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
)

from ...core.paths import (
    AUDIO_EXTS,
    VIDEO_EXTS,
    audio_codec_choices,
    default_audio_codec,
)
from ...core.spec import OP_COMPRESS, OperationSpec
from ...core.timeparse import human_bitrate, human_size
from ..widgets.video_params import VideoParams
from .base_page import BasePage

LEVELS = [("high", "高（画质优先）"), ("mid", "中（推荐）"), ("low", "低（体积优先）")]

VIDEO_CONTAINERS = [(".mp4", "MP4 (.mp4)"), (".mkv", "MKV (.mkv)")]
AUDIO_CONTAINERS = [
    (".mp3", "MP3 (.mp3)"),
    (".m4a", "M4A (.m4a)"),
    (".flac", "FLAC (.flac)"),
    (".wav", "WAV (.wav)"),
]

CRF_TABLE = {
    "x264": {"high": 20, "mid": 23, "low": 26},
    "x265": {"high": 24, "mid": 28, "low": 32},
}

COMPRESS_VIDEO_CODECS = [("x264", "H.264 / libx264"), ("x265", "H.265 / libx265")]


class CompressPage(BasePage):
    TITLE = "压缩"
    USES_PREVIEW = False
    ALLOWED_EXTS = VIDEO_EXTS | AUDIO_EXTS
    FILE_HINT = "拖入要压缩的文件；压缩前会先显示源文件实际码率作为判断依据"
    LIST_HEIGHT = 150

    def _build_params(self, layout: QVBoxLayout) -> None:
        # ---------------- 1. 压缩对象 ----------------
        self.add_group_title(layout, "压缩对象")
        head = QFormLayout()
        head.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        head.setSpacing(8)
        self.cb_target = QComboBox()
        self.cb_target.addItem("视频压缩（重编码，体积明显变小）", "video")
        self.cb_target.addItem("音频压缩（抽出音轨后压缩）", "audio")
        head.addRow("压缩对象", self.cb_target)

        self.cb_container = QComboBox()
        self.lbl_container = _right_label("输出格式")
        head.addRow(self.lbl_container, self.cb_container)
        layout.addLayout(head)

        # ---------------- 2. 视频参数 ----------------
        self.add_separator(layout)
        self.video_title = self.add_group_title(layout, "视频参数")
        self.video = VideoParams(
            codecs=COMPRESS_VIDEO_CODECS,
            show_copy_codec=False,
            show_resolution=False,
            show_fps=False,
            show_crf=False,
            show_bitrate=False,
            show_preset=True,
            preset_default="medium",
        )
        layout.addWidget(self.video)

        video_form = QFormLayout()
        video_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        video_form.setSpacing(8)
        self.cb_level = QComboBox()
        for value, label in LEVELS:
            self.cb_level.addItem(label, value)
        self.cb_level.setCurrentIndex(1)
        self.lbl_level = _right_label("压缩档位")
        video_form.addRow(self.lbl_level, self.cb_level)

        self.lbl_crf = QLabel("CRF 23")
        self.lbl_crf.setObjectName("SectionTitle")
        self.lbl_crf_row = _right_label("等效参数")
        video_form.addRow(self.lbl_crf_row, self.lbl_crf)

        self.sp_target = QDoubleSpinBox()
        self.sp_target.setRange(0, 100000)
        self.sp_target.setDecimals(1)
        self.sp_target.setValue(0)
        self.sp_target.setSuffix(" MB")
        self.sp_target.setSpecialValueText("不指定（用 CRF 单遍）")
        self.sp_target.setToolTip("指定目标体积后改用两遍编码反推码率，耗时为原来的约 2 倍")
        self.lbl_target_row = _right_label("目标体积")
        video_form.addRow(self.lbl_target_row, self.sp_target)
        layout.addLayout(video_form)

        # ---------------- 3. 音频参数 ----------------
        self.add_separator(layout)
        self.audio_title = self.add_group_title(layout, "音频参数")
        audio_form = QFormLayout()
        audio_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        audio_form.setSpacing(8)
        self.cb_acodec = QComboBox()
        self.lbl_acodec = _right_label("音频编码")
        audio_form.addRow(self.lbl_acodec, self.cb_acodec)

        self.sp_abitrate = QSpinBox()
        self.sp_abitrate.setRange(32, 512)
        self.sp_abitrate.setValue(128)
        self.sp_abitrate.setSuffix(" kbps")
        self.lbl_abitrate = _right_label("音频码率")
        audio_form.addRow(self.lbl_abitrate, self.sp_abitrate)
        layout.addLayout(audio_form)

        self.lbl_source = self.hint_label("")
        layout.addWidget(self.lbl_source)

        self.lbl_result = QLabel("")
        self.lbl_result.setObjectName("NoticeOk")
        self.lbl_result.setWordWrap(True)
        layout.addWidget(self.lbl_result)

        # ---------------- 信号 ----------------
        self.cb_target.currentIndexChanged.connect(self._on_target_changed)
        self.cb_container.currentIndexChanged.connect(self._on_container_changed)
        for widget in (self.cb_level, self.cb_acodec):
            widget.currentIndexChanged.connect(self._on_param_changed)
        self.video.changed.connect(self._on_param_changed)
        self.sp_target.valueChanged.connect(self._on_param_changed)
        self.sp_abitrate.valueChanged.connect(self._on_param_changed)
        self._on_target_changed()

    # ------------------------------------------------------------------
    @staticmethod
    def _container_muxer(ext: str) -> str:
        return {".mp4": "mp4", ".mkv": "matroska", ".mp3": "mp3",
                ".m4a": "ipod", ".flac": "flac", ".wav": "wav"}.get(ext, "mp4")

    def _container_ext(self) -> str:
        return str(self.cb_container.currentData() or ".mp4")

    def _is_video_target(self) -> bool:
        return (self.cb_target.currentData() or "video") == "video"

    def _fill_containers(self) -> None:
        previous = self.cb_container.currentData()
        options = VIDEO_CONTAINERS if self._is_video_target() else AUDIO_CONTAINERS
        self.cb_container.blockSignals(True)
        self.cb_container.clear()
        for value, label in options:
            self.cb_container.addItem(label, value)
        index = self.cb_container.findData(previous)
        self.cb_container.setCurrentIndex(index if index >= 0 else 0)
        self.cb_container.blockSignals(False)

    def _fill_audio_codecs(self) -> None:
        muxer = self._container_muxer(self._container_ext())
        previous = self.cb_acodec.currentData()
        choices = [item for item in audio_codec_choices(muxer) if item[0] != "none"]
        self.cb_acodec.blockSignals(True)
        self.cb_acodec.clear()
        for value, label in choices:
            self.cb_acodec.addItem(label, value)
        target = previous
        if target not in [value for value, _ in choices]:
            target = default_audio_codec(muxer)
        index = self.cb_acodec.findData(target)
        self.cb_acodec.setCurrentIndex(index if index >= 0 else 0)
        self.cb_acodec.blockSignals(False)

    def _on_target_changed(self) -> None:
        video = self._is_video_target()
        self._fill_containers()
        self._on_container_changed()

        for widget in (self.video_title, self.video, self.lbl_level, self.cb_level,
                       self.lbl_crf_row, self.lbl_crf, self.lbl_target_row, self.sp_target):
            widget.setVisible(video)
        self.lbl_acodec.setVisible(True)
        self.cb_acodec.setVisible(True)
        self.sp_abitrate.setVisible(True)
        self.lbl_abitrate.setVisible(True)
        self._on_param_changed()

    def _on_container_changed(self) -> None:
        self._fill_audio_codecs()
        muxer = self._container_muxer(self._container_ext())
        codec = self.cb_acodec.currentData() or ""
        # PCM / FLAC 无损，码率无意义
        show_rate = codec in ("aac", "mp3", "opus", "vorbis", "ac3")
        self.lbl_abitrate.setVisible(show_rate)
        self.sp_abitrate.setVisible(show_rate)
        if codec and muxer == "mp4" and codec not in ("aac", "copy"):
            self.set_notice("MP4 容器只建议使用 AAC 音轨，已自动切换到 AAC。", "warn")
        else:
            self.set_notice("", "info")
        self._on_param_changed()

    def _on_param_changed(self) -> None:
        codec = self.video.cb_codec.currentData() or "x264"
        level = self.cb_level.currentData() or "mid"
        crf = CRF_TABLE.get(codec, CRF_TABLE["x264"])[level]
        target = self.sp_target.value()
        self.lbl_crf.setText("目标体积模式（码率由体积反推）" if target > 0 else f"CRF {crf}")
        self._update_source_hint()
        self.schedule_preview()

    def _update_source_hint(self) -> None:
        paths = self.paths()
        if not paths:
            self.lbl_source.setText("")
            return
        info = self.probe(self.current_first())
        if not info or not info.ok:
            self.lbl_source.setText("")
            return
        rate = info.estimated_bit_rate()
        parts = [f"源文件：{human_size(info.size)}", f"码率 {human_bitrate(rate)}"]
        if info.has_video:
            parts.append(f"{info.resolution} @ {info.fps_text}fps")
        if info.has_audio:
            rate_text = human_bitrate(info.a_bit_rate) if info.a_bit_rate else ""
            parts.append(f"音频 {info.a_codec} {rate_text}".strip())
        self.lbl_source.setText("　|　".join(parts))
        if self._is_video_target() and rate and rate < 900_000 and not self.sp_target.value():
            self.set_notice(
                f"提示：该文件码率仅 {rate / 1000:.0f} kbps，接近网络视频水平，继续压缩收益有限且画质损失明显。",
                "warn",
            )
        elif self._is_video_target():
            self.set_notice("", "info")

    def _on_current_changed(self, path: str) -> None:
        super()._on_current_changed(path)
        self._update_source_hint()

    def _on_files_changed(self) -> None:
        super()._on_files_changed()
        self._update_source_hint()

    # ------------------------------------------------------------------
    def build_specs(self) -> list[OperationSpec]:
        paths = self.paths()
        if not paths:
            return []
        target = "video" if self._is_video_target() else "audio"
        ext = self._container_ext()
        values = self.video.values()
        codec = values["vcodec"] or "x264"
        level = self.cb_level.currentData() or "mid"
        target_mb = self.sp_target.value()
        acodec = self.cb_acodec.currentData() or default_audio_codec(
            self._container_muxer(ext))

        specs: list[OperationSpec] = []
        for src in paths:
            info = self.probe(src)
            if info is not None and not info.ok:
                continue
            if target == "video" and info is not None and not info.has_video:
                continue
            if target == "audio" and info is not None and not info.has_audio:
                continue
            suffix = "compressed" if target == "video" else "audio_compressed"
            out = self.make_output_path(src, suffix, ext)
            specs.append(
                OperationSpec(
                    op=OP_COMPRESS,
                    inputs=[src],
                    output=out,
                    params={
                        "target": target,
                        "container": ext,
                        "vcodec": codec,
                        "level": level,
                        "preset": values["preset"],
                        "target_mb": target_mb,
                        "acodec": acodec,
                        "abitrate_k": self.sp_abitrate.value(),
                    },
                    label=os.path.basename(src),
                    info=info,
                )
            )
        return specs

    # ------------------------------------------------------------------
    def on_job_result(self, ok: bool, message: str) -> None:
        if ok:
            self.lbl_result.setText(f"✔ 上一次压缩：{message}")
        else:
            self.lbl_result.setText("")


def _right_label(text: str) -> QLabel:
    label = QLabel(text)
    label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
    return label


__all__ = ["CompressPage"]
