"""核心层：四段管线（Spec → Validator → CommandBuilder → Runner）+ 校验/队列。

Runner 与 TaskQueue 依赖 Qt，所以这里做惰性导入 —— 这样核心层可以在没有
PySide6 的环境（CI、自检脚本）中独立测试。
"""

from __future__ import annotations

from typing import Any

from .builder import BuildError, CommandBuilder
from .paths import quote_argv, unique_output, unique_output_multi
from .prober import MediaInfo, ProbeError, Prober
from .spec import Job, OperationSpec, Step
from .timeparse import TimeParseError, format_hms, format_timestamp, parse_time, parse_time_safe
from .validator import Issue, Severity, ValidationReport, Validator

__all__ = [
    "BuildError",
    "CommandBuilder",
    "Issue",
    "Job",
    "MediaInfo",
    "OperationSpec",
    "ProbeError",
    "Prober",
    "Runner",
    "Severity",
    "Step",
    "TaskQueue",
    "TimeParseError",
    "ValidationReport",
    "Validator",
    "format_hms",
    "format_timestamp",
    "parse_time",
    "parse_time_safe",
    "quote_argv",
    "unique_output",
    "unique_output_multi",
]

_LAZY = {"Runner": ("runner", "Runner"), "TaskQueue": ("queue", "TaskQueue")}


def __getattr__(name: str) -> Any:  # PEP 562：惰性加载依赖 Qt 的模块
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    module = import_module(f".{target[0]}", __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value
