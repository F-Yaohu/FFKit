"""主窗口：单窗口 + 左侧功能列表 + 右侧参数区（需求二 / 七）。

同时负责：
- 拖拽语义（需求 5.8）：拖到合并页 = 加入可排序列表；拖到其他页 = 加入批量队列
- 输出目录记忆、"打开所在文件夹"
- 输出冲突策略（自动追加序号 / 弹窗确认覆盖）
- 批量预检（全局错误拦截 + 单文件错误跳过）
- 执行结束后展示失败清单与体积对比
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QSize
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import log_max_lines
from ..core.builder import BuildError
from ..core.paths import is_writable_dir, unique_output_multi
from ..core.queue import QueueSummary, TaskQueue
from ..core.spec import Job
from ..core.validator import Severity
from .context import AppContext
from .dialogs import SettingsDialog
from .pages.base_page import BasePage
from .pages.compress_page import CompressPage
from .pages.convert_page import ConvertPage
from .pages.extract_page import ExtractPage
from .pages.merge_page import MergePage
from .pages.props_page import PropsPage
from .pages.track_page import TrackPage
from .pages.trim_page import TrimPage
from .theme import stylesheet
from .widgets.file_list import collect_media_files
from .widgets.log_panel import LogPanel

PAGE_CLASSES = [TrimPage, MergePage, TrackPage, ConvertPage, ExtractPage, CompressPage, PropsPage]


@dataclass
class RunPlan:
    jobs: list[Job]
    pages: list[BasePage]


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("FFKit · ffmpeg 图形化外壳")
        self.setMinimumSize(1000, 660)  # 问题 8：防止低分辨率下控件重叠
        self.setAcceptDrops(True)
        try:
            from ..resources import app_icon

            self.setWindowIcon(app_icon())
        except Exception:
            pass  # 图标缺失不影响程序启动

        self.queue = TaskQueue(ctx.validator, self)
        self._run_pages: list[BasePage] = []
        self._current_job_index = 0
        self._last_summary: QueueSummary | None = None

        self._build_ui()
        self._wire_queue()
        self._restore_geometry()
        self._refresh_toolchain(initial=True)
        self._update_run_availability()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 顶部提示条（ffmpeg 缺失时显示） ----
        self.banner = QWidget()
        banner_layout = QHBoxLayout(self.banner)
        banner_layout.setContentsMargins(12, 8, 12, 8)
        banner_layout.setSpacing(10)
        self.banner_label = QLabel("")
        self.banner_label.setObjectName("Banner")
        self.banner_label.setWordWrap(True)
        banner_layout.addWidget(self.banner_label, 1)
        self.btn_pick_ffmpeg = QPushButton("手动指定 ffmpeg…")
        self.btn_pick_ffmpeg.setObjectName("Primary")
        self.btn_recheck = QPushButton("重新检测")
        banner_layout.addWidget(self.btn_pick_ffmpeg)
        banner_layout.addWidget(self.btn_recheck)
        root.addWidget(self.banner)

        # ---- 主体：导航 + 页面栈 ----
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)

        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        self.nav.setFixedWidth(158)
        self.nav.setIconSize(QSize(18, 18))

        self.stack = QStackedWidget()
        self.pages: list[BasePage] = []
        for cls in PAGE_CLASSES:
            page = cls(self.ctx)
            page.requestStart.connect(self.start_run)
            page.logMessage.connect(self.log)
            page.stateReset.connect(self._on_page_state_reset)
            page.files.filesChanged.connect(self._update_run_availability)
            page.runAvailabilityChanged.connect(self._update_run_availability)
            self.pages.append(page)
            self.stack.addWidget(page)
            item = QListWidgetItem(self._nav_text(cls.TITLE))
            self.nav.addItem(item)

        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)

        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        # ---- 底部控制区 ----
        bottom = QWidget()
        bottom_layout = QVBoxLayout(bottom)
        bottom_layout.setContentsMargins(12, 8, 12, 10)
        bottom_layout.setSpacing(8)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        dir_row.addWidget(self._section_label("输出目录"))
        self.ed_output = QLineEdit()
        self.ed_output.setText(self.ctx.output_dir or "跟随源文件所在目录")
        self.ed_output.setReadOnly(True)
        dir_row.addWidget(self.ed_output, 1)
        # 全局勾选项移到输出目录这一行（问题 19），视觉上明确是可勾选项
        self.chk_follow_source = QCheckBox("输出到源文件所在目录")
        self.chk_follow_source.setToolTip(
            "勾选后输出文件与源文件放在同一目录；取消则统一输出到上面指定的目录"
        )
        self.chk_follow_source.setChecked(bool(self.ctx.follow_source_dir))
        dir_row.addWidget(self.chk_follow_source)
        self.chk_auto_rename = QCheckBox("已存在时自动加序号")
        self.chk_auto_rename.setToolTip("输出文件已存在时自动追加序号，绝不覆盖原文件")
        self.chk_auto_rename.setChecked(
            str(self.ctx.cfg.get("overwrite_policy") or "rename") == "rename"
        )
        dir_row.addWidget(self.chk_auto_rename)
        self.btn_choose_dir = QPushButton("选择…")
        self.btn_open_dir = QPushButton("打开所在文件夹")
        dir_row.addWidget(self.btn_choose_dir)
        dir_row.addWidget(self.btn_open_dir)
        bottom_layout.addLayout(dir_row)

        run_row = QHBoxLayout()
        run_row.setSpacing(10)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat("%p%")
        run_row.addWidget(self.progress, 1)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setObjectName("Notice")
        self.lbl_status.setMinimumWidth(280)
        run_row.addWidget(self.lbl_status)
        self.btn_start = QPushButton("开始处理")
        self.btn_start.setObjectName("Primary")
        self.btn_cancel = QPushButton("取消")
        self.btn_cancel.setObjectName("Danger")
        self.btn_cancel.setEnabled(False)
        run_row.addWidget(self.btn_start)
        run_row.addWidget(self.btn_cancel)
        bottom_layout.addLayout(run_row)

        # ---- 结果横幅（问题 9）：不打开日志也能看到结果 ----
        result_row = QHBoxLayout()
        result_row.setSpacing(8)
        self.lbl_result = QLabel("")
        self.lbl_result.setObjectName("ResultOk")
        self.lbl_result.setWordWrap(True)
        self.lbl_result.setVisible(False)
        result_row.addWidget(self.lbl_result, 1)
        self.btn_view_errors = QPushButton("查看错误")
        self.btn_view_errors.setVisible(False)
        self.btn_open_output = QPushButton("打开输出目录")
        self.btn_open_output.setVisible(False)
        result_row.addWidget(self.btn_view_errors)
        result_row.addWidget(self.btn_open_output)
        bottom_layout.addLayout(result_row)

        self.log_panel = LogPanel(log_max_lines(self.ctx.cfg))
        bottom_layout.addWidget(self.log_panel)
        root.addWidget(bottom, 0)

        # ---- 菜单 ----
        self._build_menus()

        # ---- 状态栏：工具链信息（原底部勾选行的位置让给了勾选项，问题 19） ----
        self.lbl_toolchain = QLabel("")
        self.lbl_toolchain.setObjectName("Notice")
        self.statusBar().addPermanentWidget(self.lbl_toolchain)

        # ---- 信号 ----
        self.btn_start.clicked.connect(self.start_run)
        self.btn_cancel.clicked.connect(self.cancel_run)
        self.btn_choose_dir.clicked.connect(self._choose_output_dir)
        self.btn_open_dir.clicked.connect(self._open_output_dir)
        self.btn_open_output.clicked.connect(self._open_output_dir)
        self.btn_view_errors.clicked.connect(self._show_errors)
        self.btn_pick_ffmpeg.clicked.connect(self._pick_ffmpeg)
        self.btn_recheck.clicked.connect(lambda: self._refresh_toolchain())
        self.chk_auto_rename.toggled.connect(self._on_overwrite_policy_changed)
        self.chk_follow_source.toggled.connect(self._on_follow_source_changed)
        self.nav.currentRowChanged.connect(self._on_page_changed)

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("SectionTitle")
        return label

    def _nav_text(self, title: str) -> str:
        return f"  {title}"

    def _build_menus(self) -> None:
        menu_file = self.menuBar().addMenu("文件(&F)")
        act_settings = QAction("首选项…", self)
        act_settings.setShortcut(QKeySequence("Ctrl+,"))
        act_settings.triggered.connect(self.open_settings)
        act_open_cfg = QAction("打开配置目录", self)
        act_open_cfg.triggered.connect(self._open_config_dir)
        act_quit = QAction("退出", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        menu_file.addAction(act_settings)
        menu_file.addSeparator()
        menu_file.addAction(act_open_cfg)
        menu_file.addSeparator()
        menu_file.addAction(act_quit)

        menu_view = self.menuBar().addMenu("视图(&V)")
        self.act_theme = QAction("切换到浅色模式" if self.ctx.theme == "dark" else "切换到深色模式", self)
        self.act_theme.triggered.connect(self._toggle_theme)
        menu_view.addAction(self.act_theme)
        act_log = QAction("展开/收起运行日志", self)
        act_log.setShortcut(QKeySequence("Ctrl+L"))
        act_log.triggered.connect(lambda: self.log_panel.set_expanded(not self.log_panel.expanded))
        menu_view.addAction(act_log)

        menu_help = self.menuBar().addMenu("帮助(&H)")
        act_hint = QAction("ffmpeg 安装指引", self)
        act_hint.triggered.connect(lambda: self._info(self.ctx.install_hint(), "ffmpeg 安装指引"))
        act_about = QAction("关于", self)
        act_about.triggered.connect(self._about)
        menu_help.addAction(act_hint)
        menu_help.addAction(act_about)

    # ------------------------------------------------------------------
    @property
    def current_page(self) -> BasePage:
        index = max(0, self.stack.currentIndex())
        return self.pages[index]

    def log(self, text: str) -> None:
        self.log_panel.append(text)

    # ------------------------------------------------------------------
    # 状态管理（问题 3 / 5 / 9）
    # ------------------------------------------------------------------
    def _on_page_changed(self, _index: int) -> None:
        """切换功能页：底部全局状态跟随当前页，不留上一页的残留。"""
        if not self.queue.running:
            self._reset_run_display()
        self._update_run_availability()

    def _on_page_state_reset(self) -> None:
        """页面清空文件后：进度、结果、日志一起重置（问题 3）。"""
        if self.queue.running:
            return
        self._reset_run_display()
        self.log_panel.clear()
        self._update_run_availability()

    def _reset_run_display(self) -> None:
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setProperty("state", "")
        self._repolish(self.progress)
        self.lbl_status.setText("就绪")
        self.lbl_result.setVisible(False)
        self.btn_view_errors.setVisible(False)
        self.btn_open_output.setVisible(False)
        self._last_summary = None

    @staticmethod
    def _repolish(widget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _set_progress_state(self, state: str) -> None:
        self.progress.setProperty("state", state)
        self._repolish(self.progress)

    def _update_run_availability(self) -> None:
        """开始按钮的可用性 = 未在运行 + 工具链就绪 + 本页前置检查通过（问题 5）。"""
        if self.queue.running:
            self.btn_start.setEnabled(False)
            return
        if not self.ctx.toolchain.ok:
            self.btn_start.setEnabled(False)
            self.btn_start.setToolTip("未检测到 ffmpeg，请先安装或手动指定")
            return
        page = self.current_page
        ok, reason = page.can_run()
        has_files = bool(page.paths())
        self.btn_start.setEnabled(bool(ok and has_files))
        if not has_files:
            self.btn_start.setToolTip("先添加要处理的文件")
        elif reason:
            self.btn_start.setToolTip(reason)
        else:
            self.btn_start.setToolTip("开始处理当前页面中的任务")

    def _show_errors(self) -> None:
        """结果横幅上的「查看错误」：展开日志并滚到失败摘要处。"""
        self.log_panel.set_expanded(True)
        summary = self._last_summary
        if summary and summary.failures:
            lines = ["", "── 失败清单 ──"]
            for result in summary.failures:
                lines.append(f"✖ {result.label}：{result.message}")
            self.log("\n".join(lines))

    # ------------------------------------------------------------------
    # 工具链
    # ------------------------------------------------------------------
    def _refresh_toolchain(self, initial: bool = False) -> None:
        chain = self.ctx.refresh_toolchain()
        if chain.ok:
            self.lbl_toolchain.setText(chain.summary())
            self.banner.setVisible(False)
            if chain.outdated:
                self.log(f"⚠ ffmpeg 版本较低（{chain.version_str}），建议升级到 4.0 以上")
            if not chain.ffprobe:
                self.log("⚠ 未找到 ffprobe，无法读取媒体信息与计算进度")
        else:
            self.lbl_toolchain.setText("未检测到 ffmpeg / ffprobe")
            self.banner_label.setText(
                "未检测到 ffmpeg / ffprobe，暂时无法执行任何处理。请安装后点击「重新检测」，"
                "或直接「手动指定 ffmpeg…」。"
            )
            self.banner.setVisible(True)
        for page in self.pages:
            page.on_toolchain_changed()
        self._update_run_availability()
        if not initial:
            self.log(f"工具链：{chain.summary()}")

    def _pick_ffmpeg(self) -> None:
        start = self.ctx.toolchain.ffmpeg or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, "选择 ffmpeg 可执行文件", start, "ffmpeg (ffmpeg.exe ffmpeg);;所有文件 (*.*)"
        )
        if not path:
            return
        ok, message = self.ctx.verify_single_path(path)
        if not ok:
            self._warn(f"无法使用该文件：{message}", "指定失败")
            return
        self.ctx.set_manual_paths(path)
        self._refresh_toolchain()
        self.log(f"已指定 ffmpeg：{path}（{message}）")

    # ------------------------------------------------------------------
    # 输出目录
    # ------------------------------------------------------------------
    def _on_follow_source_changed(self, checked: bool) -> None:
        self.ctx.follow_source_dir = bool(checked)
        self.ctx.cfg.set("follow_source_dir", bool(checked))
        self.ctx.cfg.save()
        self._refresh_output_label()
        for page in self.pages:
            page.on_output_dir_changed()
        self.log("输出位置：" + ("跟随源文件所在目录" if checked else f"统一输出到 {self.ctx.output_dir}"))

    def _refresh_output_label(self) -> None:
        if self.ctx.follow_source_dir:
            self.ed_output.setText("跟随源文件所在目录")
            self.ed_output.setToolTip("每个文件的输出放在该文件自己的目录里")
        else:
            self.ed_output.setText(self.ctx.output_dir)
            self.ed_output.setToolTip(self.ctx.output_dir)

    def _choose_output_dir(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录", self.ctx.output_dir)
        if not directory:
            return
        if not is_writable_dir(directory):
            self._warn(f"该目录不可写：{directory}", "目录不可写")
            return
        self.ctx.output_dir = directory
        self.ctx.cfg.set("output_dir", directory)
        self.ctx.cfg.save()
        # 手动选了目录 → 自动切到"统一输出到该目录"
        if self.chk_follow_source.isChecked():
            self.chk_follow_source.setChecked(False)
        self._refresh_output_label()
        for page in self.pages:
            page.on_output_dir_changed()
        self.log(f"输出目录：{directory}")

    def _open_output_dir(self) -> None:
        page = self.current_page
        paths = page.paths()
        target = page.output_dir_for(paths[0]) if paths else self.ctx.output_dir
        self._open_path(target)

    def _open_config_dir(self) -> None:
        from ..config import config_dir

        self._open_path(str(config_dir()))

    def _open_path(self, path: str) -> None:
        if not path or not os.path.isdir(path):
            self._warn(f"目录不存在：{path}", "打开失败")
            return
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        if os.name == "nt":
            try:
                os.startfile(path)  # type: ignore[attr-defined]
                return
            except OSError:
                pass
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    # ------------------------------------------------------------------
    # 运行
    # ------------------------------------------------------------------
    def start_run(self) -> None:
        if self.queue.running:
            return
        page = self.current_page
        if not self.ctx.toolchain.ok:
            self._warn("未找到 ffmpeg，无法执行处理。请先安装或在顶栏手动指定 ffmpeg.exe。", "缺少 ffmpeg")
            return

        # 页面级前置检查（问题 5：合并页要先完成编码一致性检测）
        ok, reason = page.can_run()
        if not ok:
            self._warn(reason, "还不能开始")
            return

        try:
            specs = page.build_specs()
        except BuildError as exc:
            self._warn(str(exc), "参数不完整")
            return
        except Exception as exc:
            self._warn(f"无法解析当前参数：{exc}", "参数错误")
            return

        if not specs:
            self._warn("请先添加要处理的文件。", "没有文件")
            return

        # 1) 手动编辑过的命令（P3）：只支持单文件单步，输出路径校验按约定关闭
        if page.command_box.is_edited:
            if not self._apply_command_override(page, specs):
                return
        # 2) 输出冲突处理（会改写 spec.output，必须在构建 argv 之前做）
        elif not self._resolve_conflicts(specs):
            return

        jobs = self._build_jobs(page, specs)
        if not jobs:
            self._warn("没有可执行的任务（参数组合无效）。", "无任务")
            return

        if not self._preflight(jobs):
            return

        self._run_pages = [page] * len(jobs)
        self._reset_run_display()
        self.log_panel.clear()
        self.log(f"═══ 开始处理：{page.TITLE} · {len(jobs)} 个任务 ═══")
        self.lbl_result.setVisible(False)
        self._set_running(True)
        self._set_progress_state("running")
        self.queue.start(jobs)

    def _apply_command_override(self, page: BasePage, specs) -> bool:
        """把命令预览框里手改的内容作为 argv 使用，并做降级提示（需求 5.7）。"""
        try:
            probe_job = self.ctx.builder.build(specs[0])
        except BuildError:
            probe_job = None

        if probe_job is not None and len(probe_job.steps) > 1:
            self._warn(
                "当前参数会生成多条 ffmpeg 命令（例如智能切割、两遍编码），不支持手动编辑。\n"
                "请改用单步模式，或取消勾选「手动编辑命令后执行」。",
                "不支持手动编辑",
            )
            return False

        if len(specs) > 1:
            self._warn(
                f"手动编辑的命令只能用于单个文件，当前有 {len(specs)} 个文件。\n"
                "请只保留一个文件，或取消勾选「手动编辑命令后执行」。",
                "不支持批量",
            )
            return False

        if QMessageBox.question(
            self,
            "命令已被手动编辑",
            "当前命令被手动修改过，执行时将降级：\n"
            "· 不显示进度条\n· 不校验输出路径\n· 仅显示 stderr 尾部日志\n\n确定继续执行吗？",
            QMessageBox.Ok | QMessageBox.Cancel,
            QMessageBox.Cancel,
        ) != QMessageBox.Ok:
            return False

        tokens = _split_command(page.command_box.command_text())
        if not tokens:
            self._warn("编辑后的命令为空。", "命令为空")
            return False
        specs[0].argv_override = tokens
        self.log("已启用手动编辑的命令（降级模式）")
        return True

    def _build_jobs(self, page: BasePage, specs) -> list[Job]:
        jobs: list[Job] = []
        for spec in specs:
            try:
                jobs.append(self.ctx.builder.build(spec))
            except BuildError as exc:
                self.log(f"✖ 跳过 {spec.display_name()}：{exc}")
        return jobs

    def _resolve_conflicts(self, specs) -> bool:
        """输出冲突处理：自动追加序号 / 弹窗确认 / 直接覆盖（跟随首选项策略）。"""
        desired = [spec.output or "" for spec in specs]
        existed = [os.path.exists(path) for path in desired]

        # 首选项里的「直接覆盖同名文件」：不预改名、不弹窗，直接用原路径
        if any(existed) and not self.chk_auto_rename.isChecked() \
                and str(self.ctx.cfg.get("overwrite_policy") or "rename") == "overwrite":
            names = [os.path.basename(p) for p in desired if os.path.exists(p)]
            self.log(f"按首选项直接覆盖同名输出文件（{len(names)} 个）")
            return True

        resolved = unique_output_multi(desired)
        for spec, path in zip(specs, resolved):
            spec.output = path

        if not any(existed):
            return True

        names = [os.path.basename(p) for p, hit in zip(desired, existed) if hit]
        preview = "、".join(names[:3]) + ("…" if len(names) > 3 else "")
        if self.chk_auto_rename.isChecked():
            self.log(f"输出文件已存在（{preview}），已自动追加序号避免覆盖原文件")
            return True

        box = QMessageBox(self)
        box.setWindowTitle("输出文件已存在")
        box.setIcon(QMessageBox.Question)
        box.setText(f"有 {len(names)} 个目标文件已存在：\n{preview}\n\n要如何处理？")
        btn_overwrite = box.addButton("覆盖", QMessageBox.DestructiveRole)
        btn_rename = box.addButton("自动加序号", QMessageBox.AcceptRole)
        box.addButton("取消", QMessageBox.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is btn_overwrite:
            for spec, path in zip(specs, desired):
                spec.output = path
            self.log("已选择覆盖同名输出文件")
            return True
        if clicked is btn_rename:
            self.log(f"输出文件已存在（{preview}），已自动追加序号")
            return True
        self.log("已取消执行")
        return False

    def _preflight(self, jobs: list[Job]) -> bool:
        report = self.ctx.validator.preflight(jobs)
        globals_ = report.of(Severity.GLOBAL)
        files = report.of(Severity.FILE)
        warns = report.of(Severity.WARN)

        for issue in warns:
            self.log(f"· {issue.message}")

        if globals_:
            detail = "\n".join(f"· {i.message}" for i in globals_[:8])
            hint = next((i.hint for i in globals_ if i.hint), "")
            self._warn(f"以下问题会中断整个队列，已停止执行：\n\n{detail}\n\n{hint}", "无法执行")
            return False

        if files:
            detail = "\n".join(f"· {i.message}" for i in files[:10])
            more = f"\n… 共 {len(files)} 项" if len(files) > 10 else ""
            if QMessageBox.question(
                self,
                "部分文件会被跳过",
                f"以下文件存在问题，将被跳过，其余文件继续处理：\n\n{detail}{more}\n\n是否继续？",
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Ok,
            ) != QMessageBox.Ok:
                return False
            bad_labels = {i.path for i in files}
            for job in jobs:
                for path in job.spec.inputs:
                    if path in bad_labels:
                        job.skip_reason = next(
                            (i.message for i in files if i.path == path), "预检未通过"
                        )
                        break
        return True

    def cancel_run(self) -> None:
        if self.queue.running:
            self.queue.cancel()

    # ------------------------------------------------------------------
    # 队列信号
    # ------------------------------------------------------------------
    def _wire_queue(self) -> None:
        self.queue.logLine.connect(self.log)
        self.queue.jobStarted.connect(self._on_job_started)
        self.queue.progressChanged.connect(self._on_progress)
        self.queue.jobResult.connect(self._on_job_result)
        self.queue.queueFinished.connect(self._on_queue_finished)
        self.queue.queueAborted.connect(lambda reason: self.log(f"■■ 队列中断：{reason}"))

    def _on_job_started(self, index: int, total: int, label: str) -> None:
        self._current_job_index = index
        self._total_jobs = total
        self.lbl_status.setText(f"第 {index}/{total} 个：{label}")

    def _on_progress(self, index: int, percent: float, out_time: float, speed: str) -> None:
        total = max(1, getattr(self, "_total_jobs", 1))
        from ..core.timeparse import format_short

        if percent is None or percent < 0:
            self.progress.setRange(0, 0)
            detail = f"已处理 {format_short(out_time)}" + (f" · 速度 {speed}" if speed else "")
        else:
            self.progress.setRange(0, 100)
            overall = ((index - 1) + percent / 100.0) / total * 100.0
            self.progress.setValue(int(max(0.0, min(100.0, overall))))
            detail = f"{percent:.0f}%" + (f" · 速度 {speed}" if speed else "")
        self.lbl_status.setText(f"第 {index}/{total} 个 · {detail}")

    def _on_job_result(self, index: int, ok: bool, message: str, stderr_text: str) -> None:
        if 0 < index <= len(self._run_pages):
            page = self._run_pages[index - 1]
            handler = getattr(page, "on_job_result", None)
            if callable(handler):
                handler(ok, message)
        if not ok and stderr_text:
            self.log("─" * 20 + " stderr 尾部 " + "─" * 20)
            for line in stderr_text.splitlines()[-12:]:
                self.log("  " + line)

    def _on_queue_finished(self, summary: QueueSummary) -> None:
        self._set_running(False)
        self._last_summary = summary
        self.progress.setRange(0, 100)
        self.progress.setValue(100 if summary.ok_count else 0)

        if summary.aborted:
            if summary.user_cancelled:
                # 用户主动取消：中性提示即可，不该弹"全局错误"警告框
                self._set_progress_state("")
                self.lbl_status.setText(
                    f"已取消：成功 {summary.ok_count} 个，失败 {summary.fail_count} 个"
                )
                self._show_result(
                    f"⏹ 已取消：成功 {summary.ok_count} 个，失败 {summary.fail_count} 个",
                    ok=False,
                )
                self.btn_view_errors.setVisible(False)
                return
            self._set_progress_state("error")
            self.lbl_status.setText(f"已中断：{summary.abort_reason}")
            self._show_result(
                f"✖ 已中断：{summary.abort_reason}（成功 {summary.ok_count} 个，失败 {summary.fail_count} 个）",
                ok=False,
            )
            self._notify_finish(False, f"队列中断：{summary.abort_reason}")
            self._warn(
                f"队列已中断（全局错误）：\n\n{summary.abort_reason}\n\n"
                f"已完成 {summary.ok_count} 个，失败 {summary.fail_count} 个。",
                "队列中断",
            )
            return

        self.lbl_status.setText(
            f"完成：成功 {summary.ok_count} 个，失败 {summary.fail_count} 个"
        )
        self.log(f"═══ 全部结束：成功 {summary.ok_count}，失败 {summary.fail_count} ═══")

        if summary.failures:
            # 失败：进度条转红 + 横幅高亮 + 一键查看错误（问题 9）
            self._set_progress_state("error")
            first = summary.failures[0]
            self._show_result(
                f"✖ 成功 {summary.ok_count} 个，失败 {summary.fail_count} 个　"
                f"首个失败：{first.label} — {first.message}",
                ok=False,
            )
            self._notify_finish(False, f"{summary.fail_count} 个任务失败")
            lines = []
            for result in summary.failures[:12]:
                tail = (result.stderr_tail or "").strip().splitlines()
                detail = tail[-1][:160] if tail else ""
                lines.append(f"· {result.label}：{result.message}" + (f"\n    {detail}" if detail else ""))
            more = f"\n… 共 {len(summary.failures)} 个失败" if len(summary.failures) > 12 else ""
            QMessageBox.warning(
                self,
                "部分任务失败",
                f"成功 {summary.ok_count} 个，失败 {summary.fail_count} 个：\n\n" + "\n".join(lines) + more
                + "\n\n详细 stderr 已输出到「运行日志」。",
            )
        elif summary.ok_count:
            # 成功：进度条转绿 + 横幅（问题 9）
            self._set_progress_state("done")
            self.lbl_status.setText(f"全部完成：成功 {summary.ok_count} 个")
            self._show_result(f"✔ 全部完成：成功 {summary.ok_count} 个", ok=True)
            self._notify_finish(True, f"全部完成：{summary.ok_count} 个")

    def _show_result(self, text: str, ok: bool) -> None:
        self.lbl_result.setText(text)
        self.lbl_result.setObjectName("ResultOk" if ok else "ResultFail")
        self._repolish(self.lbl_result)
        self.lbl_result.setVisible(True)
        self.btn_view_errors.setVisible(not ok)
        self.btn_open_output.setVisible(True)

    def _notify_finish(self, ok: bool, message: str) -> None:
        """可选的系统托盘通知 + 提示音（问题 9）。失败时静默失败，不影响主流程。"""
        try:
            if QApplication.instance() is not None:
                QApplication.beep()
        except Exception:
            pass
        try:
            from PySide6.QtWidgets import QSystemTrayIcon

            if QSystemTrayIcon.isSystemTrayAvailable():
                if getattr(self, "_tray", None) is None:
                    from PySide6.QtGui import QIcon

                    from ..resources import app_icon

                    self._tray = QSystemTrayIcon(app_icon(), self)
                    self._tray.setToolTip("FFKit")
                    self._tray.show()
                self._tray.showMessage(
                    "FFKit",
                    message,
                    QSystemTrayIcon.Information if ok else QSystemTrayIcon.Warning,
                    4000,
                )
        except Exception:
            pass

    def _set_running(self, running: bool) -> None:
        self.btn_cancel.setEnabled(running)
        self.nav.setEnabled(True)  # 长任务期间仍可切换功能页（需求八）
        if not running:
            self.progress.setRange(0, 100)
        for page in self.pages:
            page.command_box.set_enabled_state(not running)
        self._update_run_availability()

    # ------------------------------------------------------------------
    # 拖拽（需求 5.8）
    # ------------------------------------------------------------------
    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        if not event.mimeData().hasUrls():
            super().dropEvent(event)
            return
        page = self._page_under(event.position().toPoint()) or self.current_page
        paths = [u.toLocalFile() for u in event.mimeData().urls()]
        files = collect_media_files(paths, page.ALLOWED_EXTS)
        if not files:
            self.log("拖入的内容里没有可处理的媒体文件")
        else:
            added = page.add_files(files)
            self.log(
                f"拖入 {len(files)} 个文件到「{page.TITLE}」"
                + (f"，新增 {added} 个" if added != len(files) else "")
            )
            if page is not self.current_page:
                self.nav.setCurrentRow(self.pages.index(page))
        event.acceptProposedAction()

    def _page_under(self, pos) -> BasePage | None:
        widget = self.childAt(pos)
        while widget is not None:
            if isinstance(widget, BasePage):
                return widget
            widget = widget.parentWidget()
        return None

    # ------------------------------------------------------------------
    # 主题 / 设置
    # ------------------------------------------------------------------
    def _toggle_theme(self) -> None:
        mode = "light" if self.ctx.theme == "dark" else "dark"
        self.apply_theme(mode)
        self.ctx.cfg.set("theme", mode)
        self.ctx.cfg.set("theme_resolved", mode)
        self.ctx.cfg.save()
        self.act_theme.setText("切换到深色模式" if mode == "light" else "切换到浅色模式")
        self.log(f"已切换到{'浅色' if mode == 'light' else '深色'}模式")

    def apply_theme(self, mode: str) -> None:
        self.ctx.theme = mode
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(stylesheet(mode))

    def _on_overwrite_policy_changed(self, checked: bool) -> None:
        policy = "rename" if checked else "ask"
        self.ctx.cfg.set("overwrite_policy", policy)
        self.ctx.cfg.save()
        self.ctx.validator.overwrite_policy = policy

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.ctx, self)
        if dialog.exec():
            self._refresh_toolchain()
            self.log_panel.set_max_lines(log_max_lines(self.ctx.cfg))
            self.chk_auto_rename.setChecked(
                str(self.ctx.cfg.get("overwrite_policy") or "rename") == "rename"
            )
            self.apply_theme(self.ctx.theme)
            self.act_theme.setText(
                "切换到浅色模式" if self.ctx.theme == "dark" else "切换到深色模式"
            )

    # ------------------------------------------------------------------
    def _info(self, text: str, title: str) -> None:
        QMessageBox.information(self, title, text)

    def _warn(self, text: str, title: str = "提示") -> None:
        QMessageBox.warning(self, title, text)

    def _about(self) -> None:
        from .. import __version__
        from ..resources import logo_pixmap

        box = QMessageBox(self)
        box.setWindowTitle("关于")
        box.setText(
            f"<b>FFKit v{__version__}</b><br><br>"
            "ffmpeg 的图形化外壳：本地、离线、无广告、轻量。<br>"
            "不做时间线多轨编辑，只覆盖常见的音视频处理需求。<br><br>"
            f"当前工具链：{self.ctx.toolchain.summary()}"
        )
        pixmap = logo_pixmap(96)
        if not pixmap.isNull():
            box.setIconPixmap(pixmap)
        box.exec()

    # ------------------------------------------------------------------
    def _restore_geometry(self) -> None:
        geometry = self.ctx.cfg.get("window_geometry") or ""
        if geometry:
            try:
                from PySide6.QtCore import QByteArray

                self.restoreGeometry(QByteArray.fromHex(bytes(geometry, "ascii")))
            except Exception:
                pass

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.queue.running:
            if QMessageBox.question(
                self,
                "任务进行中",
                "还有任务正在执行，确定要退出吗？（当前任务会被取消）",
                QMessageBox.Ok | QMessageBox.Cancel,
                QMessageBox.Cancel,
            ) != QMessageBox.Ok:
                event.ignore()
                return
            self.queue.cancel()
        try:
            self.ctx.cfg.set("window_geometry", bytes(self.saveGeometry().toHex()).decode("ascii"))
            self.ctx.cfg.set("output_dir", self.ctx.output_dir)
            self.ctx.cfg.set("follow_source_dir", bool(self.ctx.follow_source_dir))
            self.ctx.cfg.save()
        except Exception:
            pass
        super().closeEvent(event)


def _split_command(text: str) -> list[str]:
    """把用户手动编辑的命令拆回 argv（跳过注释行与空行）。"""
    lines = [line.strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line and not line.startswith("#")]
    if not lines:
        return []
    command = " ".join(lines)
    if os.name == "nt":
        tokens = _split_windows(command)
        if tokens:
            return tokens
    import shlex

    try:
        return shlex.split(command, posix=(os.name != "nt"))
    except ValueError:
        return command.split()


def _split_windows(command: str) -> list[str]:
    """按 Windows 命令行规则拆分（与 ffmpeg/QProcess 的解析保持一致）。"""
    try:
        import ctypes

        shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        func = shell32.CommandLineToArgvW
        func.restype = ctypes.POINTER(ctypes.c_wchar_p)
        func.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
        argc = ctypes.c_int(0)
        argv = func(command, ctypes.byref(argc))
        if not argv:
            return []
        try:
            return [argv[i] for i in range(argc.value)]
        finally:
            kernel32.LocalFree(argv)
    except Exception:
        return []


__all__ = ["MainWindow", "PAGE_CLASSES"]
