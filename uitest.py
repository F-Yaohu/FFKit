"""GUI 冒烟测试：不需要真实 ffmpeg，用桩数据把每个功能页跑一遍。

- 构造主窗口，逐个功能页添加文件、生成命令预览、切换主题、抓取截图
- 用于验证界面装配是否正常、命令预览是否与参数联动
- 第二轮实测修复的回归项（问题 1/2/3/4/5/10/11/14/20 + 新增 4）也在这里验证

运行：python uitest.py [输出截图目录]
"""

from __future__ import annotations

import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# offscreen 平台默认不加载任何字体（中文会渲染成方框）；显式指到系统字体目录
if os.name == "nt":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from PySide6.QtCore import QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication, QComboBox, QTableWidgetItem  # noqa: E402

from app.config import Config  # noqa: E402
from app.core.prober import MediaInfo  # noqa: E402
from app.ui.context import AppContext  # noqa: E402
from app.ui.main_window import MainWindow  # noqa: E402
from app.ui.theme import stylesheet  # noqa: E402
from app.ui.widgets.inputs import WheelGuard, install_wheel_guard  # noqa: E402

FAILED: list[str] = []
PASSED = 0


def _force_utf8_output() -> None:
    """把标准输出/错误切到 UTF-8。

    Windows 控制台默认编码可能是 GBK 或 cp1252，直接 print 中文与 ✓/✖ 会抛
    UnicodeEncodeError（GitHub Actions 的 Windows runner 就是 cp1252）。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
    else:
        FAILED.append(name + (f"  → {detail}" if detail else ""))


def fake_info(path: str, audio_only: bool = False, duration: float = 120.0) -> MediaInfo:
    info = MediaInfo(
        path=path, ok=True, size=120 * 1024 * 1024, duration=duration,
        format_name="mov,mp4,m4a,3gp,3g2,mj2", bit_rate=8_000_000,
        v_codec="h264", v_width=1920, v_height=1080, v_fps=30.0, v_fps_raw="30/1",
        v_pix_fmt="yuv420p", v_sar="1:1", v_bit_rate=7_800_000,
        a_codec="aac", a_channels=2, a_sample_rate=48000, a_bit_rate=192_000,
    )
    if audio_only:
        info.v_codec = ""
        info.v_width = info.v_height = 0
        info.streams = [
            {"index": 0, "codec_type": "audio", "codec_name": "aac", "channels": 2,
             "sample_rate": 48000, "disposition": {"default": 1}, "tags": {"language": "chi"}},
        ]
    else:
        info.streams = [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920,
             "height": 1080, "disposition": {}, "tags": {}},
            {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2,
             "sample_rate": 48000, "disposition": {"default": 1},
             "tags": {"language": "chi", "title": "原声"}},
            {"index": 2, "codec_type": "subtitle", "codec_name": "subrip",
             "disposition": {}, "tags": {"language": "eng"}},
        ]
    return info


def main() -> int:
    _force_utf8_output()
    out_dir = sys.argv[1] if len(sys.argv) > 1 else os.path.join(tempfile.gettempdir(), "ffkit_shots")
    os.makedirs(out_dir, exist_ok=True)

    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # offscreen 下挑一个真实存在的中文字体，保证截图里的文字可读
    families = set(QFontDatabase.families())
    cjk_font = next(
        (name for name in ("Microsoft YaHei UI", "Microsoft YaHei", "SimHei",
                           "HP Simplified Hans", "Arial Unicode MS") if name in families),
        "",
    )
    app.setFont(QFont(cjk_font or "Sans Serif", 9))
    install_wheel_guard(app)

    work = tempfile.mkdtemp(prefix="ffkit_uitest_")
    sources = []
    for index in range(3):
        path = os.path.join(work, f"示例 视频'{index}'.mp4")
        with open(path, "wb") as fh:
            fh.write(b"\x00" * 2048)
        sources.append(path)
    audio_src = os.path.join(work, "音乐 音频.mp3")
    with open(audio_src, "wb") as fh:
        fh.write(b"\x00" * 1024)
    cover_src = os.path.join(work, "封面.jpg")
    with open(cover_src, "wb") as fh:
        fh.write(b"\x00" * 512)

    cfg = Config(path=os.path.join(work, "config.json"))
    cfg.set("output_dir", work)
    cfg.save()

    ctx = AppContext.create(cfg, "dark")
    # 用桩替换 prober：没有真实 ffmpeg 也能验证页面逻辑
    infos = {p: fake_info(p) for p in sources}
    infos[audio_src] = fake_info(audio_src, audio_only=True, duration=200.0)
    ctx.prober.get = lambda path, _infos=infos: _infos.get(path, MediaInfo(path=path, ok=False, error="桩数据缺失"))  # type: ignore[assignment]
    ctx.prober.keyframes = lambda path, timeout=180.0: [0.0, 10.0, 20.0, 30.0, 60.0, 90.0, 120.0]  # type: ignore[assignment]
    # 强制模拟"未找到 ffmpeg"的场景（本机装了 ffmpeg 也不影响）
    ctx.toolchain.ffmpeg = ""
    ctx.toolchain.ffprobe = ""
    ctx.refresh_toolchain = lambda: ctx.toolchain  # type: ignore[method-assign]
    ctx.prober.ffprobe_path = ""

    app.setStyleSheet(stylesheet("dark"))
    window = MainWindow(ctx)
    window.resize(1360, 880)
    window.show()
    app.processEvents()

    check("主窗口构建成功", window.isVisible())
    check("七个功能页都已装配", len(window.pages) == 7, str(len(window.pages)))
    check("ffmpeg 缺失时显示提示条", window.banner.isVisibleTo(window))

    # ---- 品牌资源：图标必须真实加载，不能是空图标 ----
    from app.resources import app_icon, assets_available, logo_pixmap

    check("assets 资源目录可用", assets_available())
    check("应用图标非空", not app_icon().isNull())
    check("应用图标含多尺寸", len(app_icon().availableSizes()) >= 5,
          str(app_icon().availableSizes()))
    check("主窗口已设置图标", not window.windowIcon().isNull())
    check("关于对话框用的 logo 位图非空", not logo_pixmap(96).isNull())

    # ---- 恢复一个"存在"的桩工具链，让开始按钮的可用性逻辑可以真实走通 ----
    fake_ff = os.path.join(work, "ffmpeg.exe")
    fake_fp = os.path.join(work, "ffprobe.exe")
    for probe in (fake_ff, fake_fp):
        with open(probe, "wb") as fh:
            fh.write(b"\x00" * 16)
    ctx.toolchain.ffmpeg = fake_ff
    ctx.toolchain.ffprobe = fake_fp
    ctx.toolchain.source = "测试桩"
    window._refresh_toolchain()
    app.processEvents()
    check("工具链就绪后提示条隐藏", not window.banner.isVisibleTo(window))

    expectations = {
        0: ("剪辑", ["-ss", "-t", "libx264", "-progress"]),
        1: ("合并", ["concat", "-safe"]),
        2: ("轨道", ["-map", "copy"]),
        3: ("转换", ["-i", "-progress"]),
        4: ("提取", ["-vn", "copy"]),
        5: ("压缩", ["-crf", "libx264"]),
        6: ("属性", ["-metadata", "title="]),
    }

    for index, page in enumerate(window.pages):
        window.nav.setCurrentRow(index)
        app.processEvents()
        page.files.clear_all()
        if index == 2:
            files = [sources[0]]
        else:
            files = sources + ([audio_src] if index in (0, 3, 4) else [])
        page.add_files(files)
        app.processEvents()
        if index == 6:
            # 属性页默认是"封面"模式，没有图片时没有命令；切成元数据模式填一项
            page.cb_mode.setCurrentIndex(1)
            page.edits["title"].setText("测试标题")
        page.refresh_now()
        app.processEvents()

        title, needles = expectations[index]
        if index == 0:
            page.ed_start.setText("00:00:10")
            page.ed_end.setText("00:00:30")
            page.refresh_now()
            app.processEvents()
        command = page.command_box.command_text()
        check(f"{title}页命令预览非空", bool(command.strip()), command[:80])
        missing = [token for token in needles if token not in command]
        check(f"{title}页命令包含关键参数", not missing, f"缺少 {missing}\n{command[:300]}")
        check(f"{title}页命令预览不含临时后缀 .tmp", ".tmp" not in command, command[:200])

        specs = page.build_specs()
        check(f"{title}页能生成任务", bool(specs), str(len(specs)))
        if specs:
            check(f"{title}页输出路径已确定", bool(specs[0].output), str(specs[0].output))
            check(f"{title}页输出不覆盖源文件", specs[0].output not in sources,
                  str(specs[0].output))

        shot = os.path.join(out_dir, f"{index + 1}_{title}_dark.png")
        window.grab().save(shot)
        check(f"{title}页截图已生成", os.path.isfile(shot), shot)

    # ==================================================================
    # 第二轮实测反馈回归
    # ==================================================================

    # ---- 新增 3：多轨封装页 ----
    track = window.pages[2]
    window.nav.setCurrentRow(2)
    track.files.clear_all()
    track.add_files([sources[0]])
    app.processEvents()
    check("轨道页能读出内嵌轨道", track.track_table.rowCount() == 3,
          str(track.track_table.rowCount()))
    check("轨道页初始全部勾选", all(
        track.track_table.item(r, 0).checkState() == Qt.Checked
        for r in range(track.track_table.rowCount())))
    extra_audio_path = os.path.join(work, "配音.aac")
    open(extra_audio_path, "wb").close()
    track.extra_table.insertRow(0)
    ext_item = QTableWidgetItem("配音.aac")
    ext_item.setData(Qt.UserRole, extra_audio_path)
    track.extra_table.setItem(0, 0, ext_item)
    track.extra_table.setItem(0, 1, QTableWidgetItem("音轨"))
    track.extra_table.setItem(0, 2, QTableWidgetItem("yue"))
    track.extra_table.setItem(0, 3, QTableWidgetItem("粤语配音"))
    dflt = QTableWidgetItem()
    dflt.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
    dflt.setCheckState(Qt.Checked)
    track.extra_table.setItem(0, 4, dflt)
    track.refresh_now()
    app.processEvents()
    mux_cmd = track.command_box.command_text()
    check("轨道页命令保留内嵌音轨", "-map 0:a:0" in mux_cmd, mux_cmd[:200])
    check("轨道页命令挂载外部音轨", "-map 1:a:0" in mux_cmd, mux_cmd[:240])
    check("轨道页命令写语言标签", "-metadata:s:a:1 language=yue" in mux_cmd, mux_cmd[:240])
    check("轨道页命令默认轨标记", "-disposition:a:1 default" in mux_cmd, mux_cmd[:240])
    check("轨道页视频无损复制", "-c:v copy" in mux_cmd, mux_cmd[:240])
    mux_specs = track.build_specs()
    check("轨道页能生成任务", bool(mux_specs) and mux_specs[0].output.endswith(".mp4"),
          str(mux_specs[0].output if mux_specs else None))
    track.extra_table.setRowCount(0)
    track.files.clear_all()
    app.processEvents()

    # ---- 问题 1：转换页选音频容器时不得带任何视频参数 ----
    conv = window.pages[3]
    window.nav.setCurrentRow(3)
    app.processEvents()
    conv.files.clear_all()
    conv.add_files([sources[0]])
    app.processEvents()
    mp3_index = conv.cb_container.findData(".mp3")
    check("转换页提供 MP3 纯音频输出", mp3_index >= 0)
    if mp3_index >= 0:
        conv.cb_container.setCurrentIndex(mp3_index)
        conv.refresh_now()
        app.processEvents()
        audio_cmd = conv.command_box.command_text()
        leftovers = [flag for flag in ("-c:v", "-vf", "-crf", "-b:v", "-preset", "-r ") if flag in audio_cmd]
        check("转 MP3 时命令不带视频参数", not leftovers, f"{leftovers}\n{audio_cmd[:300]}")
        check("转 MP3 时命令带 -vn", "-vn" in audio_cmd, audio_cmd[:200])
        specs = conv.build_specs()
        check("转 MP3 的任务 vcodec 为 none",
              bool(specs) and specs[0].params.get("vcodec") == "none",
              str(specs[0].params if specs else None))
        check("转 MP3 的任务画面参数已清零",
              bool(specs) and not specs[0].params.get("width") and not specs[0].params.get("fps"),
              str(specs[0].params if specs else None))

    # ---- 问题 2：MP4 容器不得提供 MP3 音轨 ----
    mp4_index = conv.cb_container.findData(".mp4")
    conv.cb_container.setCurrentIndex(mp4_index)
    conv.refresh_now()
    app.processEvents()
    acodecs = [conv.cb_acodec.itemData(i) for i in range(conv.cb_acodec.count())]
    check("MP4 容器的音轨选项不含 MP3", "mp3" not in acodecs, str(acodecs))
    check("MP4 容器默认音轨是 AAC", conv.cb_acodec.currentData() == "aac",
          str(conv.cb_acodec.currentData()))

    # ---- 问题 14：输出目录可跟随源文件目录 ----
    window.chk_follow_source.setChecked(True)
    conv.refresh_now()
    app.processEvents()
    src_dir = os.path.dirname(os.path.abspath(sources[0]))
    check("跟随源目录时输出落在源文件目录", src_dir in conv.command_box.command_text(),
          conv.command_box.command_text()[:200])

    # ---- 问题 10：滚轮不应误改下拉框 ----
    guard = install_wheel_guard(app)
    check("滚轮防护对象已创建", isinstance(guard, WheelGuard))
    combo = QComboBox()
    combo.addItems(["A", "B", "C"])
    combo.setCurrentIndex(0)
    combo.show()
    app.processEvents()
    wheel = QWheelEvent(
        QPointF(5, 5), QPointF(5, 5), QPoint(0, 0), QPoint(0, 120),
        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False,
    )
    app.sendEvent(combo, wheel)
    app.processEvents()
    check("未聚焦的下拉框忽略滚轮", combo.currentIndex() == 0, str(combo.currentIndex()))
    combo.deleteLater()

    # ---- 问题 4：未运行时应禁用取消按钮 ----
    check("未运行时取消按钮禁用", not window.btn_cancel.isEnabled())
    check("未运行时开始按钮可用（有文件且检查通过）", window.btn_start.isEnabled(),
          window.btn_start.toolTip())

    # ---- 开始按钮随参数输入即时刷新 ----
    props = window.pages[6]
    window.nav.setCurrentRow(6)
    app.processEvents()
    props.files.clear_all()
    props.add_files([sources[0]])
    app.processEvents()
    props.cb_mode.setCurrentIndex(0)   # 回到默认的"封面"模式
    props.edits["title"].clear()
    props.chk_remove_cover.setChecked(False)
    app.processEvents()
    check("属性页未填任何内容时禁止运行", not window.btn_start.isEnabled(),
          window.btn_start.toolTip())
    props.cb_mode.setCurrentIndex(1)
    props.edits["title"].setText("测试标题")
    app.processEvents()
    check("填写属性后开始按钮即时可用", window.btn_start.isEnabled(),
          window.btn_start.toolTip())
    props.files.clear_all()
    app.processEvents()
    check("清空文件后开始按钮禁用", not window.btn_start.isEnabled())

    trim_page = window.pages[0]
    window.nav.setCurrentRow(0)
    trim_page.files.clear_all()
    trim_page.add_files([sources[0]])
    app.processEvents()
    trim_page.ed_start.setText("不是时间")
    app.processEvents()
    check("时间非法时开始按钮禁用", not window.btn_start.isEnabled(),
          window.btn_start.toolTip())
    trim_page.ed_start.setText("00:00:10")
    trim_page.ed_end.setText("00:00:30")
    app.processEvents()
    check("时间改对后开始按钮恢复可用", window.btn_start.isEnabled(),
          window.btn_start.toolTip())

    # ---- 问题 5：合并页一致性检测未完成时禁止运行 ----
    merge = window.pages[1]
    window.nav.setCurrentRow(1)
    merge.files.clear_all()
    merge.add_files(sources)
    app.processEvents()
    merge._checked = False
    blocked, reason = merge.can_run()
    check("合并页检测未完成时禁止运行", not blocked, reason)
    merge._checked = True
    allowed, _ = merge.can_run()
    check("合并页检测通过后允许运行", allowed)
    merge.files.clear_all()
    app.processEvents()

    # ---- 问题 3：清空文件后进度 / 结果 / 日志一起复位 ----
    window.nav.setCurrentRow(2)
    app.processEvents()
    window._show_result("✔ 全部完成：成功 3 个", True)
    window.progress.setValue(75)
    window.log("清空前的残留日志")
    check("结果横幅可显示", window.lbl_result.isVisible())
    conv.files.clear_all()
    app.processEvents()
    check("清空文件后结果横幅复位", not window.lbl_result.isVisible())
    check("清空文件后进度复位", window.progress.value() == 0, str(window.progress.value()))
    check("清空文件后状态文案复位", window.lbl_status.text() == "就绪", window.lbl_status.text())
    check("清空文件后日志清空", "清空前的残留日志" not in window.log_panel.text())

    # ---- 问题 9：结果横幅样式随成功/失败切换 ----
    window._show_result("✖ 有 1 个失败", False)
    check("失败时显示查看错误按钮", window.btn_view_errors.isVisible())
    window._show_result("✔ 全部完成", True)
    check("成功时隐藏查看错误按钮", not window.btn_view_errors.isVisible())
    check("成功时提供打开输出目录入口", window.btn_open_output.isVisible())
    window._reset_run_display()

    # ---- 问题 20：日志按钮显示行数 ----
    window.log("第一行")
    window.log("第二行")
    app.processEvents()
    check("日志按钮显示行数", "行" in window.log_panel.toggle.text(),
          window.log_panel.toggle.text())

    # ---- 新增 4：文件信息支持复制 ----
    conv.files.clear_all()
    conv.add_files([sources[0]])
    app.processEvents()
    check("信息面板支持复制全部", hasattr(conv.info_panel, "copy_all"))
    check("信息面板支持复制选中项", hasattr(conv.info_panel, "_copy_selected"))

    # ---- 问题 7：预览区在未播放时即可拖动进度条 ----
    trim = window.pages[0]
    window.nav.setCurrentRow(0)
    trim.files.clear_all()
    trim.add_files([sources[0]])
    app.processEvents()
    check("预览组件存在", trim.preview is not None)
    if trim.preview is not None:
        check("未播放也能拖进度条（按已知时长铺满）",
              trim.preview.slider.maximum() > 0,
              f"max={trim.preview.slider.maximum()}")

    # ---- 回归：切换页面/模式不得产生多余的顶层窗口（属性页黑弹窗 bug）----
    import sys as _sys

    def _top_windows():
        return {id(w) for w in QApplication.topLevelWidgets() if w.isVisible() and w is not window}

    props.cb_mode.setCurrentIndex(0)
    app.processEvents()
    baseline = _top_windows()
    stray = []
    for mode_index in (1, 0):
        props.cb_mode.setCurrentIndex(mode_index)
        app.processEvents()
        for page in window.pages:
            page.files.clear_all()
            page.add_files([sources[0]])
            app.processEvents()
        grown = _top_windows() - baseline
        stray.extend(f"mode{mode_index}:{type(w).__name__}" for w in
                     [w for w in QApplication.topLevelWidgets() if id(w) in grown])
    check("切换模式/页面不产生多余顶层窗口", not stray, str(stray[:3]))

    # ---- 主题切换 + 截图 ----
    window.apply_theme("light")
    app.processEvents()
    window.nav.setCurrentRow(0)
    app.processEvents()
    shot = os.path.join(out_dir, "7_剪辑_light.png")
    window.grab().save(shot)
    check("浅色模式截图已生成", os.path.isfile(shot), shot)
    window.nav.setCurrentRow(2)
    app.processEvents()
    window.grab().save(os.path.join(out_dir, "8_转换_light.png"))
    check("浅色模式转换页截图已生成",
          os.path.isfile(os.path.join(out_dir, "8_转换_light.png")))
    window.apply_theme("dark")
    app.processEvents()

    # ---- 参数联动：改模式后命令应变化 ----
    trim.cb_mode.setCurrentIndex(0)  # 无损切割
    trim.refresh_now()
    app.processEvents()
    copy_command = trim.command_box.command_text()
    check("无损切割命令使用 -c copy", "-c copy" in copy_command, copy_command[:200])

    trim.cb_mode.setCurrentIndex(3)  # 智能切割
    trim.ed_start.setText("00:00:18")
    trim.ed_end.setText("00:00:32")
    trim.refresh_now()
    app.processEvents()
    smart_command = trim.command_box.command_text()
    check("智能切割命令包含 concat 拼接", "concat" in smart_command, smart_command[:300])
    check("智能切割命令包含无损复制段", "-c copy" in smart_command)

    # ---- 日志区 ----
    window.nav.setCurrentRow(0)
    app.processEvents()
    window.log("冒烟测试日志行")
    check("日志区可写入", "冒烟测试日志行" in window.log_panel.text())

    # ---- 命令预览框：默认折叠（省纵向空间）→ 展开 → 只读/可编辑/降级提示 ----
    box = trim.command_box
    check("命令框默认折叠", not box.expanded)
    check("折叠时命令文本仍在（仅视觉隐藏）", bool(box.command_text().strip()))
    box.set_expanded(True)
    app.processEvents()
    check("命令框展开后可见", box.view.isVisibleTo(box))
    check("命令框默认只读", box.view.isReadOnly())
    box.chk_edit.setChecked(True)
    box.view.setPlainText("ffmpeg -i in.mp4 out.mp4")
    app.processEvents()
    check("勾选后可编辑", not box.view.isReadOnly())
    check("编辑后标记为脏（触发降级）", box.is_edited, str(box.is_edited))
    check("降级提示可见", box.warn.isVisible())
    box.chk_edit.setChecked(False)
    app.processEvents()
    check("取消编辑后恢复生成命令", not box.is_edited and "ffmpeg" in box.command_text())

    # ---- 输出目录切换（选择目录时自动取消"跟随源目录"，与界面行为一致）----
    new_dir = os.path.join(work, "out2")
    os.makedirs(new_dir, exist_ok=True)
    window.chk_follow_source.setChecked(False)
    ctx.output_dir = new_dir
    for page in window.pages:
        page.on_output_dir_changed()
    app.processEvents()
    trim.refresh_now()
    app.processEvents()
    check("输出目录切换后命令同步更新", new_dir in trim.command_box.command_text(),
          trim.command_box.command_text()[:200])

    # ==================================================================
    # 第三轮实测反馈（2026-10-15）
    # ==================================================================

    # ---- 问题 1：属性页选完封面后"开始处理"必须立刻可点 ----
    props = window.pages[6]
    window.nav.setCurrentRow(6)
    app.processEvents()
    props.files.clear_all()
    props.add_files([sources[0]])
    app.processEvents()
    props.cb_mode.setCurrentIndex(0)          # 封面模式
    props.edits["title"].clear()
    props.chk_remove_cover.setChecked(False)
    props.ed_cover.setText("")
    app.processEvents()
    check("属性页未选封面时禁止运行", not window.btn_start.isEnabled(),
          window.btn_start.toolTip())
    props.ed_cover.setText(cover_src)          # 模拟"选择图片…"的结果
    app.processEvents()
    check("选完封面后开始按钮立刻可用（问题 1）", window.btn_start.isEnabled(),
          window.btn_start.toolTip())
    props.ed_cover.setText("")
    app.processEvents()
    check("清空封面后开始按钮重新禁用", not window.btn_start.isEnabled())

    # ---- 问题 3：加入文件自动带出属性 ----
    props.cb_mode.setCurrentIndex(1)          # 元数据模式
    props._clear_fields()
    tagged = infos[sources[0]]
    tagged.tags = {"title": "自动带出的标题", "artist": "某歌手"}
    props.files.clear_all()
    props.add_files([sources[0]])
    app.processEvents()
    check("加入文件自动带出标题", props.edits["title"].text() == "自动带出的标题",
          props.edits["title"].text())
    check("加入文件自动带出艺术家", props.edits["artist"].text() == "某歌手",
          props.edits["artist"].text())
    check("带出属性后开始按钮可用", window.btn_start.isEnabled(),
          window.btn_start.toolTip())
    props.files.clear_all()
    app.processEvents()

    # ---- 问题 3：没有内嵌封面时如实说明，不假装有 ----
    props.add_files([sources[0]])
    app.processEvents()
    check("无内嵌封面时给出明确文案",
          "没有内嵌封面" in props.lbl_thumb.text(), props.lbl_thumb.text())

    # ---- 问题 2：轨道页只收 1 个主文件 ----
    track = window.pages[2]
    window.nav.setCurrentRow(2)
    app.processEvents()
    check("轨道页声明只收 1 个主文件", track.MAX_FILES == 1, str(track.MAX_FILES))
    track.files.clear_all()
    added = track.add_files(sources)          # 传 3 个
    app.processEvents()
    check("轨道页超出的文件被拒收", len(track.paths()) == 1, str(len(track.paths())))
    check("轨道页告知用户忽略了额外文件",
          "已忽略额外的 2 个" in window.log_panel.text()
          or "已忽略额外的 2 个" in track.lbl_hint.text(),
          window.log_panel.text()[-160:])
    track.files.clear_all()
    app.processEvents()

    # ---- 问题 4：提取页封面选项与命令联动 ----
    extract = window.pages[4]
    window.nav.setCurrentRow(4)
    app.processEvents()
    extract.files.clear_all()
    extract.add_files([sources[0], sources[1]])   # 批量：两个文件
    app.processEvents()
    check("提取页提供「取源视频开头画面」",
          extract.cb_cover.findData("start") >= 0)
    check("提取页提供「取自定义时间点」",
          extract.cb_cover.findData("custom") >= 0)
    extract.cb_cover.setCurrentIndex(extract.cb_cover.findData("start"))
    extract.refresh_now()
    app.processEvents()
    specs = extract.build_specs()
    check("选封面后每个文件各自生成任务", len(specs) == 2, str(len(specs)))
    check("批量任务逐个带上封面参数",
          all(s.params.get("cover_mode") == "start" for s in specs),
          str([s.params for s in specs]))
    cmd = extract.command_box.command_text()
    check("封面命令抽帧", "-frames:v" in cmd, cmd[:200])
    check("封面命令写 attached_pic", "attached_pic" in cmd, cmd[:240])
    extract.cb_cover.setCurrentIndex(extract.cb_cover.findData("custom"))
    app.processEvents()
    blocked, reason = extract.can_run()
    check("自定义封面时间点为空时禁止运行", not blocked, reason)
    extract.ed_cover_time.setText("00:00:07")
    app.processEvents()
    allowed, _ = extract.can_run()
    check("填好封面时间点后允许运行", allowed)
    specs = extract.build_specs()
    check("封面时间点进入任务参数",
          all(s.params.get("cover_time") == 7.0 for s in specs),
          str([s.params for s in specs]))
    extract.cb_cover.setCurrentIndex(extract.cb_cover.findData("none"))
    extract.refresh_now()
    app.processEvents()
    check("关闭封面后命令回到单步且不带图片",
          ".ffkit_accover" not in extract.command_box.command_text(),
          extract.command_box.command_text()[:200])
    extract.files.clear_all()
    app.processEvents()

    # ---- 问题 5：音频码率改为标准档位 + 人话说明 ----
    conv = window.pages[3]
    window.nav.setCurrentRow(3)
    app.processEvents()
    conv.files.clear_all()
    conv.add_files([sources[0]])
    app.processEvents()
    labels = [conv.sp_abitrate.cb_preset.itemText(i)
              for i in range(conv.sp_abitrate.cb_preset.count())]
    check("码率提供标准档位", any("128" in t for t in labels), str(labels[:3]))
    check("码率档位带用途说明", any("语音" in t or "标准" in t for t in labels), str(labels[:2]))
    check("码率提供自定义项",
          conv.sp_abitrate.cb_preset.findData("custom") >= 0)
    check("码率默认落在标准档位上",
          conv.sp_abitrate.cb_preset.currentData() in (96, 128, 160, 192, 256, 320),
          str(conv.sp_abitrate.cb_preset.currentData()))
    check("码率有体积估算说明", "MB" in conv.sp_abitrate.lbl_note.text(),
          conv.sp_abitrate.lbl_note.text())
    conv.sp_abitrate.set_value(128)
    conv.refresh_now()
    app.processEvents()
    check("选档位后命令使用该码率", "-b:a 128k" in conv.command_box.command_text(),
          conv.command_box.command_text()[:240])
    conv.sp_abitrate.set_value(320)
    conv.refresh_now()
    app.processEvents()
    check("切换档位后命令同步", "-b:a 320k" in conv.command_box.command_text(),
          conv.command_box.command_text()[:240])
    conv.sp_abitrate.set_value(192)

    # 选 FLAC 时码率没有意义 → 收起控件但说清原因
    flac_index = conv.cb_container.findData(".flac")
    if flac_index >= 0:
        conv.cb_container.setCurrentIndex(flac_index)
        app.processEvents()
        check("输出 FLAC 时收起码率控件",
              not conv.sp_abitrate.cb_preset.isVisibleTo(conv),
              conv.sp_abitrate.cb_preset.isVisibleTo(conv))
        check("输出 FLAC 时说明为什么不需要码率",
              "无损" in conv.sp_abitrate.lbl_note.text(),
              conv.sp_abitrate.lbl_note.text())
        conv.cb_container.setCurrentIndex(conv.cb_container.findData(".mp4"))
        app.processEvents()

    extract = window.pages[4]
    window.nav.setCurrentRow(4)
    app.processEvents()
    check("提取页也用标准码率档位",
          extract.sp_abitrate.cb_preset.currentData() in (96, 128, 160, 192, 256, 320),
          str(extract.sp_abitrate.cb_preset.currentData()))

    print("\n" + "=" * 60)
    if FAILED:
        print(f"GUI 冒烟测试：{PASSED} 项通过，{len(FAILED)} 项失败")
        for item in FAILED:
            print(f"  ✖ {item}")
        return 1
    print(f"GUI 冒烟测试：{PASSED} 项全部通过 ✔")
    print(f"截图目录：{out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
