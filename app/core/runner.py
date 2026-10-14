"""Runner —— QProcess 异步执行 + 进度解析 + 信号回调 + 取消（需求 5.1 / 5.3 / 5.7）。

关键点：
- argv 列表直接给 QProcess，绝不拼接命令字符串，绝不 shell。
- 进度从 **stdout** 读机器可读键值对（``-progress pipe:1``）；stderr 只做日志。
- 进度用 ffprobe 预取的 duration 作为分母；拿不到就退化为"已处理时长 + 速度"。
- 临时文件 + 原子重命名：成功才 ``os.replace`` 成正式名。
- 取消：``terminate`` → 超时 ``kill``，并清理临时文件。
- 命令被手动编辑过 → 降级：无进度条、不校验输出路径、只显示 stderr 尾部。
- 若系统禁止通过管道拉起子进程（部分沙箱 / 安全策略 / 杀软环境会让
  QProcess 直接 FailedToStart），自动降级为"文件重定向 + 轮询"模式，
  功能与进度解析保持不变，只是进度延迟约 0.1s。
"""

from __future__ import annotations

import os
import shutil
import tempfile
import time
from collections import deque
from typing import Deque

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from .paths import cleanup_passlog, drop_temp_files
from .proc_util import decode, stderr_tail
from .spec import Job, Step


class Runner(QObject):
    """执行单个 Job（可含多个 Step）。"""

    stepStarted = Signal(int, int, str)          # 序号(1起), 总数, 描述
    progressChanged = Signal(float, float, str)  # 百分比(-1=不可知), 已处理秒数, 速度文本
    logLine = Signal(str)
    jobFinished = Signal(bool, str)              # 成功?, 结果说明
    jobFailed = Signal(str, str)                 # 错误摘要, stderr 尾部
    cancelled = Signal(str)

    TERMINATE_GRACE_MS = 2500
    POLL_INTERVAL_MS = 100

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._job: Job | None = None
        self._proc: QProcess | None = None
        self._step_index = 0
        self._stderr: Deque[str] = deque(maxlen=600)
        self._stdout_buffer = b""
        self._stderr_buffer = b""
        self._cancelled = False
        self._finished = False
        self._started_at = 0.0
        self._step_started_at = 0.0
        self._last_emit = 0.0
        self._last_line = ""
        self._last_out_time = 0.0
        # 管道模式 → 文件重定向模式的自动降级（环境属性，发现一次即全程沿用）
        self._mode = "pipe"
        self._retried_step = False
        self._stdio_dir: str | None = None
        self._stdin_path = ""
        self._stdout_path = ""
        self._stderr_path = ""
        self._out_offset = 0
        self._err_offset = 0
        self._poll_timer: QTimer | None = None

    # ------------------------------------------------------------------
    @property
    def busy(self) -> bool:
        return self._proc is not None and self._proc.state() != QProcess.NotRunning

    def start(self, job: Job) -> None:
        if self.busy:
            self.jobFailed.emit("已有任务正在执行", "")
            return
        self._job = job
        self._step_index = 0
        self._stderr.clear()
        self._stdout_buffer = b""
        self._stderr_buffer = b""
        self._cancelled = False
        self._finished = False
        self._retried_step = False
        self._started_at = time.time()
        self._log(f"▶ 开始：{job.label}")
        if job.warnings:
            for text in job.warnings:
                self._log(f"⚠ {text}")
        self._run_current_step()

    def cancel(self) -> None:
        proc = self._proc
        if proc is None or not self.busy:
            return
        self._cancelled = True
        self._log("⏹ 正在取消…")
        proc.terminate()
        if not proc.waitForFinished(self.TERMINATE_GRACE_MS):
            self._log("强制结束 ffmpeg 进程")
            proc.kill()
            proc.waitForFinished(2000)

    # ------------------------------------------------------------------
    # 内部：逐步执行
    # ------------------------------------------------------------------
    def _run_current_step(self, announce: bool = True) -> None:
        job = self._job
        if job is None or self._step_index >= len(job.steps):
            self._on_job_success()
            return

        step = job.steps[self._step_index]
        self.stdout_buffer_reset()
        self._stderr_buffer = b""
        self._stderr.clear()
        self._last_line = ""
        self._step_started_at = time.time()
        if announce:
            self.stepStarted.emit(self._step_index + 1, len(job.steps), step.label or "处理中")
            self._log(f"$ {step.preview()}")
            if not step.progress:
                self._log("（命令被手动编辑，进度与输出路径校验已降级）")

        proc = QProcess(self)
        proc.setProgram(step.argv[0])
        proc.setArguments(step.argv[1:])
        proc.setProcessChannelMode(QProcess.SeparateChannels)
        if self._mode == "file":
            self._prepare_file_channels(proc)
        else:
            proc.readyReadStandardOutput.connect(self._on_stdout)
            proc.readyReadStandardError.connect(self._on_stderr)
        proc.finished.connect(self._on_step_finished)
        proc.errorOccurred.connect(self._on_process_error)
        self._proc = proc

        proc.start()
        if self._mode == "file":
            self._start_polling()

    # ------------------------------------------------------------------
    # 文件重定向降级模式
    #
    # 背景：QProcess 默认用匿名/具名管道承载 stdio，某些环境会直接
    # FailedToStart（错误串形如 "pipe: 系统找不到指定的文件"）。此时把三个通道
    # 全部指向真实文件即可正常拉起进程，代价是要自己轮询文件增量。
    # ------------------------------------------------------------------
    def _ensure_stdio_dir(self) -> str:
        if self._stdio_dir and os.path.isdir(self._stdio_dir):
            return self._stdio_dir
        self._stdio_dir = tempfile.mkdtemp(prefix="ffkit_stdio_")
        return self._stdio_dir

    def _prepare_file_channels(self, proc: QProcess) -> None:
        directory = self._ensure_stdio_dir()
        self._stdin_path = os.path.join(directory, "stdin")
        self._stdout_path = os.path.join(directory, "stdout")
        self._stderr_path = os.path.join(directory, "stderr")
        # 每步都重新截断，配合偏移量清零，避免读到上一步的残留内容
        for path in (self._stdin_path, self._stdout_path, self._stderr_path):
            try:
                with open(path, "wb"):
                    pass
            except OSError:
                pass
        self._out_offset = 0
        self._err_offset = 0
        proc.setStandardInputFile(self._stdin_path)
        proc.setStandardOutputFile(self._stdout_path)
        proc.setStandardErrorFile(self._stderr_path)

    def _start_polling(self) -> None:
        if self._poll_timer is None:
            self._poll_timer = QTimer(self)
            self._poll_timer.setInterval(self.POLL_INTERVAL_MS)
            self._poll_timer.timeout.connect(self._poll_channels)
        self._poll_timer.start()

    def _stop_polling(self) -> None:
        if self._poll_timer is not None:
            self._poll_timer.stop()

    def _poll_channels(self) -> None:
        self._read_appended(self._stdout_path, "_out_offset", self._ingest_stdout_bytes)
        self._read_appended(self._stderr_path, "_err_offset", self._ingest_stderr_bytes)

    def _read_appended(self, path: str, offset_attr: str, sink) -> None:
        if not path:
            return
        try:
            size = os.path.getsize(path)
        except OSError:
            return
        offset = getattr(self, offset_attr)
        if size <= offset:
            return
        try:
            with open(path, "rb") as fh:
                fh.seek(offset)
                data = fh.read(size - offset)
        except OSError:
            return
        setattr(self, offset_attr, offset + len(data))
        sink(data)

    def _cleanup_stdio(self) -> None:
        self._stop_polling()
        if self._stdio_dir:
            shutil.rmtree(self._stdio_dir, ignore_errors=True)
            self._stdio_dir = None
        self._stdin_path = self._stdout_path = self._stderr_path = ""

    def stdout_buffer_reset(self) -> None:
        self._stdout_buffer = b""

    def _on_stdout(self) -> None:
        if self._proc is None or self._job is None:
            return
        self._ingest_stdout_bytes(bytes(self._proc.readAllStandardOutput()))

    def _ingest_stdout_bytes(self, chunk: bytes) -> None:
        self._stdout_buffer += chunk
        while b"\n" in self._stdout_buffer:
            raw, _, self._stdout_buffer = self._stdout_buffer.partition(b"\n")
            self._handle_progress_line(decode(raw).strip())

    def _handle_progress_line(self, line: str) -> None:
        if not line or "=" not in line:
            return
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        step = self._job.steps[self._step_index] if self._job else None
        if step is None:
            return

        if key == "progress":
            if value == "end":
                self._emit_progress(step.duration or 0.0, "")
            return
        if key == "speed":
            self._emit_progress(self._current_out_time(step), value)
            return
        if key in ("out_time_us", "out_time_ms"):
            # 注意：ffmpeg 的 out_time_ms 实际也是微秒（历史遗留命名问题）。
            # 因此优先用无歧义的 out_time（HH:MM:SS.ffffff），这里只作兜底。
            try:
                micro = float(value)
            except ValueError:
                return
            seconds = micro / 1_000_000.0
            self._emit_progress(seconds, "")
            return
        if key == "out_time":
            seconds = _parse_ff_time(value)
            if seconds is not None:
                self._emit_progress(seconds, "")
            return

    def _current_out_time(self, step: Step) -> float:
        return getattr(self, "_last_out_time", 0.0)

    def _emit_progress(self, out_time: float, speed: str) -> None:
        if self._job is None:
            return
        if out_time > 0:
            self._last_out_time = out_time
        else:
            out_time = getattr(self, "_last_out_time", 0.0)
        percent = self._overall_percent(out_time)
        now = time.time()
        # 限流：最多 12 次/秒，避免刷爆 UI
        if now - self._last_emit < 0.08 and percent < 99.0:
            return
        self._last_emit = now
        self.progressChanged.emit(percent, out_time, speed)

    def _overall_percent(self, out_time: float) -> float:
        job = self._job
        if job is None:
            return -1.0
        durations = [s.duration or 0.0 for s in job.steps]
        total = sum(durations)
        step = job.steps[self._step_index]
        if total <= 0 or (step.duration or 0) <= 0:
            return -1.0
        done = sum(durations[: self._step_index])
        current = min(max(out_time, 0.0), step.duration or 0.0)
        percent = (done + current) / total * 100.0
        # 单步封顶 99.4%，由结束事件补到 100%，避免"卡在 99% 后又跳变"
        step_floor = done / total * 100.0
        step_ceil = (done + (step.duration or 0.0)) / total * 100.0
        percent = max(step_floor, min(percent, min(step_ceil, 99.4)))
        return percent

    def _on_stderr(self) -> None:
        if self._proc is None:
            return
        self._ingest_stderr_bytes(bytes(self._proc.readAllStandardError()))

    def _ingest_stderr_bytes(self, chunk: bytes) -> None:
        self._stderr_buffer += chunk
        while b"\n" in self._stderr_buffer:
            raw, _, self._stderr_buffer = self._stderr_buffer.partition(b"\n")
            line = decode(raw).rstrip()
            if not line:
                continue
            self._stderr.append(line)
            # ffmpeg 默认在同一行刷新统计信息，只在内容变化时输出，避免日志刷屏
            if line != self._last_line:
                self._last_line = line
                self.logLine.emit(line)

    def _flush_stderr_buffer(self) -> None:
        """文件模式收尾：最后一段可能没有换行符，补一次。"""
        if not self._stderr_buffer:
            return
        raw, self._stderr_buffer = self._stderr_buffer, b""
        line = decode(raw).rstrip()
        if line:
            self._stderr.append(line)
            if line != self._last_line:
                self._last_line = line
                self.logLine.emit(line)

    def _on_process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.FailedToStart:
            # 管道不被系统允许 → 自动降级为文件重定向后重跑当前步（只重试一次）
            if self._mode == "pipe" and not self._retried_step and not self._finished:
                self._retried_step = True
                self._mode = "file"
                self._discard_proc()
                self._log("⚠ 系统不允许通过管道启动 ffmpeg，已自动切换为文件重定向模式")
                QTimer.singleShot(0, lambda: self._run_current_step(announce=False))
                return
            step = self._job.steps[self._step_index] if self._job else None
            program = step.argv[0] if step else "ffmpeg"
            message = f"无法启动进程：{program}"
            self._finish_failed(message, "请检查 ffmpeg 路径是否正确。")
        elif error == QProcess.Crashed and not self._cancelled:
            self._finish_failed("ffmpeg 进程异常终止", stderr_tail("\n".join(self._stderr)))

    def _discard_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is not None:
            try:
                proc.kill()
                proc.waitForFinished(500)
            except Exception:
                pass
            proc.deleteLater()
        self._stop_polling()

    def _on_step_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        job = self._job
        if job is None or self._finished:
            return
        if self._mode == "file":
            # 进程已退出 → 文件里必然写全了，最后捞一次再判读
            self._poll_channels()
            self._flush_stderr_buffer()
        self._stop_polling()
        step = job.steps[self._step_index]
        stderr_all = "\n".join(self._stderr)

        if self._cancelled:
            self._finish_cancelled()
            return

        if exit_status == QProcess.CrashExit or exit_code != 0:
            tail = stderr_tail(stderr_all) or f"ffmpeg 退出码 {exit_code}"
            self._log("✖ 本步失败：" + (tail.splitlines()[-1] if tail.strip() else str(exit_code)))
            self._finish_failed(f"处理失败（退出码 {exit_code}）", tail)
            return

        # 本步成功：原子重命名 + 清理本步的中间产物
        if step.temp_out and step.final_out and step.temp_out != step.final_out:
            try:
                os.replace(step.temp_out, step.final_out)
            except OSError as exc:
                self._finish_failed(f"无法写入输出文件：{exc}", stderr_tail(stderr_all))
                return
        if step.cleanup:
            drop_temp_files(step.cleanup)

        self._last_out_time = 0.0
        self._step_index += 1
        if self._step_index < len(job.steps):
            self._run_current_step()
        else:
            self._on_job_success()

    # ------------------------------------------------------------------
    # 收尾
    # ------------------------------------------------------------------
    def _on_job_success(self) -> None:
        if self._finished:
            return
        self._finished = True
        job = self._job
        self._proc = None
        self._cleanup_stdio()
        self._cleanup_passlog()
        elapsed = time.time() - self._started_at
        self.progressChanged.emit(100.0, 0.0, "")
        message = "完成"
        if job is not None and job.final_output and os.path.exists(job.final_output):
            size = os.path.getsize(job.final_output)
            from .timeparse import human_size, size_delta

            if job.source_sizes:
                message = f"完成 · {size_delta(sum(job.source_sizes), size)}"
            else:
                message = f"完成 · 输出 {human_size(size)}"
        self._log(f"✔ {job.label if job else ''} {message}（用时 {elapsed:.1f}s）")
        self.jobFinished.emit(True, message)

    def _finish_failed(self, message: str, tail: str) -> None:
        if self._finished:
            return
        self._finished = True
        self._stop_polling()
        self._cleanup_temp()
        self._cleanup_passlog()
        self._cleanup_stdio()
        self._proc = None
        self.jobFailed.emit(message, tail)

    def _finish_cancelled(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._stop_polling()
        self._cleanup_temp()
        self._cleanup_passlog()
        self._cleanup_stdio()
        self._proc = None
        self._log("⏹ 已取消，临时文件已清理")
        self.cancelled.emit("已取消")

    def _cleanup_passlog(self) -> None:
        job = self._job
        if job is None:
            return
        for prefix in job.passlog_prefixes:
            cleanup_passlog(prefix)

    def _cleanup_temp(self) -> None:
        """删除本次任务所有的临时/中间产物，避免留下半个损坏文件。"""
        job = self._job
        if job is None:
            return
        paths = set(job.temp_paths())
        for step in job.steps:
            if step.temp_out and step.final_out and step.temp_out != step.final_out:
                paths.add(step.temp_out)
        drop_temp_files(sorted(paths))

    def _log(self, text: str) -> None:
        if text:
            self.logLine.emit(text)


def _parse_ff_time(value: str) -> float | None:
    """解析 ffmpeg 的 out_time=00:00:01.234567。"""
    try:
        hours, minutes, seconds = value.split(":")
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    except (ValueError, AttributeError):
        return None


__all__ = ["Runner"]
