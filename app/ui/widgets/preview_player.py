"""预览播放器（P0）：QtMultimedia 为主，系统默认播放器为降级路径（需求十.1）。

本轮按实测反馈（问题 7）补齐：
- 进度条**立即可拖**：用 ffprobe 的时长做兜底，不必等播放器解码出元数据
- 时间显示可编辑：直接输入 00:01:23 回车跳转
- 音量控制
- 控制按钮图标随状态切换、风格统一
- 无法播放（HEVC/10bit/mkv）时自动把"用系统默认播放器打开"高亮出来
- 提取页等纯音频场景明确提示"无画面可预览"，不再让用户对着一块黑屏猜

QtMultimedia 在 PySide6 6.12 的 Windows 上走 FFmpeg 后端，能播的格式比 WMF 时代宽，
但 10bit / 部分 mkv 仍可能失败 —— 所以降级按钮依然是 P0 的一部分。
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from ...core.paths import ext_of
from ...core.timeparse import format_timestamp, parse_time_safe

try:  # pragma: no cover - 取决于运行环境
    from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    from PySide6.QtMultimediaWidgets import QVideoWidget

    MULTIMEDIA_AVAILABLE = True
    MULTIMEDIA_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover
    MULTIMEDIA_AVAILABLE = False
    MULTIMEDIA_IMPORT_ERROR = str(exc)

# 这些格式内置播放器大概率播不了，加载前就先提示
RISKY_EXTS = {".mkv", ".webm", ".flv", ".ts", ".mov", ".avi", ".wmv", ".m4v", ".hevc"}


class PreviewPlayer(QGroupBox):
    positionChanged = Signal(float)
    durationChanged = Signal(float)
    useAsStart = Signal(float)
    useAsEnd = Signal(float)
    copyTimestamp = Signal(float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("预览播放器", parent)
        self._path = ""
        self._duration = 0.0
        self._duration_hint = 0.0
        self._position = 0.0
        self._scrubbing = False
        self._audio_only = False
        self._failed = False
        self._player = None
        self._audio = None
        self._video = None

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 12, 8, 8)
        root.setSpacing(4)

        self.stage = QWidget(self)
        # 画面区完全弹性：空间富余时吃掉所有多余高度（画面尽可能大），
        # 空间紧张时最先被压缩，保证控制行不互相重叠（用户实测问题 2/3）。
        self.stage.setMinimumHeight(0)
        policy = self.stage.sizePolicy()
        policy.setVerticalPolicy(QSizePolicy.Expanding)
        policy.setHorizontalPolicy(QSizePolicy.Expanding)
        self.stage.setSizePolicy(policy)
        stage_layout = QVBoxLayout(self.stage)
        stage_layout.setContentsMargins(0, 0, 0, 0)
        root.addWidget(self.stage, 1)

        self.placeholder = QLabel("选择文件后可在此预览")
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setObjectName("Notice")
        self.placeholder.setWordWrap(True)
        self.placeholder.setMinimumHeight(56)
        stage_layout.addWidget(self.placeholder)

        # ---- 进度条 ----
        self.slider = QSlider(Qt.Horizontal, self)
        self.slider.setRange(0, 0)
        self.slider.setToolTip("可直接拖动定位；无需先播放")
        root.addWidget(self.slider)

        # ---- 时间显示 + 跳转输入 ----
        time_row = QHBoxLayout()
        time_row.setSpacing(4)
        self.time_label = QLabel("00:00:00.000 / 00:00:00.000")
        self.time_label.setObjectName("SectionTitle")
        time_row.addWidget(self.time_label)
        time_row.addStretch(1)
        self.state_label = QLabel("")
        self.state_label.setObjectName("NoticeWarn")
        time_row.addWidget(self.state_label)
        root.addLayout(time_row)

        jump_row = QHBoxLayout()
        jump_row.setSpacing(4)
        jump_label = QLabel("跳到")
        jump_row.addWidget(jump_label)
        self.ed_jump = QLineEdit()
        self.ed_jump.setPlaceholderText("00:01:23 / 83")
        self.ed_jump.setToolTip("输入时间后回车跳转，如 00:01:23 或 83")
        self.ed_jump.setMinimumWidth(56)
        self.ed_jump.setMaximumWidth(170)
        self.btn_jump = QPushButton("跳转")
        jump_row.addWidget(self.ed_jump)
        jump_row.addWidget(self.btn_jump)
        jump_row.addStretch(1)

        vol_label = QLabel("音量")
        jump_row.addWidget(vol_label)
        self.slider_volume = QSlider(Qt.Horizontal)
        self.slider_volume.setRange(0, 100)
        self.slider_volume.setValue(80)
        self.slider_volume.setFixedWidth(76)
        self.slider_volume.setToolTip("音量")
        jump_row.addWidget(self.slider_volume)
        self.lbl_volume = QLabel("80%")
        self.lbl_volume.setObjectName("Notice")
        jump_row.addWidget(self.lbl_volume)
        root.addLayout(jump_row)

        # ---- 控制按钮（图标统一为符号，随状态切换）----
        # 按钮文案刻意压缩到最短（悬停有完整提示），否则最小宽度会超过
        # 参数栏，把整页布局撑爆（用户实测：小窗口下右侧被裁掉）。
        controls = QHBoxLayout()
        controls.setSpacing(3)
        self.btn_play = QPushButton("▶")
        self.btn_play.setToolTip("播放 / 暂停")
        self.btn_back = QPushButton("⏪ 1s")
        self.btn_back.setToolTip("后退 1 秒")
        self.btn_fwd = QPushButton("1s ⏩")
        self.btn_fwd.setToolTip("前进 1 秒")
        self.btn_prev_frame = QPushButton("◀ 帧")
        self.btn_prev_frame.setToolTip("上一帧")
        self.btn_next_frame = QPushButton("帧 ▶")
        self.btn_next_frame.setToolTip("下一帧")
        for btn in (self.btn_play, self.btn_back, self.btn_fwd,
                    self.btn_prev_frame, self.btn_next_frame):
            btn.setObjectName("Flat")
            controls.addWidget(btn)
        controls.addStretch(1)
        root.addLayout(controls)

        marks = QHBoxLayout()
        marks.setSpacing(3)
        self.btn_start = QPushButton("设为开始")
        self.btn_start.setToolTip("把当前时间填入「开始时间」")
        self.btn_end = QPushButton("设为结束")
        self.btn_end.setToolTip("把当前时间填入「结束时间」")
        self.btn_copy_ts = QPushButton("复制时间")
        self.btn_copy_ts.setToolTip("复制当前时间戳")
        for btn in (self.btn_start, self.btn_end, self.btn_copy_ts):
            btn.setObjectName("Flat")
            marks.addWidget(btn)
        marks.addStretch(1)
        root.addLayout(marks)

        # ---- 降级路径：无法内置播放时自动高亮 ----
        self.btn_system = QPushButton("用系统默认播放器打开")
        self.btn_system.setToolTip(
            "QtMultimedia 无法解码时，用系统播放器打开原文件预览（不修改原文件）"
        )
        self.btn_system.setObjectName("Flat")
        root.addWidget(self.btn_system)

        self._build_backend()
        self._wire()
        self.set_enabled_state(False)
        self._set_fallback_visible(False)

    # ------------------------------------------------------------------
    def _build_backend(self) -> None:
        if not MULTIMEDIA_AVAILABLE:
            self.placeholder.setText(
                "当前环境没有可用的 QtMultimedia 组件。\n"
                "仍可用下方「用系统默认播放器打开」预览。"
            )
            self.state_label.setText("预览不可用")
            self._set_fallback_visible(True)
            return
        try:
            self._player = QMediaPlayer(self)
            self._audio = QAudioOutput(self)
            self._player.setAudioOutput(self._audio)
            self._audio.setVolume(self.slider_volume.value() / 100.0)
            self._video = QVideoWidget(self.stage)
            try:  # 保持等比，不拉伸（问题 7）
                self._video.setAspectRatioMode(Qt.KeepAspectRatio)
            except Exception:
                pass
            self._player.setVideoOutput(self._video)
            layout = self.stage.layout()
            layout.addWidget(self._video)
            self._video.setVisible(False)
        except Exception as exc:  # pragma: no cover
            self._player = None
            self.placeholder.setText(f"初始化预览组件失败：{exc}\n请使用系统默认播放器预览。")
            self.state_label.setText("预览不可用")
            self._set_fallback_visible(True)

    def _wire(self) -> None:
        self.btn_play.clicked.connect(self.toggle_play)
        self.btn_back.clicked.connect(lambda: self._nudge(-1.0))
        self.btn_fwd.clicked.connect(lambda: self._nudge(1.0))
        self.btn_prev_frame.clicked.connect(lambda: self._nudge(-1 / 25))
        self.btn_next_frame.clicked.connect(lambda: self._nudge(1 / 25))
        self.btn_start.clicked.connect(lambda: self.useAsStart.emit(self.position()))
        self.btn_end.clicked.connect(lambda: self.useAsEnd.emit(self.position()))
        self.btn_copy_ts.clicked.connect(lambda: self.copyTimestamp.emit(self.position()))
        self.btn_system.clicked.connect(self.open_in_system_player)
        self.slider.sliderPressed.connect(self._on_slider_pressed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self.btn_jump.clicked.connect(self._on_jump)
        self.ed_jump.returnPressed.connect(self._on_jump)
        self.slider_volume.valueChanged.connect(self._on_volume)

        if self._player is not None:
            self._player.positionChanged.connect(self._on_position)
            self._player.durationChanged.connect(self._on_duration)
            self._player.errorOccurred.connect(self._on_error)
            self._player.mediaStatusChanged.connect(self._on_media_status)
            self._player.playbackStateChanged.connect(self._on_playback_state)

    # ------------------------------------------------------------------
    # 对外 API
    # ------------------------------------------------------------------
    def load(self, path: str, duration_hint: float = 0.0, audio_only: bool = False) -> None:
        path = path or ""
        self._duration_hint = max(0.0, float(duration_hint or 0.0))
        self._audio_only = bool(audio_only)
        if path == self._path and self._duration:
            return
        self._path = path
        self._position = 0.0
        self._duration = 0.0
        self._failed = False
        self.slider.setRange(0, 0)
        self.time_label.setText("00:00:00.000 / 00:00:00.000")
        self.set_enabled_state(bool(path))
        self._set_fallback_visible(False)

        if not path or not os.path.isfile(path):
            self._show_placeholder("选择文件后可在此预览")
            return

        # 关键：先用 ffprobe 的时长把进度条撑起来 → 加载即可拖动（问题 7）
        if self._duration_hint:
            self._apply_duration(self._duration_hint)

        if self._player is None:
            self._show_placeholder(
                f"{os.path.basename(path)}\n当前环境无法内嵌预览，请使用系统默认播放器。"
            )
            self._set_fallback_visible(True)
            return

        self._player.stop()
        self._player.setSource(QUrl.fromLocalFile(path))
        if self._audio_only:
            self._show_placeholder(
                f"纯音频文件：{os.path.basename(path)}\n音频没有画面，可用下方进度条试听与定位。"
            )
            self.state_label.setText("纯音频")
            return
        self._video.setVisible(True)
        self.placeholder.setVisible(False)
        if ext_of(path) in RISKY_EXTS:
            self.state_label.setText("该格式可能无法预览，若失败请用系统播放器")
        else:
            self.state_label.setText("")

    def clear(self) -> None:
        self.load("")

    def position(self) -> float:
        if self._player is not None and not self._scrubbing:
            return self._player.position() / 1000.0
        return self._position

    def duration(self) -> float:
        return self._duration or self._duration_hint

    def seek(self, seconds: float) -> None:
        seconds = max(0.0, min(float(seconds), self.duration() or 0.0))
        self._position = seconds
        if self._player is not None and not self._failed:
            self._player.setPosition(int(seconds * 1000))
        self.slider.setValue(int(seconds * 1000))
        self._refresh_time_label()
        self.positionChanged.emit(seconds)

    def toggle_play(self) -> None:
        if self._player is None or not self._path:
            self.open_in_system_player()
            return
        if self._failed:
            self.open_in_system_player()
            return
        if self._player.playbackState() == QMediaPlayer.PlayingState:
            self._player.pause()
        else:
            self._player.play()

    def open_in_system_player(self) -> None:
        if not self._path or not os.path.isfile(self._path):
            return
        if os.name == "nt":
            try:
                os.startfile(self._path)  # type: ignore[attr-defined]
                return
            except OSError:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(self._path))

    def set_enabled_state(self, enabled: bool) -> None:
        for widget in (
            self.btn_play, self.btn_back, self.btn_fwd, self.btn_prev_frame, self.btn_next_frame,
            self.btn_start, self.btn_end, self.btn_copy_ts, self.slider,
            self.ed_jump, self.btn_jump, self.slider_volume,
        ):
            widget.setEnabled(enabled)
        self.btn_system.setEnabled(bool(self._path))

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _apply_duration(self, seconds: float) -> None:
        if seconds <= 0:
            return
        self._duration = max(self._duration, float(seconds))
        self.slider.setRange(0, int(self._duration * 1000))
        self._refresh_time_label()
        self.durationChanged.emit(self._duration)

    def _set_fallback_visible(self, visible: bool) -> None:
        self.btn_system.setObjectName("Primary" if visible else "Flat")
        self.btn_system.setVisible(bool(self._path) or visible)
        self.btn_system.style().unpolish(self.btn_system)
        self.btn_system.style().polish(self.btn_system)

    def _show_placeholder(self, text: str) -> None:
        self.placeholder.setText(text)
        self.placeholder.setVisible(True)
        if self._video is not None:
            self._video.setVisible(False)

    def _nudge(self, delta: float) -> None:
        self.seek(self.position() + delta)

    def _on_jump(self) -> None:
        value, err = parse_time_safe(self.ed_jump.text())
        if value is None:
            self.state_label.setText(err or "时间格式不对")
            return
        self.seek(value)
        self.state_label.setText("")

    def _on_volume(self, value: int) -> None:
        self.lbl_volume.setText(f"{value}%")
        if self._audio is not None:
            self._audio.setVolume(max(0.0, min(1.0, value / 100.0)))

    def _on_slider_pressed(self) -> None:
        self._scrubbing = True

    def _on_slider_moved(self, value: int) -> None:
        self._position = value / 1000.0
        self._refresh_time_label()

    def _on_slider_released(self) -> None:
        self._scrubbing = False
        self.seek(self.slider.value() / 1000.0)

    def _on_position(self, ms: int) -> None:
        if self._scrubbing:
            return
        self._position = ms / 1000.0
        self.slider.setValue(ms)
        self._refresh_time_label()
        self.positionChanged.emit(self._position)

    def _on_duration(self, ms: int) -> None:
        if ms:
            self._apply_duration(ms / 1000.0)
        if self._duration:
            self.state_label.setText("")

    def _refresh_time_label(self) -> None:
        self.time_label.setText(
            f"{format_timestamp(self._position)} / {format_timestamp(self.duration())}"
        )

    def _on_playback_state(self, state) -> None:
        if self._player is None:
            return
        if state == QMediaPlayer.PlayingState:
            self.btn_play.setText("⏸")
            self.btn_play.setToolTip("暂停")
        else:
            self.btn_play.setText("▶")
            self.btn_play.setToolTip("播放 / 暂停")

    def _on_media_status(self, status) -> None:
        if self._player is None:
            return
        if status == QMediaPlayer.InvalidMedia:
            self._mark_failed("该文件无法用内置播放器解码（常见于 HEVC / 10bit / mkv）")

    def _on_error(self, error, message: str = "") -> None:
        if error == QMediaPlayer.NoError:
            return
        self._mark_failed(message or "内置播放器无法解码该文件")

    def _mark_failed(self, reason: str) -> None:
        if self._failed:
            return
        self._failed = True
        self._show_placeholder(
            f"{reason}。\n请点击下方高亮按钮用系统默认播放器预览，或直接按时间参数处理。"
        )
        self.state_label.setText("内置预览不可用")
        # 时长仍用 ffprobe 的值，保证进度条/时间点选择依然可用
        if self._duration_hint:
            self.slider.setEnabled(True)
        self._set_fallback_visible(True)


__all__ = ["MULTIMEDIA_AVAILABLE", "PreviewPlayer"]
