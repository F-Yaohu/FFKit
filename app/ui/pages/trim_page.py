"""剪辑（Trim / Cut）—— 需求六.1。

三种（实际四种）模式：
- 无损切割：``-c copy``，快，切割点对齐关键帧，UI 明确提示精度代价。
- 精确切割：``-ss`` 前置 + 重编码。
- 绝对精确：``-ss`` 后置 + 重编码（帧级精确，慢）。
- 智能切割：仅首尾短段重编码，中间 ``-c copy``（P1）。

时长统一用 ``-t``（计算后的秒数）而非 ``-to``。
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
    QSpinBox,
    QVBoxLayout,
)

from ...core.builder import BuildError
from ...core.paths import AUDIO_EXTS, VIDEO_EXTS, ext_of, unique_output_multi
from ...core.spec import OP_TRIM, OperationSpec
from ...core.timeparse import filename_timestamp, format_hms, parse_time_safe
from .base_page import BasePage

MODES = [
    ("copy", "无损切割（-c copy，最快）"),
    ("precise", "精确切割（-ss 前置 + 重编码）"),
    ("absolute", "绝对精确（-ss 后置 + 重编码，最慢）"),
    ("smart", "智能切割（首尾重编码 + 中间无损）"),
]

MODE_HINTS = {
    "copy": "无损切割不做重编码，速度极快；代价是切割点会自动对齐到最近的关键帧，最大偏差约一个 GOP（通常 1~5 秒）。要精确到帧请改用下面的模式。",
    "precise": "精确切割：快速定位 + 重编码，速度与精度平衡，是日常最常用的选择。",
    "absolute": "绝对精确：从文件开头逐帧解码到起点，帧级精确但速度最慢，仅在「首帧不准」时才需要。",
    "smart": "智能切割：只有首尾两个关键帧之间的一小段被重编码，中间部分无损复制，兼顾速度与精度。接缝处可能有极轻微跳变。",
}

CONTAINER_CHOICES = [
    ("same", "与源文件相同"),
    (".mp4", "MP4 (.mp4)"),
    (".mkv", "MKV (.mkv)"),
    (".mp3", "MP3 (.mp3)"),
    (".m4a", "M4A (.m4a)"),
    (".wav", "WAV (.wav)"),
]


class TrimPage(BasePage):
    TITLE = "剪辑"
    USES_PREVIEW = True
    ALLOWED_EXTS = VIDEO_EXTS | AUDIO_EXTS
    FILE_HINT = "拖入视频/音频文件（支持批量）；用右侧播放器定位时间点"
    LIST_HEIGHT = 140

    def _build_params(self, layout: QVBoxLayout) -> None:
        # ---------------- 1. 切割模式 ----------------
        self.add_group_title(layout, "切割模式")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)

        self.cb_mode = QComboBox()
        for _key, label in MODES:
            self.cb_mode.addItem(label)
        self.cb_mode.setCurrentIndex(1)
        form.addRow("切割模式", self.cb_mode)
        layout.addLayout(form)

        self.lbl_mode_hint = self.hint_label(MODE_HINTS["precise"])
        layout.addWidget(self.lbl_mode_hint)

        # ---------------- 2. 时间范围 ----------------
        self.add_separator(layout)
        self.add_group_title(layout, "时间范围")
        time_form = QFormLayout()
        time_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        time_form.setSpacing(8)

        self.ed_start = QLineEdit()
        self.ed_start.setPlaceholderText("如 00:01:23 / 01:23 / 83")
        self.btn_now_start = QPushButton("取当前")
        self.btn_now_start.setObjectName("Flat")
        self.btn_now_start.setToolTip("把右侧预览播放器的当前位置填入开始时间")
        start_row = QHBoxLayout()
        start_row.addWidget(self.ed_start, 1)
        start_row.addWidget(self.btn_now_start)
        time_form.addRow("开始时间", self.wrap_row(start_row))

        self.ed_end = QLineEdit()
        self.ed_end.setPlaceholderText("如 00:02:10 / 130")
        self.btn_now_end = QPushButton("取当前")
        self.btn_now_end.setObjectName("Flat")
        self.btn_now_end.setToolTip("把右侧预览播放器的当前位置填入结束时间")
        self.btn_full = QPushButton("从头到尾")
        self.btn_full.setObjectName("Flat")
        self.btn_full.setToolTip("开始时间置 0，结束时间取整个文件时长")
        end_row = QHBoxLayout()
        end_row.addWidget(self.ed_end, 1)
        end_row.addWidget(self.btn_now_end)
        end_row.addWidget(self.btn_full)
        time_form.addRow("结束时间", self.wrap_row(end_row))

        self.lbl_duration = QLabel("时长：-")
        self.lbl_duration.setObjectName("SectionTitle")
        time_form.addRow("", self.lbl_duration)
        layout.addLayout(time_form)

        self.lbl_keyframe = self.hint_label("", "warn")
        layout.addWidget(self.lbl_keyframe)

        # ---------------- 3. 输出与编码 ----------------
        self.add_separator(layout)
        self.add_group_title(layout, "输出与编码")
        out_form = QFormLayout()
        out_form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        out_form.setSpacing(8)

        self.cb_container = QComboBox()
        for _value, label in CONTAINER_CHOICES:
            self.cb_container.addItem(label)
        out_form.addRow("输出格式", self.cb_container)

        self.cb_codec = QComboBox()
        self.cb_codec.addItem("H.264 (兼容性最好)", "x264")
        self.cb_codec.addItem("H.265 (体积更小)", "x265")
        self.lbl_codec = self._right_label("重编码编码器")
        out_form.addRow(self.lbl_codec, self.cb_codec)

        self.sp_crf = QSpinBox()
        self.sp_crf.setRange(0, 51)
        self.sp_crf.setValue(20)
        self.sp_crf.setToolTip("数值越小画质越好、体积越大。20 接近视觉无损，23 为通用默认。")
        self.lbl_crf = self._right_label("CRF 质量")
        out_form.addRow(self.lbl_crf, self.sp_crf)

        self.cb_preset = QComboBox()
        for name in ("ultrafast", "veryfast", "faster", "fast", "medium", "slow", "slower"):
            self.cb_preset.addItem(name)
        self.cb_preset.setCurrentText("medium")
        self.lbl_preset = self._right_label("编码速度")
        out_form.addRow(self.lbl_preset, self.cb_preset)
        layout.addLayout(out_form)

        self._out_form = out_form

        # ---- 信号 ----
        self.cb_mode.currentIndexChanged.connect(self._on_mode_changed)
        self.cb_container.currentIndexChanged.connect(self.schedule_preview)
        self.cb_codec.currentIndexChanged.connect(self.schedule_preview)
        self.sp_crf.valueChanged.connect(self.schedule_preview)
        self.cb_preset.currentIndexChanged.connect(self.schedule_preview)
        for edit in (self.ed_start, self.ed_end):
            edit.textChanged.connect(self._on_time_edited)
            # 时间输入会改变 can_run() 的结论 → 刷新"开始"按钮
            edit.textChanged.connect(self.runAvailabilityChanged)
        self.cb_mode.currentIndexChanged.connect(self.runAvailabilityChanged)
        self.btn_now_start.clicked.connect(lambda: self._mark("start"))
        self.btn_now_end.clicked.connect(lambda: self._mark("end"))
        self.btn_full.clicked.connect(self._fill_full_range)

        self._on_mode_changed()

    @staticmethod
    def _right_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        return label

    # ------------------------------------------------------------------
    def _on_mode_changed(self) -> None:
        mode = MODES[self.cb_mode.currentIndex()][0]
        self.lbl_mode_hint.setText(MODE_HINTS[mode])
        needs_encode = mode in ("precise", "absolute", "smart")
        for widget in (self.lbl_codec, self.cb_codec, self.lbl_crf, self.sp_crf,
                       self.lbl_preset, self.cb_preset):
            widget.setVisible(needs_encode)
        self.schedule_preview()

    def can_run(self) -> tuple[bool, str]:
        """时间参数不合法时不让开始（问题 5 的同款前置检查）。"""
        start, err1 = parse_time_safe(self.ed_start.text())
        end, err2 = parse_time_safe(self.ed_end.text())
        if err1:
            return False, f"开始时间：{err1}"
        if err2:
            return False, f"结束时间：{err2}"
        if start is None or end is None:
            return False, "请填写开始时间与结束时间"
        if end <= start:
            return False, "结束时间必须大于开始时间"
        return True, ""

    def _on_time_edited(self, *_args) -> None:
        self._update_duration_label()
        self.schedule_preview()

    def _update_duration_label(self) -> None:
        start, err1 = parse_time_safe(self.ed_start.text())
        end, err2 = parse_time_safe(self.ed_end.text())
        if err1 or err2:
            self.lbl_duration.setText(f"时长：—  {err1 or err2}")
            self.set_notice("", "info")
            return
        if start is None or end is None:
            self.lbl_duration.setText("时长：-")
            self.set_notice("", "info")
            return
        if end <= start:
            self.lbl_duration.setText("时长：—")
            self.set_notice("结束时间必须大于开始时间", "danger")
            return
        info = self.probe(self.current_first())
        text = f"时长：{format_hms(end - start, ms=True)}（{end - start:.3f} 秒）"
        if info and info.duration and end > info.duration + 0.5:
            text += f"　⚠ 超出视频总时长 {format_hms(info.duration)}，将按实际结尾截断"
        self.lbl_duration.setText(text)
        self.set_notice("", "info")

    def _mark(self, kind: str) -> None:
        if self.preview is None:
            return
        seconds = self.preview.position()
        self._apply_preview_mark(kind, seconds)

    def _apply_preview_mark(self, kind: str, seconds: float) -> None:
        text = format_hms(seconds, ms=True)
        if kind == "start":
            self.ed_start.setText(text)
            if not self.ed_end.text().strip():
                info = self.probe(self.current_first())
                if info and info.duration:
                    self.ed_end.setText(format_hms(info.duration, ms=True))
        else:
            self.ed_end.setText(text)

    def _fill_full_range(self) -> None:
        info = self.probe(self.current_first())
        self.ed_start.setText("0")
        self.ed_end.setText(format_hms(info.duration, ms=True) if info and info.duration else "")

    # ------------------------------------------------------------------
    def _on_current_changed(self, path: str) -> None:
        super()._on_current_changed(path)
        info = self.probe(path)
        if info and info.duration and not self.ed_end.text().strip():
            self.ed_end.setText(format_hms(info.duration, ms=True))
        self._update_duration_label()
        self._update_keyframe_hint()

    def _on_files_changed(self) -> None:
        super()._on_files_changed()
        self._update_duration_label()
        self._update_keyframe_hint()

    def _update_keyframe_hint(self) -> None:
        """无损/智能切割下，提前把"实际会切在哪里"算给用户看（后台扫描，不卡界面）。"""
        mode = MODES[self.cb_mode.currentIndex()][0]
        if mode not in ("copy", "smart"):
            self.lbl_keyframe.setText("")
            return
        path = self.current_first()
        info = self.probe(path)
        if not info or not info.has_video:
            self.lbl_keyframe.setText("")
            return
        start, _ = parse_time_safe(self.ed_start.text())
        if start is None or self.ctx.prober is None:
            self.lbl_keyframe.setText("")
            return
        # 关键帧扫描要解码整文件的包索引，特大文件只提示、不预扫
        try:
            if os.path.getsize(path) > 1500 * 1024 * 1024:
                self.lbl_keyframe.setText(
                    "提示：文件较大，已跳过关键帧预检；无损切割的实际起点由 ffmpeg 自动对齐。"
                )
                return
        except OSError:
            return
        # 同一个文件正在扫描时不要重复起线程
        if getattr(self, "_kf_scanning", None) == path:
            return
        self._kf_scanning = path
        self._kf_token = getattr(self, "_kf_token", 0) + 1
        token = self._kf_token
        self.lbl_keyframe.setText("正在扫描关键帧…")
        self.run_in_background(
            lambda: self.ctx.prober.keyframes(path),
            lambda payload, token=token, path=path, start=start:
                self._on_keyframes_ready(payload, token, path, start),
        )

    def _on_keyframes_ready(self, payload, token: int, path: str, start: float) -> None:
        self._kf_scanning = None
        if token != getattr(self, "_kf_token", 0):
            return  # 过期结果：文件/模式又变了，交给最新一次扫描
        if self.current_first() != path:
            return
        result, _error = payload
        keyframes = result or []
        if not keyframes:
            self.lbl_keyframe.setText("提示：未能解析关键帧列表，无损切割的实际起点由 ffmpeg 自动对齐。")
            return
        import bisect

        index = bisect.bisect_left(keyframes, start - 0.05)
        if index < len(keyframes):
            actual = keyframes[index]
            delta = abs(actual - start)
            if delta <= 0.05:
                self.lbl_keyframe.setText(f"✔ 起点 {format_hms(start, ms=True)} 正好落在关键帧上，本次切割不会有偏差。")
            else:
                self.lbl_keyframe.setText(
                    f"⚠ 无损切割实际起点将变为 {format_hms(actual, ms=True)}，"
                    f"比设定值晚 {delta:.2f} 秒。要精确请选「精确切割」。"
                )
        else:
            self.lbl_keyframe.setText("")

    # ------------------------------------------------------------------
    def build_specs(self) -> list[OperationSpec]:
        paths = self.paths()
        if not paths:
            return []

        start, err1 = parse_time_safe(self.ed_start.text())
        end, err2 = parse_time_safe(self.ed_end.text())
        if err1:
            raise BuildError(f"开始时间：{err1}")
        if err2:
            raise BuildError(f"结束时间：{err2}")
        if start is None or end is None:
            raise BuildError("请填写开始时间与结束时间")
        if end <= start:
            raise BuildError("结束时间必须大于开始时间")

        mode = MODES[self.cb_mode.currentIndex()][0]
        choice = CONTAINER_CHOICES[self.cb_container.currentIndex()][0]
        codec = self.cb_codec.currentData() or "x264"
        crf = self.sp_crf.value()
        preset = self.cb_preset.currentText()

        specs: list[OperationSpec] = []
        usable: list[tuple[str, object]] = []
        for src in paths:
            info = self.probe(src)
            if info is not None and not info.ok:
                continue
            usable.append((src, info))

        outputs: list[str] = []
        for src, info in usable:
            ext = self._resolve_ext(src, info, choice, mode)
            suffix = f"trim_{filename_timestamp(start)}_to_{filename_timestamp(end)}"
            outputs.append(self.make_output_path(src, suffix, ext))

        resolved = unique_output_multi(outputs)
        for (src, info), out in zip(usable, resolved):
            specs.append(
                OperationSpec(
                    op=OP_TRIM,
                    inputs=[src],
                    output=out,
                    params={
                        "mode": mode,
                        "start": start,
                        "end": end,
                        "vcodec": codec,
                        "crf": crf,
                        "preset": preset,
                    },
                    label=os.path.basename(src),
                    info=info,
                )
            )
        return specs

    def _resolve_ext(self, src: str, info, choice: str, mode: str) -> str:
        src_ext = ext_of(src)
        if choice != "same":
            # 无损切割必须保持容器家族，否则流复制会失败
            if mode == "copy":
                if info and info.is_audio_only:
                    return src_ext
                if choice in (".mp3", ".m4a", ".wav") and not (info and info.is_audio_only):
                    return src_ext
                if choice in (".mp4", ".mkv") and info and info.is_audio_only:
                    return src_ext
            return choice
        if src_ext:
            return src_ext
        return ".mp4"

    def on_output_dir_changed(self) -> None:
        super().on_output_dir_changed()


__all__ = ["CONTAINER_CHOICES", "MODES", "TrimPage"]
