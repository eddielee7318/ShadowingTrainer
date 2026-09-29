<p align="center">
  <img src="assets/shadowing_icon.png" alt="Shadowing Trainer" width="120">
</p>

<h1 align="center">Shadowing Trainer</h1>

<p align="center">
  面向英语听写与 Shadowing 练习的 Windows 桌面软件<br>
  视频、音频、字幕、逐词判定与错题导出，都在一个界面完成。
</p>

<p align="center">
  <a href="https://github.com/eddielee7318/ShadowingTrainer/releases/latest"><strong>下载最新版</strong></a>
  ·
  <a href="#使用方法">使用方法</a>
  ·
  <a href="#从源码运行">从源码运行</a>
</p>

## 主要功能

- 视频和音频按字幕逐句播放，到句尾自动暂停
- 支持单句重播、循环播放、连续播放、播放速度和字幕时间校准
- 拖动视频进度条时显示当前时间点的画面缩略图
- 单句循环会跨句保持，进入下一句后仍按相同模式循环
- 字幕可隐藏或显示，答案揭晓后可显示匹配到的中文译文
- 根据单词数量显示长短不一的下划线，并可开启首字母提示
- 输入后逐词判定，错误答案显示在词框下方
- 支持收起答案重新练习，并用 Enter 提交或进入下一句
- 可把不重要或未对白的句子标记为跳过，不计入正确率
- 可递归导入整个文件夹，并在右侧树形列表中选择不同剧集或音频
- 可导出 TXT 或 Word 练习报告
- 答案显示后可在原句中划词，右键使用“荧光笔”或“添加文字笔记”，并随报告一键导出

## 支持的文件

| 类型 | 格式 |
| --- | --- |
| 视频 | MP4、MKV、AVI、MOV、WMV、WebM 等常见格式 |
| 音频 | MP3、M4A、WAV、FLAC、AAC、OGG、Opus、WMA、AIFF、MKA 等 |
| 字幕 | SRT、ASS、SSA、VTT、LRC、TXT，以及视频内嵌文字字幕 |

程序会优先寻找同名外部字幕；存在可读取的内嵌英文字幕时，也可自动用于时间校准和分句。图片型字幕（例如部分 PGS）不能直接用于拼写判定。

## 使用方法

1. 从 [Releases](https://github.com/eddielee7318/ShadowingTrainer/releases/latest) 下载最新版 EXE。
2. 双击运行后，选择“导入整个文件夹”或“打开单个视频 / 音频”。
3. 选择对应媒体；程序会自动匹配外部字幕或尝试读取内嵌字幕。
4. 点击“播放本句”，根据下划线提示输入听到的内容。
5. 按 Enter 判词；显示结果后再次按 Enter 会进入下一句。
6. 需要复习时可收起答案重来，练习结束后可导出报告。
7. 答案显示后，在原句中拖动选择单词或短语；右键可做荧光标记或添加文字笔记。

> 当前发布包未进行商业代码签名。Windows 第一次运行时可能显示安全提示，请核对 Release 页面提供的 SHA-256 后再运行。

## 判词规则

- 普通英文单词不区分大小写，识别到的专有名词会检查大小写
- 直撇号和弯撇号等价；省略撇号也可通过，例如 `dont` / `don't`
- `OK` 与 `okay` 等常见写法按等价处理
- 阿拉伯数字、英文数词和单个中文数字按数值判定，例如 `3` / `three` / `三`
- 错词会提示少写、多写、替换、字母顺序或专有名词大小写问题

## 快捷键

| 快捷键 | 功能 |
| --- | --- |
| `Ctrl+O` | 打开媒体 |
| `Ctrl+R` | 重播本句 |
| `Alt+←` / `Alt+→` | 上一句 / 下一句 |
| `Ctrl+E` | 导出 TXT |

## 从源码运行

需要 Python 3.11 或更新版本：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

运行测试：

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

## 构建 Windows 单文件版

```powershell
python -m pip install -r requirements-dev.txt
pyinstaller --clean --noconfirm ShadowingTrainer.spec
```

生成的文件位于 `dist/ShadowingTrainer-v8.exe`。
