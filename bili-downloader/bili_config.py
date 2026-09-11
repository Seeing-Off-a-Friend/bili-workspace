# -*- coding: utf-8 -*-
"""全局配置 —— 全项目唯一的"可变参数"集中地。

想改请求头 / Cookie / 默认保存路径 / 清晰度，只改这个文件即可，
命令行(main.py) 和网页(web_app.py) 都会自动生效。
所有常量都可以用环境变量覆盖（不改文件也能临时换）。
"""

import os

# ------------------------- 请求头 -------------------------
# 模拟浏览器。Cookie 里最关键的是 SESSDATA（登录态）与 bili_jct。
USER_AGENT = os.environ.get(
    "BILI_UA",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36",
)

# Cookie 默认留空 —— 真凭据不要写进代码，更不要提交到仓库。
# 一旦推到 GitHub，自动化爬虫几分钟内就会抓走。
# 推荐用法：跑 python bili_login.py 自动登录，凭据存到 bili_session.json（已被 .gitignore 忽略）。
# 临时覆盖：set BILI_COOKIE=xxx
COOKIE = os.environ.get("BILI_COOKIE", "")

REFERER = os.environ.get("BILI_REFERER", "https://www.bilibili.com/")

# ------------------------- 会话文件（自动录入的登录态）-------------------------
# 运行 `python bili_login.py` 会弹出浏览器让你登录，然后自动把 Cookie + UA 存到这里。
# 文件优先级高于上面硬编码的 COOKIE，所以手抄的 Cookie 过期了也不影响。
# 该文件含有账号凭据，已被 .gitignore 忽略，不会进版本库。
SESSION_FILE = os.environ.get(
    "BILI_SESSION_FILE",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "bili_session.json"),
)


def load_session(path=None):
    """读取自动录入的会话文件；不存在或损坏时返回空字典（不抛异常）。"""
    import json
    try:
        with open(path or SESSION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_session(cookie, user_agent=None, uname=None, vip=None, path=None,
                 source="browser", extra=None):
    """把登录态写入会话文件，返回写入路径。"""
    import json
    import time as _time
    data = {
        "cookie": cookie,
        "user_agent": user_agent,
        "uname": uname,
        "vip": vip,
        "source": source,
        "saved_at": _time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if extra:
        data.update(extra)
    target = path or SESSION_FILE
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return target


def session_summary(path=None):
    """把会话文件摘要成人话，用于日志和自检。没有文件时返回 None。"""
    data = load_session(path)
    if not data.get("cookie"):
        return None
    who = data.get("uname") or "未知账号"
    vip = "大会员" if data.get("vip") else "普通/未确认"
    return f"{who}（{vip}），保存于 {data.get('saved_at') or '未知时间'}"


# ------------------------- 默认行为 -------------------------
# 命令行不传参数时用的默认值
SEARCH_KEYWORD_DEFAULT = os.environ.get("BILI_KEYWORD", "无极魔尊")
DEFAULT_SAVE_PATH = os.environ.get("BILI_SAVE_PATH", r"F:\用户\视频")

# 请求清晰度：80=1080P, 64=720P, 32=480P, 16=360P
# 实际能拿到多高取决于 Cookie 的登录态/大会员状态，接口会自动降级。
QUALITY = int(os.environ.get("BILI_QUALITY", "80"))

# 网络超时（秒）
TIMEOUT = int(os.environ.get("BILI_TIMEOUT", "20"))

# ------------------------- 下载完之后干什么 -------------------------
def _flag(name, default=False):
    """读一个"开关型"环境变量：1/true/yes/on 都算开。"""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on", "y")

# 下载完成后自动用播放器打开第一个视频（等价于命令行 --play）
AUTO_PLAY = _flag("BILI_AUTO_PLAY", False)
# 下载完成后自动打开保存目录（等价于命令行 --open-folder）
AUTO_OPEN_FOLDER = _flag("BILI_AUTO_OPEN_FOLDER", False)
# 指定播放器路径，例如 "C:\\Program Files\\DAUM\\PotPlayer\\PotPlayerMini64.exe"
# 留空则用系统默认关联程序
PLAYER = os.environ.get("BILI_PLAYER", "")


def headers(cookie=None, user_agent=None, use_session=True):
    """构造一份 requests 用的请求头。

    取值优先级（越靠前越优先）：
        1. 函数参数（显式传入）
        2. 环境变量 BILI_COOKIE / BILI_UA
        3. bili_session.json（`python bili_login.py` 自动录入的）
        4. 本文件里的 COOKIE / USER_AGENT 默认值
           （COOKIE 现为空 —— 凭据不写死在源码里，否则会随仓库泄露）
    """
    session = load_session() if use_session else {}
    chosen_cookie = (cookie
                     or os.environ.get("BILI_COOKIE")
                     or session.get("cookie")
                     or COOKIE)
    chosen_ua = (user_agent
                 or os.environ.get("BILI_UA")
                 or session.get("user_agent")
                 or USER_AGENT)
    return {
        "user-agent": chosen_ua,
        "cookie": chosen_cookie,
        "referer": REFERER,
    }
