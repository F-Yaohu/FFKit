# FFKit — ffmpeg 的图形化外壳

<p align="center">
  <img src="assets/logo-horizontal.png" width="520" alt="FFKit">
</p>

> **ffmpeg 的图形化外壳**：本地、离线、无广告、轻量。  
> Python + PySide6 + 本机 ffmpeg/ffprobe，Windows 桌面应用。  
> 需求依据：`requirements_v2.md`（v2 最终版）。

---

## 快速开始

```bat
:: 1) 安装依赖（需要 Python 3.10+）
pip install -r requirements.txt

:: 2) 安装 ffmpeg（任选其一）
winget install Gyan.FFmpeg
:: 或 scoop install ffmpeg
:: 或到 https://www.gyan.dev/ffmpeg/builds/ 下载，把 bin 加入 PATH

:: 3) 运行
python main.py
```

- **没有 ffmpeg 也能打开程序**：顶栏会出现提示条，可以在「手动指定 ffmpeg…」或  
  「文件 → 首选项」里指定 `ffmpeg.exe` 路径（会记住，下次启动直接生效）。
- 建议先跑一遍自检：`python selftest.py`（不需要 ffmpeg 也能跑）。

---

## 功能一览

| 功能     | 说明                                                                                                        |
| ------ | --------------------------------------------------------------------------------------------------------- |
| **剪辑** | 无损切割（`-c copy`，提示关键帧精度代价）／精确切割／绝对精确／智能切割（首尾短段重编码 + 中间无损）。内置预览播放器，可一键填开始/结束时间、复制时间戳；播不了时给出「用系统默认播放器打开」降级按钮 |
| **合并** | 用 ffprobe 逐项比对编码参数；**一致 → concat demuxer 无损秒合；不一致 → 自动降级 concat filter 重编码**，UI 明确标注走哪条路。支持拖拽排序           |
| **轨道** | 多轨封装：视频 + 外部音频/字幕合并成单文件；内嵌轨道可勾选保留/丢弃，可挂多条音轨/字幕并设置语言、标题、默认/强制；视频与兼容音轨**无损 copy**，MP4 不收的编码（FLAC/srt 等）自动转 mov_text/AAC 并提示 |
| **转换** | 视频转 mp4/mkv，音频转 mp3/wav/m4a/flac；可改分辨率、帧率、码率、编码器；附带旋转/翻转与音量调整                                             |
| **提取** | 提取音频（m4a/aac 源为 AAC 时**无损 copy 秒级完成**）、去音频（`-an -c:v copy`）、提取某一帧为图片（默认取预览时间，否则时长 10%；支持秒数与百分比两种输入）       |
| **压缩** | x264/x265 三档预设（CRF 20/23/26、24/28/32）；可选「目标体积 MB」两遍编码反推码率；**压缩前显示源码率**（码率偏低会提示不建议压缩），**完成后显示前后体积对比**      |
| **属性** | 设置/更换封面图（含预览，支持移除已有封面）、编辑文件属性（标题/艺术家/专辑/年份/备注等，留空不改、可勾选"留空即清除"） |

通用能力：批量处理（失败跳过 + 汇总清单）、全局错误立即中断、进度条（`-progress pipe:1`）、  
任务取消（不留半个损坏文件）、命令预览框（只读 P1 / 可编辑 P3）、文件信息面板（支持复制单项/全部）、  
日志区（默认折叠、限行数、显示行数、可导出）、深浅色主题、输出目录记忆（可跟随源文件目录）、  
输出冲突自动加序号、结果横幅 + 完成托盘提示、未聚焦的下拉框/数字框不响应滚轮（防误触）。

### 第二轮实测反馈修复（重点）

- **容器 ↔ 音频编码联动**：MP4 里塞 MP3、WAV 里塞 AAC 这类播放器认不出的组合被彻底禁止——
  UI 下拉选项与 `CommandBuilder` 校验共用 `paths.py` 里的同一张表，"界面上能选的"和"命令里真正生成的"永远一致；
- 转换页参数分组（输出格式 / 音频 / 画面），选纯音频容器时整个"画面"组隐藏、命令绝不带视频参数；
- 清空文件列表后进度 / 结果横幅 / 日志一起复位，不留"全部完成"的残留；
- 未运行时取消按钮禁用；合并页一致性检测未完成前"开始处理"不可用；开始按钮随参数输入即时刷新；
- 文件列表支持滚动 / 右键菜单（移除 / 清空 / 打开所在目录 / 复制路径）/ 数量与总体积；
- 预览播放器无需先播放即可拖进度条、支持跳转到指定时间、音量调节、等比缩放；
- 命令预览不再暴露 `.tmp` 临时后缀，展示的是最终输出路径；
- 合并页信息区改为汇总视图（文件数 / 合计时长 / 体积 / 统一画布 / 差异明细）；
- 参数区可滚动、左右分栏可拖动（QSplitter）、参数分组标题 + 分隔线；
- **新增**：属性页（封面 + 元数据）、文件信息单项复制与一键复制。

### 第三轮实测反馈修复

- **属性页黑色空弹窗**：切换"操作类型"时会冒出一个空白窗口——原因是占位用的
  `QLabel("")` 没有父窗口也没进布局，`setVisible(True)` 就成了独立顶层窗口。已改为 `None` 占位，
  并加了"切换模式/页面不得产生多余顶层窗口"的回归测试；
- **小窗口布局**：命令预览默认**折叠成一行**（点标题展开，省约 100px 纵向空间）；
  预览播放器控制按钮压缩文案、画面区改为完全弹性（空间大时画面自动变大）；
  页面宽度放不下两栏时**自动改为上下堆叠**，任何窗口尺寸都不再互相遮挡；
- **预览画面更大**：右栏改为「预览 / 参数」垂直分割器，默认给画面更多空间，可手动拖动；
- **新增「轨道」页**（新增 3 落地）：见功能表。

### 关于 QProcess 的环境兼容

个别受限环境（沙箱 / 企业安全策略 / 某些杀软）会禁止 Qt 通过管道拉起子进程，
`QProcess` 直接 `FailedToStart`。`Runner` 会**自动检测并降级**为"文件重定向 + 轮询"模式，
进度解析、取消、临时文件清理等行为完全不变，只是进度刷新延迟约 0.1s。

---

## 运行架构

```
OperationSpec ──► Validator ──► CommandBuilder ──► Runner(QProcess) ──► CommandPreview
  (做什么)       (能不能做)      (唯一 argv 出口)     (异步+进度+取消)
```

| 模块    | 位置                      | 职责                                                   |
| ----- | ----------------------- | ---------------------------------------------------- |
| 配置持久化 | `app/config.py`         | JSON，`%APPDATA%/FFKit/config.json`                  |
| 工具链定位 | `app/ffmpeg_locator.py` | PATH → 用户指定 → 常见目录；版本检测（< 4.0 提示升级）                  |
| 元信息解析 | `app/core/prober.py`    | ffprobe JSON 解析 + 缓存 + 编码一致性比对 + 关键帧扫描               |
| 校验层   | `app/core/validator.py` | 区分**全局错误**（中断队列）与**单文件错误**（跳过），磁盘空间预检                |
| 命令构建  | `app/core/builder.py`   | **全项目唯一生成 argv 的地方**；所有 `-y -nostdin -progress` 收敛于此 |
| 异步执行  | `app/core/runner.py`    | QProcess 信号驱动、进度解析、terminate→kill、临时文件清理             |
| 批量队列  | `app/core/queue.py`     | 顺序执行、失败策略、结束汇总                                       |
| 界面    | `app/ui/**`             | 主窗口 + 5 个功能页 + 通用组件                                  |

### 关键约定（对应需求第五节）

- 子进程一律 argv 列表，绝不 `shell=True`、绝不拼字符串；
- 一律 `-y -nostdin -progress pipe:1 -nostats`，stderr 只做日志；
- 输出先写 `xxx.mp4.tmp`（显式 `-f` 指定 muxer），成功后 `os.replace` 原子重命名；
- concat 列表 UTF-8 **无 BOM**、单引号转义 `'` → `'\''`、调用时 `-safe 0`；
- stderr 按 locale 解码 + `errors='replace'`，中文系统 GBK 输出不会炸；
- 时长统一用 `-t` 而非 `-to`；时间输入支持 `00:01:23 / 01:23 / 83`；
- 拖拽语义：拖到**合并页** = 加入可排序列表；拖到**其他页** = 加入批量队列；
- 命令被手动编辑后执行 → 降级：无进度条、不校验输出路径、只显示 stderr 尾部。

---

## 测试

```bat
python selftest.py   :: 核心层 161 项断言（无需 ffmpeg / 无需 GUI）
python uitest.py     :: GUI 冒烟 114 项（需要 PySide6，可用 QT_QPA_PLATFORM=offscreen）
python realtest.py <ffmpeg目录>   :: 真实媒体全链路 37 项（需要本机 ffmpeg，会真正执行转码/封面/元数据/多轨封装）
```

`uitest.py` 会在 `screenshots/` 下生成各功能页深/浅色截图。

---

## 打包（可选）

```bat
pip install pyinstaller
python tools/build.py
```

产物在 `dist/FFKit/FFKit.exe`。

> **为什么用 `tools/build.py` 而不是直接 `pyinstaller FFKit.spec`？**
> PyInstaller 有个坑：`--icon` 指向的 ico 路径**含非 ASCII 字符（例如中文目录）时会静默失败**——
> 日志照样打印 `Copying icon to EXE`，实际写入的却是它自带的默认图标，很难察觉。
> `tools/build.py` 会先把 `logo.ico` 复制到 ASCII 临时目录再传给 `--icon`，
> 并在打包结束后用 Win32 API 读回 exe 的图标像素做校验，确保图标真的是 FFKit 的。
>
> 若你的项目路径是纯 ASCII，也可以直接用：
> ```bat
> pyinstaller --noconfirm --windowed --name FFKit --icon "assets\logo.ico" ^
>   --add-data "app;app" --add-data "assets;assets" main.py
> ```
> 或 `pyinstaller --noconfirm FFKit.spec`（spec 已含 assets 与图标）。

> 打包后 exe 仍依赖本机 ffmpeg（符合"ffmpeg 图形化外壳"的定位，不内置分发 ffmpeg）。
> `assets/` 必须随程序分发，否则窗口/任务栏/托盘图标会变成 Qt 默认图标；
> `app/resources.py` 会自动识别源码运行与 `sys._MEIPASS` 打包环境。

图标重生：只改 `assets/logo.svg`，然后 `python tools/render_logo.py` 重新导出 PNG/ICO。

---

## 目录结构

```
FFKit/
├── main.py                  # 入口
├── selftest.py              # 核心层自检
├── uitest.py                # GUI 冒烟测试
├── realtest.py              # 真实媒体全链路测试
├── requirements.txt
├── requirements_v2.md       # 需求文档（本实现的依据）
├── FFKit.spec               # PyInstaller 打包配置（含 assets 与图标）
├── assets/                  # logo 与图标（需随程序分发）
│   ├── logo.svg             #   正方形矢量版（512×512，唯一设计源文件）
│   ├── logo.png             #   正方形位图版
│   ├── logo-horizontal.svg  #   横版矢量
│   ├── logo-horizontal.png  #   横版位图
│   └── logo.ico             #   Windows 图标（16~256 多尺寸）
├── tools/
│   ├── render_logo.py       # 从 SVG 重新导出 PNG/ICO
│   └── build.py             # 一键打包（修正中文路径下 exe 图标失效）
├── app/
│   ├── resources.py         # 资源定位与图标加载（兼容源码/打包两种环境）
│   ├── config.py            # 配置持久化
│   ├── ffmpeg_locator.py    # ffmpeg/ffprobe 定位与版本检测
│   ├── core/                # 核心层（与 UI 解耦，可独立测试）
│   │   ├── spec.py          #   OperationSpec / Step / Job
│   │   ├── prober.py        #   ffprobe 元信息解析
│   │   ├── validator.py     #   校验层
│   │   ├── builder.py       #   CommandBuilder（唯一 argv 出口）
│   │   ├── runner.py        #   QProcess 异步执行
│   │   ├── queue.py         #   批量队列
│   │   ├── paths.py         #   路径/临时文件/concat 转义/命令渲染
│   │   ├── timeparse.py     #   时间解析与格式化
│   │   └── proc_util.py     #   子进程封装（隐藏控制台、编码解码）
│   └── ui/
│       ├── main_window.py   # 主窗口（导航/底部控制条/拖拽路由/队列接线）
│       ├── context.py       # AppContext（配置+工具链+各层打包）
│       ├── dialogs.py       # 首选项对话框
│       ├── theme.py         # 深浅色 QSS
│       ├── widgets/         # 文件列表/信息面板/日志/命令框/预览播放器
│       └── pages/           # 剪辑/合并/轨道/转换/提取/压缩/属性
└── screenshots/             # uitest 生成的界面截图
```

---

## 待办（按需求优先级，已预留接口）

- **P2 硬件编码**：`CommandBuilder.video_args()` 已支持 `hw_encoder`（nvenc/qsv/amf 三套参数体系），  
  配置项 `hw_encoder` 已就位，只差 UI 开关与实测。
- **P3 命令编辑后执行**：已实现（含降级逻辑与多步命令拦截）。
- 高分屏（DPI）适配的进一步打磨（当前使用系统默认 PassThrough 策略）。
