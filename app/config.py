"""配置持久化层（JSON）。

存放位置：%APPDATA%/FFKit/config.json（Windows），其它平台退化为 ~/.ffkit/config.json。
不使用注册表、不联网。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def config_dir() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return Path(base) / "FFKit"
    return Path(os.path.expanduser("~")) / ".ffkit"


def config_path() -> Path:
    return config_dir() / "config.json"


DEFAULTS: dict[str, Any] = {
    # 工具链
    "ffmpeg_path": "",
    "ffprobe_path": "",
    # 目录
    "output_dir": "",
    "last_input_dir": "",
    # 输出位置：默认跟随源文件所在目录（问题 14）
    "follow_source_dir": True,
    # 外观
    "theme": "auto",            # auto | light | dark  （仅首次解析后落盘为具体值）
    "theme_resolved": "dark",   # light | dark
    # 行为
    "keep_original": True,
    "overwrite_policy": "rename",   # rename | ask | overwrite
    "log_max_lines": 2000,
    "hw_encoder": "none",           # none | nvenc | qsv | amf  （P2 预留接口）
    "last_preset_middle": True,
    # 窗口
    "window_geometry": "",
    "window_state": "",
}


class Config:
    """轻量 JSON 配置对象。所有读写都经过 get/set，便于集中维护默认值。"""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config_path()
        self._data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    # ---------- io ----------
    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    for k, v in raw.items():
                        self._data[k] = v
        except Exception:
            # 配置损坏时静默回退默认值，绝不影响启动
            self._data = dict(DEFAULTS)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, self.path)
        except Exception:
            pass

    # ---------- access ----------
    def get(self, key: str, default: Any = None) -> Any:
        if key in self._data:
            return self._data[key]
        if default is not None:
            return default
        return DEFAULTS.get(key)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def update(self, **kwargs: Any) -> None:
        self._data.update(kwargs)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._data)


def resolve_initial_theme(cfg: Config) -> str:
    """首次启动跟随系统一次，之后尊重用户选择（不做动态跟随）。"""
    stored = str(cfg.get("theme") or "auto")
    if stored in ("light", "dark"):
        return stored
    # 首次：探测系统深浅色一次
    resolved = "dark"
    try:
        if os.name == "nt":
            import winreg  # type: ignore

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            val, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            resolved = "light" if int(val) == 1 else "dark"
        else:  # pragma: no cover - 非 Windows 仅作兜底
            resolved = "dark"
    except Exception:
        resolved = "dark"
    cfg.set("theme", resolved)
    cfg.set("theme_resolved", resolved)
    cfg.save()
    return resolved


def log_max_lines(cfg: Config) -> int:
    try:
        return max(200, int(cfg.get("log_max_lines") or 2000))
    except Exception:
        return 2000


__all__ = [
    "Config",
    "DEFAULTS",
    "config_dir",
    "config_path",
    "resolve_initial_theme",
    "log_max_lines",
]
