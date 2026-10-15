"""合并（Merge / Concat）—— 需求六.2。

判定从"同格式"改为"ffprobe 比对编码参数"：
- 编码参数完全一致 → concat demuxer（-c copy，无损秒合）
- 不一致 → 自动降级 concat filter（重编码，慢但必定成功），UI 明确标注走哪条路
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from ...core.paths import AUDIO_EXTS, VIDEO_EXTS, stem_of
from ...core.spec import OP_MERGE, OperationSpec
from ...core.timeparse import format_hms, human_size
from .base_page import BasePage


class MergePage(BasePage):
    TITLE = "合并"
    USES_PREVIEW = False
    ALLOW_REORDER = True
    ALLOWED_EXTS = VIDEO_EXTS | AUDIO_EXTS
    FILE_HINT = "拖入多个文件加入合并列表（可拖拽调整顺序）；编码参数不一致会自动降级重编码"
    LIST_HEIGHT = 210
    # 合并天然是多进一出，但没有"最多几个"的概念，只提示下限
    MIN_FILES = 2

    def _build_params(self, layout: QVBoxLayout) -> None:
        self.add_group_title(layout, "输出设置")
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        form.setSpacing(8)

        self.ed_name = QLineEdit()
        self.ed_name.setPlaceholderText("输出文件名（不含扩展名）")
        form.addRow("输出文件名", self.ed_name)

        self.cb_container = QComboBox()
        self.cb_container.addItem("MP4 (.mp4)", ".mp4")
        self.cb_container.addItem("MKV (.mkv)", ".mkv")
        self.cb_container.addItem("MP3 (.mp3)", ".mp3")
        self.cb_container.addItem("M4A (.m4a)", ".m4a")
        form.addRow("输出格式", self.cb_container)
        layout.addLayout(form)

        self.add_separator(layout)
        self.add_group_title(layout, "编码一致性")
        buttons = QHBoxLayout()
        self.btn_check = QPushButton("重新检测编码一致性")
        self.btn_check.setToolTip("加入文件后会自动检测，这里是手动重跑")
        buttons.addWidget(self.btn_check)
        buttons.addStretch(1)
        layout.addLayout(buttons)

        self.lbl_route = self.hint_label("等待添加文件…")
        layout.addWidget(self.lbl_route)

        self.lbl_detail = self.hint_label("")
        layout.addWidget(self.lbl_detail)

        self.btn_check.clicked.connect(self._check_consistency)
        self.ed_name.textEdited.connect(lambda _text: setattr(self, "_name_touched", True))
        self.ed_name.textChanged.connect(self.schedule_preview)
        self.cb_container.currentIndexChanged.connect(self.schedule_preview)
        self._name_touched = False
        self._checked = False

    # ------------------------------------------------------------------
    def _on_files_changed(self) -> None:
        super()._on_files_changed()
        self._sync_default_name()
        self._check_consistency()

    def _on_current_changed(self, path: str) -> None:
        super()._on_current_changed(path)
        self._refresh_summary()

    def add_files(self, paths: list[str]) -> int:
        added = super().add_files(paths)
        if added:
            self._on_files_changed()
        return added

    # ------------------------------------------------------------------
    def _sync_default_name(self) -> None:
        if self._name_touched:
            return
        paths = self.paths()
        if not paths:
            return
        self.ed_name.setText(f"{stem_of(paths[0])}_merged")

    def _refresh_summary(self) -> None:
        """信息区改为汇总视图（问题 13）：共 N 个 / 合计时长 / 合计体积 + 一致性结论。"""
        paths = self.paths()
        if not paths:
            self.info_panel.set_rows([("状态", "未选择文件")], "")
            return
        infos = [self.ctx.prober.get(p) for p in paths]
        rows: list[tuple[str, str]] = [
            ("文件数量", f"{len(paths)} 个"),
            ("合计时长", format_hms(sum(i.duration for i in infos if i.ok))),
            ("合计体积", human_size(sum(i.size for i in infos if i.ok))),
        ]
        good = [i for i in infos if i.ok]
        if good:
            reference = good[0]
            if reference.has_video:
                rows.append(("统一画布", f"{reference.resolution} @ {reference.fps_text}fps"))
            rows.append(("视频编码", reference.v_codec or "无视频流"))
            rows.append(("音频编码", reference.a_codec or "无音轨"))
        rows.append(("推荐路径", self._route_label()))
        mismatches = self._mismatches()
        if mismatches:
            rows.append(("不一致项", f"{len(mismatches)} 处"))
        else:
            rows.append(("一致性", "全部一致"))

        note = ""
        if mismatches:
            note = "不一致明细：" + "；".join(mismatches[:4]) + (
                f"；另有 {len(mismatches) - 4} 处" if len(mismatches) > 4 else ""
            )
        elif len(paths) >= 2:
            note = "编码参数完全一致，可直接无损拼接。"
        self.info_panel.set_rows(rows, note)

    def _route_label(self) -> str:
        if len(self.paths()) < 2:
            return "—"
        if self._mismatches():
            return "concat filter（重编码）"
        return "concat demuxer（无损）"

    def _mismatches(self) -> list[str]:
        paths = self.paths()
        if len(paths) < 2:
            return []
        infos = [self.ctx.prober.get(p) for p in paths[:40]]
        reference = infos[0]
        if not reference.ok:
            return []
        result: list[str] = []
        for path, info in zip(paths[1:], infos[1:]):
            name = os.path.basename(path)
            if not info.ok:
                result.append(f"{name} 无法解析")
                continue
            if info.video_signature() != reference.video_signature():
                if info.resolution != reference.resolution:
                    result.append(f"{name} 分辨率 {info.resolution} ≠ {reference.resolution}")
                elif info.v_codec != reference.v_codec:
                    result.append(f"{name} 视频编码 {info.v_codec} ≠ {reference.v_codec}")
                elif info.fps_text != reference.fps_text:
                    result.append(f"{name} 帧率 {info.fps_text}fps ≠ {reference.fps_text}fps")
                else:
                    result.append(f"{name} 像素格式/宽高比与第一个文件不一致")
            elif info.has_audio and reference.has_audio and info.audio_signature() != reference.audio_signature():
                if info.a_sample_rate != reference.a_sample_rate:
                    result.append(
                        f"{name} 采样率 {info.a_sample_rate}Hz ≠ {reference.a_sample_rate}Hz"
                    )
                elif info.a_codec != reference.a_codec:
                    result.append(f"{name} 音频编码 {info.a_codec} ≠ {reference.a_codec}")
                else:
                    result.append(f"{name} 声道数与第一个文件不一致")
            elif info.has_audio != reference.has_audio:
                result.append(f"{name} 音轨有无与第一个文件不同")
        return result

    def can_run(self) -> tuple[bool, str]:
        """检测未完成/结论未定就不让开始（问题 5）。"""
        paths = self.paths()
        if len(paths) < 2:
            return False, "至少需要 2 个文件才能合并"
        if not self._checked:
            return False, "正在检测编码一致性，请稍候"
        infos = [self.ctx.prober.get(p) for p in paths[:40]]
        bad = [os.path.basename(p) for p, i in zip(paths, infos) if not i.ok]
        if bad:
            return False, f"以下文件无法解析，请先移除：{', '.join(bad[:3])}"
        return True, ""

    def _check_consistency(self) -> None:
        paths = self.paths()
        if len(paths) < 2:
            self._checked = True
            self.lbl_route.setText("至少需要 2 个文件才能合并。")
            self.lbl_route.setObjectName("Notice")
            self.lbl_detail.setText("")
            self._repolish(self.lbl_route)
            self._refresh_summary()
            self.schedule_preview()
            return

        probe_limit = 40
        if len(paths) > probe_limit:
            self.lbl_route.setText(f"⚠ 文件数超过 {probe_limit}，仅对前 {probe_limit} 个做一致性预检，执行时仍会逐个判定。")
            self.lbl_route.setObjectName("NoticeWarn")
            self._repolish(self.lbl_route)
            paths = paths[:probe_limit]

        # 批量 ffprobe 可能要几秒 → 放到后台线程，期间"开始"按钮保持禁用
        self._checked = False
        self._check_token = getattr(self, "_check_token", 0) + 1
        token = self._check_token
        self.lbl_route.setText(f"正在检测 {len(paths)} 个文件的编码一致性…")
        self.lbl_route.setObjectName("Notice")
        self._repolish(self.lbl_route)
        self.lbl_detail.setText("")
        self.run_in_background(
            lambda: [self.ctx.prober.get(p) for p in paths],
            lambda payload, token=token, paths=list(paths):
                self._on_consistency_probed(payload, token, paths),
        )

    def _on_consistency_probed(self, payload, token: int, paths: list[str]) -> None:
        if token != getattr(self, "_check_token", 0):
            return  # 过期结果：列表又变了，以最新一次检测为准
        infos = payload[0] or []
        if self.paths()[:len(paths)] != paths:
            return  # 文件列表已变化，等新一轮检测
        bad = [p for p, i in zip(paths, infos) if not i.ok]
        if bad:
            self._checked = True
            self.lbl_route.setText(f"⚠ 有 {len(bad)} 个文件无法解析：{', '.join(os.path.basename(b) for b in bad[:3])}")
            self.lbl_route.setObjectName("NoticeDanger")
            self._repolish(self.lbl_route)
            self._refresh_summary()
            self.schedule_preview()
            return

        mixed_media = {("audio" if i.is_audio_only else "video") for i in infos}
        reference = infos[0]
        mismatch: list[str] = []
        for path, info in zip(paths[1:], infos[1:]):
            if info.video_signature() != reference.video_signature():
                mismatch.append(f"{os.path.basename(path)}：分辨率/编码/帧率/像素格式不一致")
            elif info.has_audio and reference.has_audio and info.audio_signature() != reference.audio_signature():
                mismatch.append(f"{os.path.basename(path)}：音频采样率/声道/编码不一致")
            if info.has_audio != reference.has_audio:
                mismatch.append(f"{os.path.basename(path)}：音轨有无与首个文件不同")

        total_duration = sum(i.duration for i in infos)
        self.lbl_detail.setText(
            f"共 {len(paths)} 个文件，合计时长 {format_hms(total_duration)}；"
            f"统一画布参考第一个文件 {reference.resolution} @ {reference.fps_text}fps。"
        )

        if mixed_media == {"video", "audio"}:
            self.lbl_route.setText("⚠ 列表里同时存在纯音频文件与视频文件，合并结果可能不符合预期，建议只合并同类型文件。")
            self.lbl_route.setObjectName("NoticeDanger")
        elif not mismatch:
            self.lbl_route.setText(
                "✔ 编码参数完全一致 → 走 concat demuxer 无损合并（-c copy），秒级完成、无画质损失。"
            )
            self.lbl_route.setObjectName("NoticeOk")
        else:
            preview = "；".join(mismatch[:3])
            more = f"；另有 {len(mismatch) - 3} 项" if len(mismatch) > 3 else ""
            self.lbl_route.setText(
                f"⚠ 编码参数不一致 → 自动降级为 concat filter 重编码（较慢，但必定成功）。\n{preview}{more}"
            )
            self.lbl_route.setObjectName("NoticeWarn")
        self._repolish(self.lbl_route)
        self._checked = True
        self._refresh_summary()
        self.schedule_preview()
        self.runAvailabilityChanged.emit()

    @staticmethod
    def _repolish(widget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    # ------------------------------------------------------------------
    def build_specs(self) -> list[OperationSpec]:
        paths = self.paths()
        if len(paths) < 2:
            return []
        ext = self.cb_container.currentData() or ".mp4"
        name = self.ed_name.text().strip() or f"{stem_of(paths[0])}_merged"
        name = _sanitize(name)
        out = os.path.join(self.output_dir_for(paths[0]), f"{name}{ext}")

        return [
            OperationSpec(
                op=OP_MERGE,
                inputs=paths,
                output=out,
                params={"container": ext},
                label=f"合并 {len(paths)} 个文件",
                duration=sum(i.duration for i in (self.ctx.prober.get(p) for p in paths) if i.ok),
            )
        ]


def _sanitize(name: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if ch in invalid else ch for ch in name).strip().strip(".")
    return cleaned or "merged"


__all__ = ["MergePage"]
