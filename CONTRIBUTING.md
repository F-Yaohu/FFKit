# 参与 FFKit

FFKit 是 **ffmpeg 的图形化外壳**，定位红线写在 `requirements_v2.md` 里，改动前请先读它。

## 环境

```bat
pip install -r requirements.txt
python main.py
```

`app/core/` 做了惰性导入，**不装 PySide6 也能跑核心层自检**，改核心逻辑时验证会快很多。

## 提交前必须跑测试

```bat
python selftest.py                      :: 核心层，零依赖，必须全绿
python uitest.py                        :: GUI 冒烟（offscreen），改了 UI 就跑
python realtest.py <ffmpeg目录>         :: 真实媒体全链路，改了 builder/参数必须跑
```

CI 会自动跑前两套（Windows / Ubuntu / macOS × Python 3.10 & 3.13）。

## 几条不能破的约定

1. **`CommandBuilder` 是全项目唯一生成 ffmpeg argv 的地方**。任何新功能都是加 Spec + 加 Builder，
   不要在 UI 层拼命令字符串。
2. 子进程一律传 **argv 列表**，禁止 `shell=True`、禁止字符串拼接。
3. 统一带 `-y -nostdin -progress pipe:1 -nostats`，输出先写 `.tmp` 再 `os.replace` 原子重命名。
4. concat 列表 **UTF-8 无 BOM**、单引号按 ffmpeg 规则转义、调用时带 `-safe 0`。
5. 区分**全局错误**（中断整个队列）与**单文件错误**（跳过并汇总），放进 `Validator`。

违反其中任何一条，批量任务就可能损坏用户的文件。

## 品牌资源

logo 的**唯一设计源文件是 `assets/logo.svg`**。改设计只改它，然后：

```bat
python tools/render_logo.py     :: 重新导出 PNG / ICO
```

不要用 Pillow 手绘复现 SVG 的圆角描边——`stroke-linejoin="round"` 是「边整体外扩 + 顶点圆弧」，
手绘会让圆凸出直边。

## 打包

```bat
python tools/build.py
```

**不要直接用 `pyinstaller FFKit.spec`**：项目路径含中文时，PyInstaller 的 `--icon` 会静默失效，
日志照样打印成功，实际写入的是它自带的默认图标。`tools/build.py` 会绕开这个坑并自动校验结果。