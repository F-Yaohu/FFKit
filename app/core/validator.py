"""Validator 校验层（需求第四节 / 5.6 / 十二.6）。

必须在执行前完成这些检查，并明确区分两类问题：

- **全局错误（GLOBAL）**：ffmpeg 不存在、输出目录不可写、磁盘空间不足
  → 立即中断整个队列，避免逐个失败。
- **单文件错误（FILE）**：某个输入文件不存在/损坏、源文件缺音轨等
  → 跳过该文件继续，结束后汇总。
- **提醒（WARN）**：输出已存在将被重命名、切割区间超出时长等，不阻断执行。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum

from .paths import dir_of, ext_of, free_space, is_writable_dir, unique_output
from .spec import OP_MERGE, OP_TRIM, Job, OperationSpec


class Severity(str, Enum):
    WARN = "warn"
    FILE = "file"
    GLOBAL = "global"


@dataclass
class Issue:
    severity: Severity
    message: str
    hint: str = ""
    path: str = ""


@dataclass
class ValidationReport:
    issues: list[Issue] = field(default_factory=list)
    resolved_output: str | None = None  # 冲突被自动改名后的实际输出路径

    def add(self, severity: Severity, message: str, hint: str = "", path: str = "") -> None:
        self.issues.append(Issue(severity, message, hint, path))

    @property
    def has_global(self) -> bool:
        return any(i.severity is Severity.GLOBAL for i in self.issues)

    @property
    def has_file_error(self) -> bool:
        return any(i.severity is Severity.FILE for i in self.issues)

    @property
    def has_warning(self) -> bool:
        return any(i.severity is Severity.WARN for i in self.issues)

    def of(self, severity: Severity) -> list[Issue]:
        return [i for i in self.issues if i.severity is severity]

    def global_messages(self) -> list[str]:
        return [i.message for i in self.of(Severity.GLOBAL)]

    def file_messages(self) -> list[str]:
        return [i.message for i in self.of(Severity.FILE)]

    def warning_messages(self) -> list[str]:
        return [i.message for i in self.of(Severity.WARN)]

    def first_file_error(self) -> str:
        items = self.file_messages()
        return items[0] if items else ""


# 判定运行期"全局错误"（中断整个队列）的关键词
GLOBAL_ERROR_PATTERNS = (
    "no space left",
    "disk full",
    "not enough space",
    "permission denied",
    "access is denied",
    "read-only file system",
    "cannot open output",
    "error opening output",
    "no such file or directory",
    "is not recognized as an internal or external command",
    "program not found",
    "找不到指定的文件",
    "拒绝访问",
    "空间不足",
)


class Validator:
    def __init__(
        self,
        ffmpeg_path: str | None = None,
        ffprobe_path: str | None = None,
        overwrite_policy: str = "rename",
    ) -> None:
        self.ffmpeg_path = ffmpeg_path or ""
        self.ffprobe_path = ffprobe_path or ""
        self.overwrite_policy = overwrite_policy
        self.min_free_bytes = 200 * 1024 * 1024  # 200MB 余量

    # ------------------------------------------------------------------
    def set_paths(self, ffmpeg_path: str, ffprobe_path: str) -> None:
        self.ffmpeg_path = ffmpeg_path or ""
        self.ffprobe_path = ffprobe_path or ""

    # ---------- 环境检查（启动时 / 每次执行前） ----------
    def check_environment(self, output_dir: str | None = None) -> list[Issue]:
        issues: list[Issue] = []
        if not self.ffmpeg_path or not os.path.isfile(self.ffmpeg_path):
            issues.append(
                Issue(
                    Severity.GLOBAL,
                    "未找到 ffmpeg，无法执行任何处理",
                    "请安装 ffmpeg 并加入 PATH，或在「设置」中手动指定 ffmpeg.exe 路径。",
                )
            )
        if not self.ffprobe_path or not os.path.isfile(self.ffprobe_path):
            issues.append(
                Issue(
                    Severity.GLOBAL,
                    "未找到 ffprobe（应与 ffmpeg 同目录）",
                    "ffprobe 用于读取媒体信息与计算进度，请一并指定。",
                )
            )
        if output_dir:
            if not os.path.isdir(output_dir):
                issues.append(
                    Issue(Severity.GLOBAL, f"输出目录不存在：{output_dir}", "请重新选择输出目录。")
                )
            elif not is_writable_dir(output_dir):
                issues.append(
                    Issue(
                        Severity.GLOBAL,
                        f"输出目录不可写：{output_dir}",
                        "请换一个目录，或检查权限/磁盘只读状态。",
                    )
                )
        return issues

    # ---------- 单个 Job ----------
    def validate(self, job: Job, resolve_conflicts: bool = True, check_steps: bool = True) -> ValidationReport:
        report = ValidationReport()

        # 1) 环境
        for issue in self.check_environment():
            report.issues.append(issue)

        # 2) 输入文件
        for path in job.spec.inputs:
            if not path:
                report.add(Severity.FILE, "存在空的输入路径")
            elif not os.path.isfile(path):
                report.add(Severity.FILE, f"输入文件不存在：{path}", path=path)

        # 3) 输出路径
        out = job.final_output
        if out:
            directory = dir_of(out)
            if not os.path.isdir(directory):
                report.add(
                    Severity.FILE,
                    f"输出目录不存在：{directory}",
                    "请重新选择输出目录。",
                    path=directory,
                )
            elif not is_writable_dir(directory):
                report.add(
                    Severity.GLOBAL,
                    f"输出目录不可写：{directory}",
                    "请换一个目录，或检查权限。",
                    path=directory,
                )

            if os.path.exists(out):
                if self.overwrite_policy == "overwrite":
                    report.add(Severity.WARN, f"输出文件已存在，将按设置直接覆盖：{os.path.basename(out)}")
                elif resolve_conflicts:
                    resolved = unique_output(out)
                    report.resolved_output = resolved
                    report.add(
                        Severity.WARN,
                        f"输出文件已存在，已自动改名为：{os.path.basename(resolved)}",
                    )
                else:
                    report.add(
                        Severity.WARN,
                        f"输出文件已存在，需要你确认是否覆盖：{os.path.basename(out)}",
                    )
            # 临时文件残留
            temp_probe = out + ".tmp"
            if os.path.exists(temp_probe):
                report.add(Severity.WARN, f"检测到上次遗留的临时文件，执行时将被覆盖：{os.path.basename(temp_probe)}")

        # 4) 磁盘空间（P1）
        if out:
            needed = self._estimate_need(job)
            available = free_space(dir_of(out))
            if available >= 0 and needed > 0 and available < needed + self.min_free_bytes:
                report.add(
                    Severity.GLOBAL,
                    f"磁盘空间不足：预计需要 {needed / 1024 / 1024:.0f} MB，"
                    f"当前可用 {max(available, 0) / 1024 / 1024:.0f} MB",
                    "请清理目标磁盘或更换输出目录。",
                    path=dir_of(out),
                )

        # 5) 时间参数（剪辑）
        if job.spec.op == OP_TRIM:
            start = float(job.spec.params.get("start") or 0.0)
            end = float(job.spec.params.get("end") or 0.0)
            duration = job.spec.duration or (job.spec.info.duration if job.spec.info else 0.0)
            if end <= start:
                report.add(Severity.FILE, "结束时间必须大于开始时间", path=job.spec.inputs[0] if job.spec.inputs else "")
            elif duration and end > duration + 0.5:
                report.add(
                    Severity.WARN,
                    f"结束时间 {end:.2f}s 超出视频时长 {duration:.2f}s，将按实际结尾截断",
                )

        if job.spec.op == OP_MERGE and len(job.spec.inputs) < 2:
            report.add(Severity.FILE, "合并需要至少 2 个文件")
        if job.spec.op == OP_MERGE:
            for path in job.spec.inputs:
                if path and ext_of(path) in (".jpg", ".png", ".bmp", ".gif", ".webp"):
                    report.add(Severity.FILE, f"合并列表里混入了图片文件：{os.path.basename(path)}")

        # 6) 构建阶段已知的问题
        if check_steps and not job.steps:
            report.add(Severity.FILE, "没有可执行的步骤（参数组合无效）")

        return report

    def validate_spec(self, spec: OperationSpec) -> ValidationReport:
        """对尚未编译成 Job 的 Spec 做校验（用于参数调整期的即时提示）。"""
        job = Job(spec=spec, steps=[], final_output=spec.output,
                  source_sizes=[spec.info.size] if spec.info else [])
        return self.validate(job, check_steps=False)

    def _estimate_need(self, job: Job) -> int:
        """粗估需要多少磁盘空间。"""
        total_source = 0
        for path in job.spec.inputs:
            try:
                total_source += os.path.getsize(path)
            except OSError:
                pass
        if total_source <= 0:
            total_source = 30 * 1024 * 1024
        # 无损复制 ≈ 源文件大小；重编码 ≈ 源文件的 1.3 倍；多步任务按步数累加
        copies = 0
        encodes = 0
        for step in job.steps:
            argv = step.argv
            is_copy = "-c" in argv and "copy" in argv and "-crf" not in argv and "-b:v" not in argv
            if is_copy:
                copies += 1
            else:
                encodes += 1
        return int(total_source * (copies + encodes * 1.3) + 20 * 1024 * 1024)

    # ---------- 批量预检 ----------
    def preflight(self, jobs: list[Job] | list[OperationSpec], resolve_conflicts: bool = True) -> ValidationReport:
        merged = ValidationReport()
        for item in jobs:
            if isinstance(item, OperationSpec):
                report = self.validate_spec(item)
                label = item.display_name()
            else:
                report = self.validate(item, resolve_conflicts=resolve_conflicts)
                label = item.label
            for issue in report.issues:
                merged.issues.append(
                    Issue(
                        issue.severity,
                        f"[{label}] {issue.message}",
                        issue.hint,
                        issue.path,
                    )
                )
        return merged


def is_global_error(message: str, stdout_or_stderr: str = "") -> bool:
    """运行期错误判定：是否为需要立刻中断整个队列的全局错误。"""
    blob = f"{message}\n{stdout_or_stderr}".lower()
    return any(pattern in blob for pattern in GLOBAL_ERROR_PATTERNS)


__all__ = [
    "GLOBAL_ERROR_PATTERNS",
    "Issue",
    "Severity",
    "ValidationReport",
    "Validator",
    "is_global_error",
]
