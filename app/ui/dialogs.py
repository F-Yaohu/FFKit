"""设置对话框：ffmpeg 路径、主题、日志行数、输出冲突策略（需求十.4 / 十一）。"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from .context import AppContext


class SettingsDialog(QDialog):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle("首选项")
        self.setMinimumWidth(620)

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 12)
        root.setSpacing(12)

        # ---- ffmpeg ----
        form = QFormLayout()
        form.setSpacing(8)
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)

        ffmpeg_row = QHBoxLayout()
        self.ed_ffmpeg = QLineEdit(str(ctx.cfg.get("ffmpeg_path") or ctx.toolchain.ffmpeg or ""))
        self.ed_ffmpeg.setPlaceholderText("留空则自动在 PATH 与常见目录中查找")
        btn_browse = QPushButton("浏览…")
        btn_verify = QPushButton("验证")
        ffmpeg_row.addWidget(self.ed_ffmpeg, 1)
        ffmpeg_row.addWidget(btn_browse)
        ffmpeg_row.addWidget(btn_verify)
        form.addRow("ffmpeg 路径", self._wrap(ffmpeg_row))

        ffprobe_row = QHBoxLayout()
        self.ed_ffprobe = QLineEdit(str(ctx.cfg.get("ffprobe_path") or ctx.toolchain.ffprobe or ""))
        self.ed_ffprobe.setPlaceholderText("一般与 ffmpeg 同目录，留空自动推断")
        btn_browse2 = QPushButton("浏览…")
        ffprobe_row.addWidget(self.ed_ffprobe, 1)
        ffprobe_row.addWidget(btn_browse2)
        form.addRow("ffprobe 路径", self._wrap(ffprobe_row))

        self.lbl_check = QLabel("")
        self.lbl_check.setObjectName("Notice")
        self.lbl_check.setWordWrap(True)
        form.addRow("", self.lbl_check)

        # ---- 外观 ----
        theme_row = QHBoxLayout()
        self.rb_light = QRadioButton("浅色")
        self.rb_dark = QRadioButton("深色")
        (self.rb_light if ctx.theme == "light" else self.rb_dark).setChecked(True)
        theme_row.addWidget(self.rb_light)
        theme_row.addWidget(self.rb_dark)
        theme_row.addStretch(1)
        form.addRow("界面主题", self._wrap(theme_row))

        self.sp_log = QSpinBox()
        self.sp_log.setRange(200, 20000)
        self.sp_log.setSingleStep(200)
        self.sp_log.setValue(int(ctx.cfg.get("log_max_lines") or 2000))
        self.sp_log.setSuffix(" 行")
        self.sp_log.setToolTip("日志区最多保留的行数，超出后自动丢弃最旧的记录")
        form.addRow("日志上限", self.sp_log)

        self.cb_overwrite = QComboBox()
        self.cb_overwrite.addItem("自动追加序号（不覆盖原文件）", "rename")
        self.cb_overwrite.addItem("每次都询问我", "ask")
        self.cb_overwrite.addItem("直接覆盖同名文件", "overwrite")
        policy = str(ctx.cfg.get("overwrite_policy") or "rename")
        index = max(0, self.cb_overwrite.findData(policy))
        self.cb_overwrite.setCurrentIndex(index)
        form.addRow("输出冲突", self.cb_overwrite)

        root.addLayout(form)

        hint = QLabel(
            "提示：本工具完全本地运行，不发起任何网络请求。目录与设置保存在 "
            "配置目录下的 config.json。"
        )
        hint.setObjectName("Notice")
        hint.setWordWrap(True)
        root.addWidget(hint)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText("保存")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

        btn_browse.clicked.connect(lambda: self._browse(self.ed_ffmpeg, "ffmpeg"))
        btn_browse2.clicked.connect(lambda: self._browse(self.ed_ffprobe, "ffprobe"))
        btn_verify.clicked.connect(self._verify)

    @staticmethod
    def _wrap(layout) -> QWidget:
        widget = QWidget()
        wrapper = QVBoxLayout(widget)
        wrapper.setContentsMargins(0, 0, 0, 0)
        wrapper.addLayout(layout)
        return widget

    # ------------------------------------------------------------------
    def _browse(self, edit: QLineEdit, name: str) -> None:
        pattern = f"{name} ({name}.exe {name});;所有文件 (*.*)" if os.name == "nt" else "所有文件 (*.*)"
        path, _ = QFileDialog.getOpenFileName(self, f"选择 {name}", edit.text() or "", pattern)
        if path:
            edit.setText(path)
            if name == "ffmpeg":
                sibling = os.path.join(os.path.dirname(path), "ffprobe.exe" if os.name == "nt" else "ffprobe")
                if os.path.isfile(sibling):
                    self.ed_ffprobe.setText(sibling)
            self._verify()

    def _verify(self) -> None:
        path = self.ed_ffmpeg.text().strip()
        ok, message = self.ctx.verify_single_path(path)
        self.lbl_check.setText(("✔ " if ok else "✖ ") + message)
        self.lbl_check.setObjectName("NoticeOk" if ok else "NoticeDanger")
        self.lbl_check.style().unpolish(self.lbl_check)
        self.lbl_check.style().polish(self.lbl_check)

    def _on_accept(self) -> None:
        self.ctx.cfg.set("ffmpeg_path", self.ed_ffmpeg.text().strip())
        self.ctx.cfg.set("ffprobe_path", self.ed_ffprobe.text().strip())
        self.ctx.cfg.set("theme", "light" if self.rb_light.isChecked() else "dark")
        self.ctx.cfg.set("theme_resolved", "light" if self.rb_light.isChecked() else "dark")
        self.ctx.cfg.set("log_max_lines", self.sp_log.value())
        self.ctx.cfg.set("overwrite_policy", self.cb_overwrite.currentData())
        self.ctx.cfg.save()

        self.ctx.theme = "light" if self.rb_light.isChecked() else "dark"
        self.ctx.validator.overwrite_policy = str(self.cb_overwrite.currentData())
        self.accept()


__all__ = ["SettingsDialog"]
