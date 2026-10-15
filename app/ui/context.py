"""应用上下文：把配置、工具链、校验层、命令构建层打包传给各功能页。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..config import Config
from ..core.builder import CommandBuilder
from ..core.prober import Prober
from ..core.validator import Validator
from ..ffmpeg_locator import INSTALL_HINT, Toolchain, detect_version, locate
from ..core.paths import default_output_dir


@dataclass
class AppContext:
    cfg: Config
    toolchain: Toolchain = field(default_factory=Toolchain)
    prober: Prober = field(default_factory=Prober)
    builder: CommandBuilder = field(default_factory=CommandBuilder)
    validator: Validator = field(default_factory=Validator)
    output_dir: str = ""
    theme: str = "dark"
    # 输出到源文件所在目录（问题 14：默认不再落到某个固定目录/磁盘根目录）
    follow_source_dir: bool = True

    @classmethod
    def create(cls, cfg: Config, theme: str) -> "AppContext":
        ctx = cls(cfg=cfg, theme=theme)
        ctx.builder.prober = ctx.prober
        ctx.prober.set_path(ctx.toolchain.ffprobe, ctx.toolchain.ffmpeg)
        ctx.refresh_toolchain()
        out_dir = str(cfg.get("output_dir") or "")
        if not out_dir or not os.path.isdir(out_dir):
            out_dir = default_output_dir()
            cfg.set("output_dir", out_dir)
        ctx.output_dir = out_dir
        stored_follow = cfg.get("follow_source_dir")
        ctx.follow_source_dir = True if stored_follow is None else bool(stored_follow)
        ctx.validator.overwrite_policy = str(cfg.get("overwrite_policy") or "rename")
        ctx.builder.hw_encoder = str(cfg.get("hw_encoder") or "none")
        return ctx

    def refresh_toolchain(self) -> Toolchain:
        """重新定位 ffmpeg/ffprobe 并同步到各层。"""
        ffmpeg = str(self.cfg.get("ffmpeg_path") or "")
        ffprobe = str(self.cfg.get("ffprobe_path") or "")
        if ffmpeg and not os.path.isfile(ffmpeg):
            ffmpeg = ""
        if ffprobe and not os.path.isfile(ffprobe):
            ffprobe = ""
        self.toolchain = locate(ffmpeg, ffprobe)
        self.prober.set_path(self.toolchain.ffprobe, self.toolchain.ffmpeg)
        # builder 需要 prober 才能判断"能否无损合并""智能切割是否可行"等
        self.builder.prober = self.prober
        self.builder.ffmpeg_path = self.toolchain.ffmpeg or "ffmpeg"
        self.validator.set_paths(self.toolchain.ffmpeg, self.toolchain.ffprobe)
        return self.toolchain

    def set_manual_paths(self, ffmpeg_path: str, ffprobe_path: str = "") -> Toolchain:
        self.cfg.set("ffmpeg_path", ffmpeg_path or "")
        self.cfg.set("ffprobe_path", ffprobe_path or "")
        self.cfg.save()
        return self.refresh_toolchain()

    def verify_single_path(self, ffmpeg_path: str) -> tuple[bool, str]:
        """设置对话框用：验证用户填的路径是不是可用的 ffmpeg。"""
        if not ffmpeg_path:
            return False, "路径为空"
        if not os.path.isfile(ffmpeg_path):
            return False, "文件不存在"
        text, version = detect_version(ffmpeg_path)
        if not version:
            return False, f"无法执行该文件，或不是 ffmpeg：{text or '无输出'}"
        directory = os.path.dirname(ffmpeg_path)
        probe = os.path.join(directory, "ffprobe.exe" if os.name == "nt" else "ffprobe")
        extra = "" if os.path.isfile(probe) else "（同目录未找到 ffprobe，建议一并指定）"
        return True, f"检测到 ffmpeg {'.'.join(str(x) for x in version)}{extra}"

    def install_hint(self) -> str:
        return INSTALL_HINT

    @property
    def ready(self) -> bool:
        return self.toolchain.ok and bool(self.toolchain.ffprobe)


__all__ = ["AppContext"]
