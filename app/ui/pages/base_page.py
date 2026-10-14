"""功能页基类。

统一提供：
- 左列：文件列表（拖拽 + 批量）+ 文件信息面板
- 右列：可选的预览播放器 + 本页参数区（滚动）
- 底部：命令预览框
- 输出命名规则、参数变更防抖、命令预览自动刷新

子类只需要实现 ``_build_params()`` 与 ``build_specs()``。
"""

from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, QRunnable, QThreadPool, QTimer, Signal
from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ...core.builder import BuildError
from ...core.paths import ext_of, stem_of
from ...core.spec import OperationSpec
from ..context import AppContext
from ..widgets.command_box import CommandBox
from ..widgets.file_list import ALL_MEDIA, FileListGroup
from ..widgets.info_panel import InfoPanel
from ..widgets.preview_player import PreviewPlayer


class _BackgroundSignal(QObject):
    """后台任务的结果通道：跨线程 emit，队列连接回到主线程。"""

    done = Signal(object)


class _FnRunnable(QRunnable):
    def __init__(self, fn) -> None:
        super().__init__()
        self.fn = fn

    def run(self) -> None:  # pragma: no cover - 线程池内执行
        self.fn()


class BasePage(QWidget):
    requestStart = Signal()
    logMessage = Signal(str)
    # 文件被清空 / 页面状态被重置（问题 3：主窗口据此重置进度、结果、日志）
    stateReset = Signal()
    # 可运行条件可能发生变化（参数、输入内容等），主窗口据此刷新"开始"按钮
    runAvailabilityChanged = Signal()

    TITLE = "功能"
    USES_PREVIEW = False
    ALLOWED_EXTS = ALL_MEDIA
    ALLOW_REORDER = False
    FILE_HINT = "把文件或文件夹拖到这里；支持批量处理"
    LIST_HEIGHT = 150

    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.preview: PreviewPlayer | None = None

        # 参数变更 -> 命令预览防抖刷新（早于 _build_params 创建，
        # 因为子类在构建控件时就会调用 schedule_preview）
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(180)
        self._preview_timer.timeout.connect(self._do_refresh_preview)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 10, 12, 10)
        root.setSpacing(10)

        # ---------------- 左右两栏（可拖动分割，问题 8） ----------------
        columns = QSplitter(Qt.Horizontal)
        columns.setChildrenCollapsible(False)
        columns.setHandleWidth(8)

        # ---------------- 左列：文件 + 信息 ----------------
        files_group = QGroupBox("文件")
        files_layout = QVBoxLayout(files_group)
        files_layout.setContentsMargins(10, 14, 10, 10)
        self.files = FileListGroup(
            title="文件列表",
            allowed_exts=self.ALLOWED_EXTS,
            allow_reorder=self.ALLOW_REORDER,
            hint=self.FILE_HINT,
            height=self.LIST_HEIGHT,
        )
        files_layout.addWidget(self.files, 1)

        self.info_panel = InfoPanel()

        left_splitter = QSplitter(Qt.Vertical)
        left_splitter.setChildrenCollapsible(False)
        left_splitter.addWidget(files_group)
        left_splitter.addWidget(self.info_panel)
        left_splitter.setStretchFactor(0, 3)
        left_splitter.setStretchFactor(1, 2)
        left_splitter.setHandleWidth(8)

        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(0)
        left_layout.addWidget(left_splitter)
        left_widget.setMinimumWidth(280)

        # ---------------- 右列：预览 + 参数 ----------------
        self.params_group = QGroupBox("参数")
        self.params_layout = QVBoxLayout(self.params_group)
        self.params_layout.setContentsMargins(12, 16, 12, 12)
        self.params_layout.setSpacing(10)
        self._build_params(self.params_layout)
        self.params_layout.addStretch(1)
        self.params_group.setMinimumWidth(320)

        right_inner = QWidget()
        right_layout = QVBoxLayout(right_inner)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        right_layout.addWidget(self.params_group, 1)
        right_layout.addWidget(self._build_notice_widget(), 0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setWidget(right_inner)
        scroll.setMinimumWidth(330)

        # 右列自身用垂直分割器：预览在上、参数在下，预览画面可以拉大（用户实测问题 2）
        right_splitter = QSplitter(Qt.Vertical)
        right_splitter.setChildrenCollapsible(False)
        right_splitter.setHandleWidth(8)
        if self.USES_PREVIEW:
            self.preview = PreviewPlayer()
            self.preview.setMinimumHeight(190)
            right_splitter.addWidget(self.preview)
            self._wire_preview()
        right_splitter.addWidget(scroll)
        if self.USES_PREVIEW:
            # 默认给画面更多空间；参数区自身可滚动，压得下也不丢功能
            right_splitter.setStretchFactor(0, 5)
            right_splitter.setStretchFactor(1, 4)
            right_splitter.setSizes([340, 280])
        self.right_splitter = right_splitter

        columns.addWidget(left_widget)
        columns.addWidget(right_splitter)
        columns.setStretchFactor(0, 5)
        columns.setStretchFactor(1, 4)
        columns.setSizes([560, 460])
        self.columns = columns
        self.left_widget = left_widget
        self.params_scroll = scroll
        root.addWidget(columns, 1)

        # ---------------- 命令预览 ----------------
        self.command_box = CommandBox()
        root.addWidget(self.command_box, 0)

        # 参数变更 -> 命令预览防抖刷新
        self.files.filesChanged.connect(self._on_files_changed)
        self.files.currentChanged.connect(self._on_current_changed)
        self.files.filesDropped.connect(self._on_dropped)

        self.schedule_preview()
        self._update_columns_orientation()

    def resizeEvent(self, event) -> None:  # noqa: N802
        """窗口变窄时左右两栏自动改为上下堆叠，避免内容被裁掉（用户实测问题 3）。"""
        super().resizeEvent(event)
        self._update_columns_orientation()

    def _update_columns_orientation(self) -> None:
        """宽度放不下两栏就竖着排，任何窗口尺寸都不会出现控件互相遮挡。"""
        if not hasattr(self, "left_widget"):
            return
        need = (self.left_widget.minimumSizeHint().width()
                + self.params_scroll.minimumSizeHint().width()
                + self.columns.handleWidth() + 16)
        want = Qt.Vertical if self.width() < need else Qt.Horizontal
        if self.columns.orientation() == want:
            return
        self.columns.setOrientation(want)
        total = self.height() if want == Qt.Vertical else self.width()
        first = max(220, total // 2)
        self.columns.setSizes([first, max(220, total - first)])

    # ------------------------------------------------------------------
    # 子类需要实现的钩子
    # ------------------------------------------------------------------
    def _build_params(self, layout: QVBoxLayout) -> None:  # pragma: no cover - 抽象
        raise NotImplementedError

    def build_specs(self) -> list[OperationSpec]:  # pragma: no cover - 抽象
        raise NotImplementedError

    def notice_widget(self) -> QWidget | None:
        """子类可提供常驻提示（如"源码率偏低不建议压缩"）。默认无。"""
        return None

    def can_run(self) -> tuple[bool, str]:
        """开始处理前的前置检查（问题 5）。

        返回 ``(是否可执行, 不可执行的原因)``。子类可覆盖，例如合并页要求
        编码一致性检测先完成。
        """
        return True, ""

    def reset_state(self) -> None:
        """清空本页的临时状态（问题 3）。默认无额外状态。"""
        self.command_box.reset()

    # ------------------------------------------------------------------
    # 通用能力
    # ------------------------------------------------------------------
    @staticmethod
    def add_group_title(layout: QVBoxLayout, text: str) -> QLabel:
        """参数区分组标题 + 分隔线（问题 22）。"""
        label = QLabel(text)
        label.setObjectName("GroupTitle")
        layout.addWidget(label)
        return label

    @staticmethod
    def add_separator(layout: QVBoxLayout) -> None:
        from PySide6.QtWidgets import QFrame

        line = QFrame()
        line.setObjectName("GroupLine")
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        layout.addWidget(line)

    @staticmethod
    def hint_label(text: str, level: str = "info") -> QLabel:
        """统一风格的说明文字（无灰色背景，问题 15）。"""
        label = QLabel(text)
        label.setObjectName({
            "info": "Notice",
            "warn": "NoticeWarn",
            "danger": "NoticeDanger",
            "ok": "NoticeOk",
        }.get(level, "Notice"))
        label.setWordWrap(True)
        label.setMinimumWidth(0)
        return label
    def _build_notice_widget(self) -> QWidget:
        custom = self.notice_widget()
        if custom is not None:
            return custom
        self.notice = QLabel("")
        self.notice.setObjectName("NoticeWarn")
        self.notice.setWordWrap(True)
        self.notice.setVisible(False)
        return self.notice

    def set_notice(self, text: str, level: str = "warn") -> None:
        label = getattr(self, "notice", None)
        if label is None:
            return
        text = text or ""
        label.setText(text)
        label.setObjectName({
            "warn": "NoticeWarn",
            "danger": "NoticeDanger",
            "ok": "NoticeOk",
            "info": "Notice",
        }.get(level, "NoticeWarn"))
        label.style().unpolish(label)
        label.style().polish(label)
        label.setVisible(bool(text))

    def add_files(self, paths: list[str]) -> int:
        return self.files.add_files(paths)

    def paths(self) -> list[str]:
        return self.files.paths()

    def output_dir(self) -> str:
        return self.ctx.output_dir

    def output_dir_for(self, src: str = "") -> str:
        """实际输出目录：跟随源文件目录时优先用源目录（问题 14）。"""
        if getattr(self.ctx, "follow_source_dir", False) and src:
            directory = os.path.dirname(os.path.abspath(src))
            if directory and os.path.isdir(directory):
                return directory
        return self.ctx.output_dir

    @staticmethod
    def wrap_row(layout) -> QWidget:
        """把一行子布局包成 QWidget，方便塞进 QFormLayout。"""
        widget = QWidget()
        wrapper = QVBoxLayout(widget)
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.addLayout(layout)
        return widget

    def make_output_path(self, src: str, suffix: str, ext: str | None = None) -> str:
        """通用命名：原名_处理后缀.扩展名（需求 5.4）。"""
        out_dir = self.output_dir_for(src)
        base = stem_of(src)
        extension = ext if ext is not None else ext_of(src)
        if extension and not extension.startswith("."):
            extension = "." + extension
        return os.path.join(out_dir, f"{base}_{suffix}{extension}")

    def current_first(self) -> str:
        paths = self.paths()
        if not paths:
            return ""
        current = self.files.current_path()
        return current if current in paths else paths[0]

    def probe(self, path: str):
        return self.ctx.prober.get(path) if path else None

    def run_in_background(self, fn, on_done) -> None:
        """把耗时探测挪出 UI 线程：fn 在线程池执行，结果 (result, error)
        经队列信号回到主线程后调 ``on_done((result, error))``。

        适合关键帧扫描、批量 ffprobe 这类可能卡住界面几秒的操作。
        """
        holder = _BackgroundSignal(self)

        def deliver(payload) -> None:
            try:
                on_done(payload)
            finally:
                holder.deleteLater()

        holder.done.connect(deliver)

        def job() -> None:
            try:
                payload = (fn(), None)
            except Exception as exc:  # 探测失败按空结果处理，不中断界面
                payload = (None, exc)
            holder.done.emit(payload)

        QThreadPool.globalInstance().start(_FnRunnable(job))

    def schedule_preview(self) -> None:
        timer = getattr(self, "_preview_timer", None)
        if timer is not None:
            timer.start()

    def refresh_now(self) -> None:
        timer = getattr(self, "_preview_timer", None)
        if timer is not None:
            timer.stop()
        self._do_refresh_preview()

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _on_files_changed(self) -> None:
        paths = self.paths()
        self.info_panel.show_paths(paths, self.ctx.prober)
        if not paths:
            # 清空列表 → 通知主窗口重置进度/结果/日志（问题 3）
            self.reset_state()
            self.stateReset.emit()
        self.schedule_preview()

    def _on_current_changed(self, path: str) -> None:
        self.info_panel.show_paths([path] if path else [], self.ctx.prober)
        if self.preview is not None:
            info = self.ctx.prober.get(path) if path else None
            self.preview.load(
                path,
                duration_hint=(info.duration if info and info.ok else 0.0),
                audio_only=bool(info and info.ok and not info.has_video),
            )
        self.schedule_preview()

    def _on_dropped(self, files: list[str]) -> None:
        self.logMessage.emit(f"拖入 {len(files)} 个文件")

    def _wire_preview(self) -> None:
        if self.preview is None:
            return
        self.preview.useAsStart.connect(lambda seconds: self._apply_preview_mark("start", seconds))
        self.preview.useAsEnd.connect(lambda seconds: self._apply_preview_mark("end", seconds))
        self.preview.copyTimestamp.connect(self._copy_timestamp)

    def _apply_preview_mark(self, kind: str, seconds: float) -> None:
        """子类可覆盖，把预览时间填进对应输入框。"""
        self.logMessage.emit(f"预览时间 {seconds:.3f}s（本页不支持填入）")

    def _copy_timestamp(self, seconds: float) -> None:
        from PySide6.QtGui import QGuiApplication

        from ...core.timeparse import format_timestamp

        text = format_timestamp(seconds)
        QGuiApplication.clipboard().setText(text)
        self.logMessage.emit(f"已复制时间戳到剪贴板：{text}")

    def _do_refresh_preview(self) -> None:
        if not hasattr(self, "command_box"):
            return
        try:
            specs = self.build_specs()
        except BuildError as exc:
            self.command_box.set_command(f"# 参数不完整：{exc}")
            return
        except Exception as exc:  # 参数还在输入中途，属正常情况
            self.command_box.set_command(f"# 参数无效：{exc}")
            return

        if not specs:
            self.command_box.set_command("# 请先添加文件")
            return

        try:
            job = self.ctx.builder.build(specs[0])
        except BuildError as exc:
            self.command_box.set_command(f"# 参数不完整：{exc}")
            return

        text = job.preview()
        if len(specs) > 1:
            text = (
                f"# 当前共 {len(specs)} 个文件将依次处理，下面显示第 1 个；\n"
                f"# 其余文件输出名会自动编号，避免互相覆盖。\n\n" + text
            )
        extra = list(job.warnings) + list(job.notes)
        if extra:
            text += "\n\n# 说明：\n" + "\n".join(f"# · {item}" for item in extra)
        self.command_box.set_command(text)

    # ------------------------------------------------------------------
    def on_toolchain_changed(self) -> None:
        self.schedule_preview()

    def on_output_dir_changed(self) -> None:
        self.schedule_preview()


__all__ = ["BasePage"]
