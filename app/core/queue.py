"""批量队列（需求 5.6 / 十二.6）。

失败策略是这个模块存在的主要理由：

- **单文件错误** → 跳过并继续，结束后汇总失败清单与各自 stderr 摘要。
- **全局错误**（ffmpeg 不存在、输出目录不可写、磁盘空间不足）→ 立即中断整个队列。

判定分两道：
1. 每个任务开跑前重新检查环境（输出目录可写、磁盘空间），坏掉就立刻中断；
2. 任务失败后按关键词判定 globals（见 validator.is_global_error），命中即中断。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtCore import QObject, Signal

from .paths import dir_of, free_space, is_writable_dir
from .runner import Runner
from .spec import Job
from .validator import Severity, Validator, is_global_error


@dataclass
class JobResult:
    index: int
    label: str
    ok: bool
    message: str = ""
    stderr_tail: str = ""


@dataclass
class QueueSummary:
    total: int = 0
    ok_count: int = 0
    failures: list[JobResult] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""
    user_cancelled: bool = False

    @property
    def fail_count(self) -> int:
        return len(self.failures)


class TaskQueue(QObject):
    queueStarted = Signal(int)                    # 任务总数
    jobStarted = Signal(int, int, str)            # 序号, 总数, 名称
    progressChanged = Signal(int, float, float, str)   # 序号, 百分比(-1未知), 已处理秒, 速度
    jobResult = Signal(int, bool, str, str)       # 序号, 成功?, 摘要, stderr
    logLine = Signal(str)
    queueFinished = Signal(object)                # QueueSummary
    queueAborted = Signal(str)                    # 中断原因

    def __init__(self, validator: Validator, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.validator = validator
        self.runner = Runner(self)
        self._jobs: list[Job] = []
        self._index = 0
        self._summary = QueueSummary()
        self._running = False

        self.runner.stepStarted.connect(self._on_step_started)
        self.runner.progressChanged.connect(self._on_progress)
        self.runner.logLine.connect(self.logLine)
        self.runner.jobFinished.connect(self._on_job_finished)
        self.runner.jobFailed.connect(self._on_job_failed)
        self.runner.cancelled.connect(lambda _msg: self._on_job_cancelled())

    # ------------------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._running

    @property
    def summary(self) -> QueueSummary:
        return self._summary

    def start(self, jobs: list[Job]) -> None:
        if self._running or not jobs:
            return
        self._jobs = list(jobs)
        self._index = 0
        self._summary = QueueSummary(total=len(jobs))
        self._running = True
        self.queueStarted.emit(len(jobs))
        self._run_next()

    def cancel(self) -> None:
        if not self._running:
            return
        self.runner.cancel()

    # ------------------------------------------------------------------
    def _current(self) -> Job | None:
        if 0 <= self._index < len(self._jobs):
            return self._jobs[self._index]
        return None

    def _run_next(self) -> None:
        if self._index >= len(self._jobs):
            self._finish()
            return

        job = self._jobs[self._index]
        number = self._index + 1

        # 预检阶段已标记跳过
        if job.skip_reason:
            self._record_failure(job, job.skip_reason)
            self._advance()
            return

        # 每跑一个任务前重新确认环境 —— 覆盖"跑到一半目录变不可写/磁盘满"的情况
        global_issue = self._environment_gate(job)
        if global_issue:
            self._abort(global_issue)
            return

        self.jobStarted.emit(number, len(self._jobs), job.label)
        self.runner.start(job)

    def _environment_gate(self, job: Job) -> str:
        out = job.final_output
        if out:
            directory = dir_of(out)
            if not os.path.isdir(directory):
                return f"输出目录不存在：{directory}"
            if not is_writable_dir(directory):
                return f"输出目录不可写：{directory}（队列已中断）"
            available = free_space(directory)
            if 0 <= available < 50 * 1024 * 1024:
                return f"磁盘空间不足（仅剩 {available / 1024 / 1024:.0f} MB），队列已中断"
        return ""

    # ---------- Runner 回调 ----------
    def _on_step_started(self, index: int, total: int, label: str) -> None:
        if total > 1:
            self.logLine.emit(f"  ── 第 {index}/{total} 步：{label}")

    def _on_progress(self, percent: float, out_time: float, speed: str) -> None:
        self.progressChanged.emit(self._index + 1, percent, out_time, speed)

    def _on_job_finished(self, ok: bool, message: str) -> None:
        job = self._current()
        if job is None:
            return
        if ok:
            self._summary.ok_count += 1
            self.jobResult.emit(self._index + 1, True, message, "")
            self.logLine.emit(f"✔ [{self._index + 1}/{len(self._jobs)}] {job.label}：{message}")
        self._advance()

    def _on_job_failed(self, message: str, stderr_text: str) -> None:
        job = self._current()
        if job is None:
            return
        if is_global_error(message, stderr_text):
            self._abort(f"{message}：{_first_line(stderr_text)}")
            return
        self._record_failure(job, message, stderr_text)
        self._advance()

    def _on_job_cancelled(self) -> None:
        job = self._current()
        label = job.label if job else ""
        self.logLine.emit(f"⏹ 已取消：{label}")
        self._running = False
        self._summary.aborted = True
        self._summary.user_cancelled = True
        self._summary.abort_reason = "用户取消"
        self.queueFinished.emit(self._summary)

    # ---------- 收尾 ----------
    def _record_failure(self, job: Job, message: str, stderr_text: str = "") -> None:
        result = JobResult(
            index=self._index + 1,
            label=job.label,
            ok=False,
            message=message,
            stderr_tail=stderr_text,
        )
        self._summary.failures.append(result)
        self.jobResult.emit(self._index + 1, False, message, stderr_text)
        self.logLine.emit(f"✖ [{self._index + 1}/{len(self._jobs)}] {job.label}：{message}")

    def _advance(self) -> None:
        self._index += 1
        if self._index >= len(self._jobs):
            self._finish()
        else:
            self._run_next()

    def _finish(self) -> None:
        if not self._running:
            return
        self._running = False
        self.queueFinished.emit(self._summary)

    def _abort(self, reason: str) -> None:
        self._running = False
        self._summary.aborted = True
        self._summary.abort_reason = reason
        self.logLine.emit(f"■■ 队列中断：{reason}")
        self.queueAborted.emit(reason)
        self.queueFinished.emit(self._summary)


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()
    return ""


__all__ = ["JobResult", "QueueSummary", "Severity", "TaskQueue"]
