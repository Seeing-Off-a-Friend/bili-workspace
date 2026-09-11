# -*- coding: utf-8 -*-
"""用系统默认程序打开文件 / 播放视频 / 定位到文件夹 —— "下载完直接看"用。

给三处调用：
    · 命令行  python main.py --play / --open-folder
    · 网页版  结果列表上的「播放」「打开所在文件夹」按钮
    · 配置    BILI_AUTO_PLAY=1 之类（见 bili_config.py）

安全约定：只接受白名单扩展名、且必须真实存在的文件，避免这个模块变成
"随便给个路径就执行"的入口。网页版的路径也一律从服务端任务记录里取，不由前端传。
"""

import os
import subprocess
import sys

# 能当视频/音频打开的类型
PLAYABLE_EXTS = {
    ".mp4", ".mkv", ".flv", ".mov", ".avi", ".webm", ".ts", ".m4s",
    ".mp3", ".m4a", ".wav", ".aac", ".flac",
}

# 允许"打开所在文件夹"的类型（比可播放的多几种附属文件）
OPENABLE_EXTS = PLAYABLE_EXTS | {".ass", ".srt", ".jpg", ".png", ".txt", ".json"}


class OpenError(RuntimeError):
    """路径不合法、文件不存在、类型不允许时抛这个，调用方好区分处理。"""


# ------------------------- 真正启动进程的地方（测试会替换这两个）-------------------------

def _startfile(path):
    if sys.platform.startswith("win"):
        os.startfile(path)                       # noqa: S606  仅 Windows 有
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _spawn(args):
    return subprocess.Popen(args)


# ------------------------- 路径校验 -------------------------

def ensure_exists(path):
    """路径必须真实存在，返回绝对路径。"""
    if not path or not str(path).strip():
        raise OpenError("没有文件路径")
    full = os.path.abspath(str(path))
    if not os.path.exists(full):
        raise OpenError(f"路径不存在：{full}")
    return full


def ensure_playable(path):
    """必须是存在的、白名单里的音视频文件。"""
    full = ensure_exists(path)
    if not os.path.isfile(full):
        raise OpenError(f"不是一个文件：{full}")
    ext = os.path.splitext(full)[1].lower()
    if ext not in PLAYABLE_EXTS:
        raise OpenError(f"这个类型不当作视频打开：{ext or '（无扩展名）'}")
    return full


# ------------------------- 对外功能 -------------------------

def play(path, player=None):
    """用播放器打开。

    player 为空 → 交给系统默认关联程序（双击那个文件的效果）；
    player 给了路径 → 用指定播放器打开，例如 --player "C:\\...\\PotPlayerMini64.exe"。
    返回实际打开的文件路径。
    """
    full = ensure_playable(path)
    if player:
        if not os.path.exists(player):
            raise OpenError(f"找不到播放器：{player}")
        _spawn([player, full])
    else:
        _startfile(full)
    return full


def open_folder(path, select=True):
    """在文件管理器里打开所在目录；select=True 时顺带选中该文件。返回打开的目录。"""
    full = ensure_exists(path)
    if os.path.isfile(full):
        folder, want_select = os.path.dirname(full), select
    else:
        folder, want_select = full, False

    if sys.platform.startswith("win"):
        if want_select:
            # /select, 后面紧跟路径；explorer 的退出码通常是 1，属正常现象
            _spawn(["explorer", "/select," + full])
        else:
            _startfile(folder)
    elif sys.platform == "darwin":
        _spawn(["open", "-R", full] if want_select else ["open", folder])
    else:
        _spawn(["xdg-open", folder])
    return folder


def play_and_report(path, player=None, log=print):
    """带日志的播放：失败只记一行，不往上抛（下载已经成功了，别因为打不开而报错）。"""
    try:
        full = play(path, player=player)
        log(f"[播放] 已交给播放器：{os.path.basename(full)}")
        return full
    except Exception as e:
        log(f"[播放] 打开失败：{e}")
        return None


def open_folder_and_report(path, log=print):
    """带日志的打开目录，同样不抛异常。"""
    try:
        folder = open_folder(path)
        log(f"[打开] 已打开目录：{folder}")
        return folder
    except Exception as e:
        log(f"[打开] 打开目录失败：{e}")
        return None
