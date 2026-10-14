"""OperationSpec / Step / Job —— 四段管线的数据契约。

设计要点：
- OperationSpec 只描述"要做什么"，不含任何 ffmpeg 细节。
- CommandBuilder 把 Spec 编译成 Job，Job 由一个或多个 Step 组成
  （多步用于：智能切割的三段拼接、目标体积的两遍编码）。
- Runner 只认 Job/Step，因此新增功能不需要动 Runner 与 UI。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from .prober import MediaInfo


# 操作类型常量
OP_TRIM = "trim"
OP_MERGE = "merge"
OP_CONVERT = "convert"
OP_EXTRACT_AUDIO = "extract_audio"
OP_STRIP_AUDIO = "strip_audio"
OP_EXTRACT_FRAME = "extract_frame"
OP_COMPRESS = "compress"
OP_SET_COVER = "set_cover"
OP_EDIT_METADATA = "edit_metadata"
OP_MUX = "mux"

OP_LABELS = {
    OP_TRIM: "剪辑",
    OP_MERGE: "合并",
    OP_CONVERT: "格式转换",
    OP_EXTRACT_AUDIO: "提取音频",
    OP_STRIP_AUDIO: "去音频",
    OP_EXTRACT_FRAME: "提取帧",
    OP_COMPRESS: "压缩",
    OP_SET_COVER: "封面",
    OP_EDIT_METADATA: "文件属性",
    OP_MUX: "多轨封装",
}


@dataclass
class OperationSpec:
    """一次待执行的操作（单文件）。"""

    op: str
    inputs: list[str] = field(default_factory=list)
    output: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    label: str = ""
    duration: float | None = None
    info: MediaInfo | None = None
    # 手动编辑后的 argv（P3）。非空时 Runner 直接原样执行。
    argv_override: list[str] | None = None

    def __post_init__(self) -> None:
        if not self.label and self.inputs:
            self.label = os.path.basename(self.inputs[0])
        if self.duration is None and self.info is not None and self.info.duration:
            self.duration = self.info.duration

    @property
    def op_label(self) -> str:
        return OP_LABELS.get(self.op, self.op)

    def display_name(self) -> str:
        if self.op == OP_MERGE:
            return f"合并 {len(self.inputs)} 个文件"
        return self.label or (self.inputs[0] if self.inputs else self.op_label)


@dataclass
class Step:
    """Job 中的一步 ffmpeg 调用。"""

    argv: list[str]
    label: str = ""
    # 进度分母（秒）。None 表示无法算百分比 → 界面显示"已处理时长 + 速度"
    duration: float | None = None
    # 本步 ffmpeg 实际写入的文件（临时文件）；成功后如需改为正式名，填 final_out
    temp_out: str | None = None
    final_out: str | None = None
    # 本步成功后需要清理的中间文件（如智能切割的 .ts 分段、concat 列表文件）
    cleanup: list[str] = field(default_factory=list)
    # 是否要求带 -progress（手动编辑的命令没有，进度需降级）
    progress: bool = True

    def display_argv(self) -> list[str]:
        """对外展示用的 argv：把临时输出路径换回最终路径（问题 11）。

        临时文件（xxx.mp4.tmp）纯粹是内部机制 —— 写在同目录、成功后 os.replace
        原子改名。用户没必要看到它，命令预览里应当显示真正的输出文件名。
        """
        if not self.temp_out or not self.final_out or self.temp_out == self.final_out:
            return list(self.argv)
        return [self.final_out if token == self.temp_out else token for token in self.argv]

    def preview(self) -> str:
        from .paths import quote_argv

        return quote_argv(self.display_argv())


@dataclass
class Job:
    """一次完整任务（可能含多个 Step）。"""

    spec: OperationSpec
    steps: list[Step] = field(default_factory=list)
    final_output: str | None = None
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # 用于压缩前后的体积对比
    source_sizes: list[int] = field(default_factory=list)
    # 两遍编码产生的 ffmpeg2pass-* 日志前缀，任务结束（无论成败）统一清理
    passlog_prefixes: list[str] = field(default_factory=list)
    # 预检阶段判定为"跳过"的原因（空字符串表示正常执行）
    skip_reason: str = ""

    @property
    def label(self) -> str:
        return self.spec.display_name()

    @property
    def editing_degraded(self) -> bool:
        """命令被手动编辑过 → 降级模式（需求 5.7）。"""
        return bool(self.spec.argv_override)

    def duration_hint(self) -> float | None:
        total = 0.0
        found = False
        for step in self.steps:
            if step.duration:
                total += step.duration
                found = True
        return total if found else self.spec.duration

    def temp_paths(self) -> list[str]:
        paths: list[str] = []
        for step in self.steps:
            if step.temp_out:
                paths.append(step.temp_out)
            paths.extend(step.cleanup)
        return paths

    def preview(self) -> str:
        if self.spec.argv_override:
            from .paths import quote_argv

            return quote_argv(self.spec.argv_override)
        if len(self.steps) == 1:
            return self.steps[0].preview()
        blocks = []
        for idx, step in enumerate(self.steps, 1):
            title = step.label or f"第 {idx} 步"
            blocks.append(f"# {idx}/{len(self.steps)}  {title}\n{step.preview()}")
        return "\n\n".join(blocks)


__all__ = [
    "Job",
    "OP_COMPRESS",
    "OP_CONVERT",
    "OP_EDIT_METADATA",
    "OP_EXTRACT_AUDIO",
    "OP_EXTRACT_FRAME",
    "OP_LABELS",
    "OP_MERGE",
    "OP_MUX",
    "OP_SET_COVER",
    "OP_STRIP_AUDIO",
    "OP_TRIM",
    "OperationSpec",
    "Step",
]
