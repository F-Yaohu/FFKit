"""主题：Fusion 风格 + 自定义 QSS 调色板（需求七 / 十.4）。

- 首次启动跟随系统一次，之后尊重用户选择，不做动态跟随（Qt 在 Windows 上不稳定）。
- 浅色 / 深色两套调色板，切换后所有控件（包括表格、滚动条、提示框）都可读。
"""

from __future__ import annotations

from string import Template

LIGHT = {
    "bg": "#f4f5f7",
    "panel": "#ffffff",
    "panel_alt": "#fafbfc",
    "nav_bg": "#eaedf2",
    "border": "#d5dae1",
    "border_strong": "#b9c0ca",
    "text": "#1e2430",
    "text_dim": "#68707d",
    "accent": "#2563eb",
    "accent_hover": "#1d4ed8",
    "accent_text": "#ffffff",
    "hover": "#e9eef6",
    "input_bg": "#ffffff",
    "input_border": "#c8cfd8",
    "disabled": "#a8aeb8",
    "danger": "#d92d20",
    "ok": "#15803d",
    "ok_bg": "#e7f6ec",
    "ok_border": "#9fd6b4",
    "ok_text": "#12633a",
    "danger_bg": "#fdeceb",
    "danger_border": "#f2b3ae",
    "danger_text": "#a3211a",
    "warn_bg": "#fff6e5",
    "warn_border": "#f3d19b",
    "warn_text": "#8a5300",
    "info_bg": "#eaf2ff",
    "info_border": "#b9d2ff",
    "info_text": "#1e40af",
    "sel_bg": "#dbe7ff",
    "log_bg": "#1f242c",
    "log_text": "#d7dde6",
    "progress_bg": "#e3e7ed",
}

DARK = {
    "bg": "#191d23",
    "panel": "#22272f",
    "panel_alt": "#1e232a",
    "nav_bg": "#1d2229",
    "border": "#333a44",
    "border_strong": "#454d59",
    "text": "#e5e9ef",
    "text_dim": "#98a2b0",
    "accent": "#3b82f6",
    "accent_hover": "#60a5fa",
    "accent_text": "#0b1220",
    "hover": "#2b323c",
    "input_bg": "#1c2129",
    "input_border": "#3b434f",
    "disabled": "#6b7480",
    "danger": "#f5705f",
    "ok": "#4ade80",
    "ok_bg": "#16301f",
    "ok_border": "#2f6b46",
    "ok_text": "#7ee2a8",
    "danger_bg": "#341c1a",
    "danger_border": "#7a3730",
    "danger_text": "#ff9d8f",
    "warn_bg": "#3a2f14",
    "warn_border": "#6b5520",
    "warn_text": "#f5c451",
    "info_bg": "#17273f",
    "info_border": "#2c4a75",
    "info_text": "#93c0ff",
    "sel_bg": "#2f4a76",
    "log_bg": "#14181e",
    "log_text": "#c9d1db",
    "progress_bg": "#2b323c",
}

_QSS = Template(
    """
QWidget {
    background-color: $bg;
    color: $text;
    font-size: 13px;
}
QMainWindow, QDialog { background-color: $bg; }

/* 关键：所有文字标签一律透明背景。
   否则 QSS 里 QWidget 的 background-color 会让 QLabel 在白色面板上
   显示成一个个"灰色方块"（问题 15）。 */
QLabel {
    background: transparent;
    color: $text;
}
QLabel#Notice, QLabel#NoticeWarn, QLabel#NoticeDanger, QLabel#NoticeOk,
QLabel#SectionTitle, QLabel#Hint {
    background: transparent;
}

QToolTip {
    background-color: $panel;
    color: $text;
    border: 1px solid $border;
    padding: 4px 6px;
}

/* ---------- 左侧导航 ---------- */
QListWidget#NavList {
    background-color: $nav_bg;
    border: none;
    border-right: 1px solid $border;
    padding: 8px 6px;
    outline: none;
}
QListWidget#NavList::item {
    padding: 11px 14px;
    margin: 2px 0;
    border-radius: 7px;
    color: $text;
}
QListWidget#NavList::item:hover { background-color: $hover; }
QListWidget#NavList::item:selected {
    background-color: $accent;
    color: $accent_text;
    font-weight: 600;
}

/* ---------- 分组框 ---------- */
QGroupBox {
    background-color: $panel;
    border: 1px solid $border;
    border-radius: 8px;
    margin-top: 12px;
    padding: 12px 12px 10px 12px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 10px;
    padding: 0 6px;
    color: $text_dim;
    font-weight: 600;
}

/* ---------- 文件列表 ---------- */
QListWidget#FileList {
    background-color: $input_bg;
    border: 1px solid $input_border;
    border-radius: 6px;
    padding: 4px;
    outline: none;
}
QListWidget#FileList::item {
    padding: 5px 6px;
    border-radius: 4px;
    color: $text;
}
QListWidget#FileList::item:selected {
    background-color: $sel_bg;
    color: $text;
}
QListWidget#FileList[dragActive="true"] {
    border: 1px dashed $accent;
    background-color: $hover;
}

/* ---------- 输入控件 ---------- */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QTextBrowser {
    background-color: $input_bg;
    border: 1px solid $input_border;
    border-radius: 6px;
    padding: 5px 8px;
    color: $text;
    selection-background-color: $accent;
    selection-color: $accent_text;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus,
QPlainTextEdit:focus, QTextEdit:focus {
    border: 1px solid $accent;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
    color: $disabled;
    background-color: $panel_alt;
}
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 16px;
    background-color: $panel_alt;
    border: none;
}
QComboBox::drop-down {
    border: none;
    border-left: 1px solid $input_border;
    width: 22px;
}
QComboBox::down-arrow {
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 5px solid $text_dim;
    width: 0;
    height: 0;
    margin-right: 6px;
}
/* 下拉框与普通输入框区分开（问题 10）：加粗边框 + 更明显的箭头 */
QComboBox {
    border: 1px solid $border_strong;
    background-color: $panel_alt;
    padding-right: 4px;
}
QComboBox:hover { border-color: $accent; }
QComboBox:on { border-color: $accent; }
QComboBox QAbstractItemView {
    background-color: $panel;
    border: 1px solid $border;
    selection-background-color: $sel_bg;
    selection-color: $text;
    outline: none;
}

QCheckBox, QRadioButton { spacing: 6px; color: $text; }
QRadioButton:disabled, QCheckBox:disabled { color: $disabled; }

/* ---------- 按钮 ---------- */
QPushButton {
    background-color: $panel_alt;
    border: 1px solid $input_border;
    border-radius: 6px;
    padding: 6px 14px;
    color: $text;
    min-height: 18px;
}
QPushButton:hover { background-color: $hover; border-color: $border_strong; }
QPushButton:pressed { background-color: $sel_bg; }
QPushButton:disabled { color: $disabled; background-color: $panel_alt; border-color: $border; }

QPushButton#Primary {
    background-color: $accent;
    color: $accent_text;
    border: 1px solid $accent;
    font-weight: 600;
    padding: 7px 22px;
}
QPushButton#Primary:hover { background-color: $accent_hover; border-color: $accent_hover; }
QPushButton#Primary:disabled { background-color: $border; color: $disabled; border-color: $border; }

QPushButton#Danger {
    color: $danger;
    border: 1px solid $border_strong;
}
QPushButton#Danger:hover { background-color: $hover; }
QPushButton#Danger:disabled { color: $disabled; background-color: $panel_alt; border-color: $border; }

QPushButton#Flat {
    background-color: transparent;
    border: none;
    padding: 4px 8px;
    color: $text_dim;
}
QPushButton#Flat:hover { color: $accent; background-color: $hover; border-radius: 5px; }

/* ---------- 进度条 ---------- */
QProgressBar {
    background-color: $progress_bg;
    border: 1px solid $border;
    border-radius: 6px;
    height: 20px;
    text-align: center;
    color: $text;
}
QProgressBar::chunk {
    background-color: $accent;
    border-radius: 5px;
    margin: 1px;
}
/* 完成 / 失败态（问题 9）：不打开日志也能看出结果 */
QProgressBar[state="done"] { border: 1px solid $ok; }
QProgressBar[state="done"]::chunk { background-color: $ok; }
QProgressBar[state="error"] { border: 1px solid $danger; }
QProgressBar[state="error"]::chunk { background-color: $danger; }
QProgressBar[state="running"]::chunk { background-color: $accent; }

/* 结果横幅 */
QLabel#ResultOk {
    background-color: $ok_bg;
    border: 1px solid $ok_border;
    color: $ok_text;
    border-radius: 6px;
    padding: 7px 12px;
    font-weight: 600;
}
QLabel#ResultFail {
    background-color: $danger_bg;
    border: 1px solid $danger_border;
    color: $danger_text;
    border-radius: 6px;
    padding: 7px 12px;
    font-weight: 600;
}

/* 参数区分组标题（问题 22） */
QLabel#GroupTitle {
    color: $accent;
    font-weight: 600;
    padding: 2px 0;
    background: transparent;
}
QFrame#GroupLine {
    background-color: $border;
    max-height: 1px;
    border: none;
}

/* ---------- 滑块 ---------- */
QSlider::groove:horizontal {
    height: 5px;
    background: $progress_bg;
    border-radius: 3px;
}
QSlider::sub-page:horizontal { background: $accent; border-radius: 3px; }
QSlider::handle:horizontal {
    background: $accent;
    width: 13px;
    margin: -5px 0;
    border-radius: 7px;
}
QSlider::handle:horizontal:hover { background: $accent_hover; }

/* ---------- 表格 ---------- */
QTableWidget, QTableView {
    background-color: $input_bg;
    alternate-background-color: $panel_alt;
    border: 1px solid $input_border;
    border-radius: 6px;
    gridline-color: $border;
    color: $text;
    selection-background-color: $sel_bg;
    selection-color: $text;
    outline: none;
}
QHeaderView::section {
    background-color: $panel_alt;
    color: $text_dim;
    border: none;
    border-bottom: 1px solid $border;
    padding: 5px 8px;
    font-weight: 600;
}
QTableCornerButton::section { background-color: $panel_alt; border: none; }

/* ---------- 滚动条 ---------- */
QScrollBar:vertical {
    background: transparent;
    width: 11px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: $border_strong;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover { background: $accent; }
QScrollBar:horizontal {
    background: transparent;
    height: 11px;
    margin: 2px;
}
QScrollBar::handle:horizontal {
    background: $border_strong;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::add-line, QScrollBar::sub-line { height: 0; width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: transparent; }

/* ---------- 分割条 ---------- */
QSplitter::handle { background-color: transparent; }
QSplitter::handle:horizontal { width: 8px; }
QSplitter::handle:vertical { height: 8px; }

/* ---------- 日志 ---------- */
QPlainTextEdit#LogView {
    background-color: $log_bg;
    color: $log_text;
    border: 1px solid $border;
    font-family: Consolas, "Cascadia Mono", "Microsoft YaHei Mono", monospace;
    font-size: 12px;
}
QPlainTextEdit#CommandView {
    font-family: Consolas, "Cascadia Mono", monospace;
    font-size: 12px;
}

/* ---------- 提示条 ---------- */
QLabel#Banner {
    background-color: $warn_bg;
    border: 1px solid $warn_border;
    color: $warn_text;
    border-radius: 6px;
    padding: 8px 12px;
}
QLabel#Notice { color: $text_dim; }
QLabel#NoticeWarn { color: $warn_text; }
QLabel#NoticeDanger { color: $danger; }
QLabel#NoticeOk { color: $ok; }
QLabel#SectionTitle { color: $text_dim; font-weight: 600; }
QLabel#Hint { color: $text_dim; }
QLabel#Thumb {
    background-color: $input_bg;
    border: 1px dashed $input_border;
    border-radius: 6px;
    color: $text_dim;
}

QStatusBar { background-color: $panel; color: $text_dim; border-top: 1px solid $border; }
QToolBar { background-color: $panel; border-bottom: 1px solid $border; padding: 4px; spacing: 4px; }
QMenuBar { background-color: $panel; }
QMenuBar::item:selected { background-color: $hover; }
QMenu {
    background-color: $panel;
    border: 1px solid $border;
    padding: 4px;
    color: $text;
}
QMenu::item { padding: 6px 22px; border-radius: 4px; }
QMenu::item:selected { background-color: $sel_bg; }
QMenu::separator { height: 1px; background: $border; margin: 4px 8px; }
"""
)


def palette(mode: str) -> dict[str, str]:
    return DARK if str(mode).lower() == "dark" else LIGHT


def stylesheet(mode: str) -> str:
    return _QSS.substitute(palette(mode))


def icon_color(mode: str, kind: str = "text") -> str:
    return palette(mode).get(kind, palette(mode)["text"])


__all__ = ["DARK", "LIGHT", "icon_color", "palette", "stylesheet"]
