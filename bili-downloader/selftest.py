# -*- coding: utf-8 -*-
"""重构后自检脚本 —— 默认全程离线（用假 session，不联网、不下大文件）。

用法：
    python selftest.py            # 离线自检（用假网络，不联网、不下大文件）
    python selftest.py --live     # 额外做一次真实接口验证（只取地址，不下载视频）
    python selftest.py --download # 额外真下一个短视频，验证合并（下完自动删）
    python selftest.py --browser  # 额外真开一次浏览器，验证搜索步骤（会弹出 Chrome）
"""

import os
import sys
import tempfile
import time

import bili_core as core

PASS = []
FAIL = []


def check(cond, msg):
    if cond:
        PASS.append(msg)
        print("  [OK]", msg)
    else:
        FAIL.append(msg)
        print("  [FAIL]", msg)


def section(title):
    print(f"\n[{title}]")


# ============================== 假的网络层 ==============================

class FakeResponse:
    def __init__(self, json_data=None, text="", headers=None, chunks=None):
        self._json = json_data
        self.text = text or ""
        self.headers = headers or {}
        self._chunks = chunks or []

    def json(self):
        return self._json

    def raise_for_status(self):
        return None

    def iter_content(self, size):
        for c in self._chunks:
            yield c

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """按 URL 片段路由的假 requests。routes: [(片段, FakeResponse | Exception)]"""

    def __init__(self, routes=(), default=None):
        self.routes = list(routes)
        self.default = default
        self.calls = []
        self.params = []

    def get(self, url, **kw):
        self.calls.append(url)
        self.params.append(kw.get("params") or {})
        for frag, resp in self.routes:
            if frag in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        if self.default is not None:
            return self.default
        raise AssertionError("未预料的请求：" + url)


class FakeCookies(list):
    """模仿 DrissionPage 的 CookiesList（有 as_str()）。"""

    def as_str(self):
        return "; ".join(f"{c['name']}={c['value']}" for c in self)


class FakeCard:
    def __init__(self, href):
        self._href = href

    def attr(self, name):
        return self._href if name == "href" else None


class FakeBrowser:
    """够用的假浏览器：search_videos / cookies_from_page / bili_login 用到的方法都在。

    jars 是一个"每次轮询返回哪一罐 Cookie"的列表：最后一项会一直重复，
    用来模拟"先没登录、用户登录后才出现 SESSDATA"的过程。
    """

    def __init__(self, cards=(), jars=(), ua="Mozilla/5.0 (Fake) Chrome/999.0",
                 raise_cookies=False):
        self.cards = list(cards)
        self.jars = [list(j) for j in jars] or [[]]
        self.ua = ua
        self.raise_cookies = raise_cookies
        self.polls = 0
        self.visited = None
        self.quit_called = False
        self.inputs = []

    # --- 搜索相关 ---
    def get(self, url):
        self.visited = url

    def ele(self, xpath):
        return self

    def input(self, text):
        self.inputs.append(text)

    def click(self):
        pass

    @property
    def latest_tab(self):
        return self

    def eles(self, xpath):
        if "search-condition-row" in xpath:
            return [self, self]              # 两个筛选按钮，代码取 [1]
        return [FakeCard(h) for h in self.cards]

    # --- Cookie / UA 相关 ---
    def cookies(self, all_domains=False):
        if self.raise_cookies:
            raise RuntimeError("取不到 cookie")
        jar = self.jars[min(self.polls, len(self.jars) - 1)]
        self.polls += 1
        return FakeCookies(jar)

    def run_js(self, script):
        if self.ua is None:
            raise RuntimeError("no js")
        return self.ua

    def quit(self):
        self.quit_called = True


class FakeClock:
    """配合 fake_sleep 用，避免测试真的等几十秒。"""

    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def silent(*args, **kwargs):
    """安静版 log 回调。"""
    return None


def dash_fixture():
    """模仿 playurl 接口返回：480P/1080P，HEVC+AVC，两档音频。"""
    return {
        "video": [
            {"id": 32, "width": 854, "height": 480, "bandwidth": 500_000, "codecs": "avc1.64001f",
             "baseUrl": "https://cdn.example.com/video_480_avc.m4s"},
            {"id": 80, "width": 1920, "height": 1080, "bandwidth": 2_500_000, "codecs": "hev1.1.6",
             "baseUrl": "https://cdn.example.com/video_1080_hevc.m4s"},
            {"id": 80, "width": 1920, "height": 1080, "bandwidth": 2_200_000, "codecs": "avc1.640032",
             "baseUrl": "https://cdn.example.com/video_1080_avc.m4s"},
        ],
        "audio": [
            {"id": 30216, "bandwidth": 64_000, "baseUrl": "https://cdn.example.com/audio_64.m4s"},
            {"id": 30280, "bandwidth": 320_000, "baseUrl": "https://cdn.example.com/audio_320.m4s"},
        ],
    }


# ============================== 各项测试 ==============================

def test_utils():
    section("1. 工具函数")
    check(core.absolute_url("//www.bilibili.com/video/BV1xx") == "https://www.bilibili.com/video/BV1xx",
          "协议相对地址 // 会被补成 https://")
    check(core.absolute_url("/video/BV1xx") == "https://www.bilibili.com/video/BV1xx",
          "站内绝对路径会补上域名")
    check(core.absolute_url("https://x/y") == "https://x/y", "完整地址保持不变")
    check(core.absolute_url("") == "", "空地址返回空串")

    check(core.parse_bvid("https://www.bilibili.com/video/BV1Bvbv6TEtT/?spm=1") == "BV1Bvbv6TEtT",
          "能从带参数的链接里取出 BV 号")
    check(core.parse_bvid("https://www.bilibili.com/") is None, "普通页面取不到 BV 号")

    bad = core.sanitize_filename('a/b\\c:d*e?f"g<h>i|j\nk')
    check(not any(ch in bad for ch in '\\/:*?"<>|\n\t'), "文件名里的非法字符被替换掉")
    check(core.sanitize_filename("   ") == "video", "空标题有兜底名字")
    check(len(core.sanitize_filename("很长的标题" * 50)) <= 80, "超长标题被截断到 80 字以内")

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "a.mp4")
        open(p, "w").close()
        check(core.unique_path(p).endswith("a(2).mp4"), "同名文件自动改名，不覆盖")


def test_html_parsing():
    section("2. 页面回退解析")
    html = ('{"baseUrl":"https://cdn.example.com/v.m4s","x":1}'
            '{"baseUrl":"https://cdn.example.com/a.m4s"}'
            '<title>某个视频_哔哩哔哩_bilibili</title>')
    urls = core.extract_media_from_html(html)
    check(urls == ["https://cdn.example.com/v.m4s", "https://cdn.example.com/a.m4s"],
          "正则能扒出全部 baseUrl")
    check(core.extract_title_from_html(html) == "某个视频", "能从 <title> 里清洗出标题")
    check(core.extract_media_from_html("") == [], "空页面返回空列表")


def test_pick_streams():
    section("3. 流选择策略")
    v, a = core.pick_streams(dash_fixture())
    check(v["height"] == 1080, "视频取最高清晰度（1080P）")
    check(v["codecs"].startswith("avc"), "同清晰度下优先 AVC 编码")
    check(a["bandwidth"] == 320_000, "音频取最高码率")
    check("1080" in core.describe_stream(v) and "AVC" in core.describe_stream(v),
          "清晰度描述可读")

    try:
        core.pick_streams({"video": [], "audio": []})
        check(False, "没有音视频流时应该报错")
    except RuntimeError as e:
        check("音视频流" in str(e), "没有音视频流时抛出明确错误")


def test_fetch_media_info_api():
    section("4. 解析：官方接口优先")
    s = FakeSession([
        ("/x/web-interface/view", FakeResponse(json_data={
            "code": 0, "data": {"title": "测试标题", "cid": 123}})),
        ("/x/player/playurl", FakeResponse(json_data={"code": 0, "data": {"dash": dash_fixture()}})),
    ])
    info = core.fetch_media_info("https://www.bilibili.com/video/BV1Bvbv6TEtT",
                                 session=s, log=lambda *a, **k: None)
    check(info["source"] == "api", "走的是接口路径")
    check(info["title"] == "测试标题" and info["cid"] == 123, "标题和 cid 取到了")
    check(info["video_url"].endswith("video_1080_avc.m4s"), "视频直链是最优流")
    check(info["audio_url"].endswith("audio_320.m4s"), "音频直链是最优流")


def test_fetch_media_info_fallback():
    section("5. 解析：接口挂了要能回退")
    s = FakeSession([
        ("/x/web-interface/view", RuntimeError("网络抖动")),
        ("www.bilibili.com/video/", FakeResponse(text=
            '{"baseUrl":"https://cdn.example.com/v.m4s"}'
            '{"baseUrl":"https://cdn.example.com/a.m4s"}'
            '<title>回退标题_bilibili</title>')),
    ])
    info = core.fetch_media_info("https://www.bilibili.com/video/BV1Bvbv6TEtT",
                                 session=s, log=lambda *a, **k: None)
    check(info["source"] == "html", "接口失败时回退到页面正则")
    check(info["video_url"].endswith("v.m4s") and info["audio_url"].endswith("a.m4s"),
          "回退时取第 1 条作视频、最后 1 条作音频")
    check(info["title"] == "回退标题", "回退时也能拿到标题")

    s2 = FakeSession([("/x/web-interface/view", RuntimeError("x")),
                      ("www.bilibili.com/video/", FakeResponse(text="<html>没有地址</html>"))])
    try:
        core.fetch_media_info("https://www.bilibili.com/video/BV1Bvbv6TEtT",
                              session=s2, log=lambda *a, **k: None)
        check(False, "两条路都失败时应该报错")
    except RuntimeError as e:
        check("baseUrl" in str(e), "两条路都失败时给出明确错误")


def test_api_error_code():
    section("6. 接口返回错误码")
    s = FakeSession([("/x/web-interface/view", FakeResponse(json_data={"code": -404, "message": "啥都木有"}))])
    try:
        core.api_view("BV1Bvbv6TEtT", session=s)
        check(False, "错误码应该抛异常")
    except RuntimeError as e:
        check("啥都木有" in str(e), "错误码里的 message 会带出来")


def test_download_flow():
    section("7. 下载 + 合并（假网络，不真下）")
    chunks = [b"x" * 1024] * 4
    s = FakeSession([
        ("video_1080_avc.m4s", FakeResponse(headers={"Content-Length": str(4096)}, chunks=chunks)),
        ("audio_320.m4s", FakeResponse(headers={"Content-Length": str(4096)}, chunks=chunks)),
    ])

    merged = {}

    def fake_merge(video_path, audio_path, out_path, log=print, audio_codec="aac"):
        merged["saw_video"] = os.path.getsize(video_path)
        merged["saw_audio"] = os.path.getsize(audio_path)
        with open(out_path, "wb") as f:
            f.write(b"fake mp4")

    real_merge = core.merge_av
    core.merge_av = fake_merge
    try:
        with tempfile.TemporaryDirectory() as d:
            info = {"title": "测试/标题:带非法字符", "bvid": "BV1Bvbv6TEtT",
                    "video_url": "https://cdn.example.com/video_1080_avc.m4s",
                    "audio_url": "https://cdn.example.com/audio_320.m4s",
                    "quality": "1080P", "cid": 1, "source": "api"}
            out = core.download("https://www.bilibili.com/video/BV1Bvbv6TEtT", d,
                                info=info, session=s, log=lambda *a, **k: None)
            check(os.path.exists(out), "输出 mp4 生成成功")
            check(os.path.basename(out) == "测试_标题_带非法字符.mp4", "标题被清洗成安全文件名")
            check(merged["saw_video"] == 4096 and merged["saw_audio"] == 4096, "音视频临时文件内容完整")

            left = [f for f in os.listdir(d) if f.endswith(".m4s")]
            check(left == [], "临时 m4s 文件已清理")

            out2 = core.download("https://www.bilibili.com/video/BV1Bvbv6TEtT", d,
                                 info=info, session=s, log=lambda *a, **k: None)
            check(out2 != out and out2.endswith("(2).mp4"), "重复下载不覆盖，自动加序号")

            # 失败场景：视频流 404 → 临时文件也不能留
            s_bad = FakeSession([("video_1080_avc.m4s", RuntimeError("404"))])
            try:
                core.download("https://www.bilibili.com/video/BV1Bvbv6TEtT", d,
                              info=info, session=s_bad, log=lambda *a, **k: None)
                check(False, "下载失败时应该抛异常")
            except RuntimeError:
                check(True, "下载失败时抛出异常（由上层捕获）")
            left = [f for f in os.listdir(d) if f.endswith(".m4s")]
            check(left == [], "失败后临时文件同样被清理")
    finally:
        core.merge_av = real_merge


def test_cookie_and_search():
    section("6b. 浏览器登录态抓取 + 搜索流程（假浏览器）")

    login_cookie = [{"name": "SESSDATA", "value": "abc"},
                    {"name": "bili_jct", "value": "def"}]
    anon_cookie = [{"name": "buvid3", "value": "xyz"}]

    # --- cookies_from_page（旧接口，仍要能用）---
    text = core.cookies_from_page(FakeBrowser(jars=[login_cookie]), log=silent)
    check(text == "SESSDATA=abc; bili_jct=def", "能从浏览器取出 Cookie 字符串（含 SESSDATA）")
    check(core.cookies_from_page(FakeBrowser(jars=[anon_cookie]), log=silent) is None,
          "浏览器未登录（无 SESSDATA）时返回 None，回退配置文件")
    check(core.cookies_from_page(FakeBrowser(raise_cookies=True), log=silent) is None,
          "取 Cookie 抛异常时不崩，返回 None")
    check(core.cookies_from_page(None) is None, "page 为空时返回 None")

    # --- search_videos ---
    real_sleep = core.time.sleep
    core.time.sleep = lambda s: None
    try:
        browser = FakeBrowser([
            "//www.bilibili.com/video/BV1aaaaaaaaa",
            "/video/BV1bbbbbbbbb",
            "//www.bilibili.com/video/BV1ccccccccc",
        ])
        seen = {}
        urls = core.search_videos("无极魔尊", limit=2, page=browser,
                                  log=lambda *a, **k: None,
                                  on_page=lambda p: seen.setdefault("page", p))
        check(browser.visited == core.HOME, "先打开了 B 站首页")
        check(browser.inputs == ["无极魔尊"], "关键词被输入到搜索框")
        check(len(urls) == 2, "limit 参数生效，只返回 2 条")
        check(urls[0] == "https://www.bilibili.com/video/BV1aaaaaaaaa",
              "协议相对地址 // 被补全")
        check(urls[1] == "https://www.bilibili.com/video/BV1bbbbbbbbb",
              "站内路径被补全成完整地址")
        check(seen.get("page") is browser, "on_page 回调拿到了浏览器对象（用于取登录态）")
        check(browser.quit_called is False, "外部传入的浏览器不会被程序关掉")
    finally:
        core.time.sleep = real_sleep


def test_login_tool():
    section("6c. 一键登录 / 会话文件（假浏览器，不真开窗口）")
    import bili_config as config
    import bili_login

    ua = "Mozilla/5.0 (Fake) Chrome/999.0"
    login_cookie = [{"name": "SESSDATA", "value": "abc"},
                    {"name": "bili_jct", "value": "def"}]
    anon_cookie = [{"name": "buvid3", "value": "xyz"}]

    real_fetch = core.fetch_account
    real_session_file = config.SESSION_FILE
    try:
        # --- 从浏览器取 UA 和会话 ---
        s = core.session_from_page(FakeBrowser(jars=[login_cookie], ua=ua))
        check(bool(s) and s["cookie"] == "SESSDATA=abc; bili_jct=def" and s["user_agent"] == ua,
              "session_from_page 同时取到 Cookie 和浏览器真实 UA")
        check(core.session_from_page(FakeBrowser(jars=[anon_cookie])) is None,
              "浏览器未登录时 session_from_page 返回 None")
        check(core.user_agent_from_page(FakeBrowser(ua=None)) is None,
              "读不到 UA 时返回 None 而不是崩")
        check(core.session_from_page(None) is None, "page 为空时 session_from_page 返回 None")

        # --- 轮询等待登录：第 3 次才登录成功 ---
        core.fetch_account = lambda headers=None: {"is_login": True, "uname": "测试用户", "vip": True}
        clock = FakeClock()
        page = FakeBrowser(jars=[anon_cookie, anon_cookie, login_cookie], ua=ua)
        sess = bili_login.wait_for_login(page, timeout=30, interval=2.0, log=silent,
                                         sleep=clock.sleep, clock=clock)
        check(bool(sess) and sess["cookie"].startswith("SESSDATA="), "轮询检测到登录后返回会话")
        check(sess.get("uname") == "测试用户" and sess.get("vip") is True,
              "会话里带上了昵称和大会员状态")
        check(page.polls == 3, f"确实等到第 3 次轮询才成功（实际 {page.polls} 次）")

        # --- 一直不登录 → 超时 ---
        clock = FakeClock()
        page = FakeBrowser(jars=[anon_cookie], ua=ua)
        check(bili_login.wait_for_login(page, timeout=10, interval=2.0, log=silent,
                                        sleep=clock.sleep, clock=clock) is None,
              "一直没登录会超时返回 None")
        check(abs(clock.t - 10) <= 2.0, f"等待时长接近设定值（实际 {clock.t:.0f} 秒）")

        # --- 有 SESSDATA 但服务器说没登录（过期残留）→ 继续等 ---
        core.fetch_account = lambda headers=None: {"is_login": False, "uname": None, "vip": False}
        clock = FakeClock()
        check(bili_login.wait_for_login(FakeBrowser(jars=[login_cookie], ua=ua),
                                        timeout=6, interval=2.0, log=silent,
                                        sleep=clock.sleep, clock=clock) is None,
              "有 SESSDATA 但服务器不认（过期残留）时继续等待，最终超时")

        # --- 完整流程：登录 → 写文件 → 后续请求自动用上 ---
        core.fetch_account = lambda headers=None: {"is_login": True, "uname": "测试用户", "vip": True}
        with tempfile.TemporaryDirectory() as d:
            config.SESSION_FILE = os.path.join(d, "bili_session.json")
            clock = FakeClock()
            page = FakeBrowser(jars=[login_cookie], ua=ua)
            got = bili_login.interactive_login(timeout=20, log=silent, page=page,
                                               sleep=clock.sleep, clock=clock)
            check(bool(got) and got.get("saved_path"), "一键登录流程跑完并返回保存路径")
            check(os.path.exists(config.SESSION_FILE), "会话文件已写入磁盘")
            check(page.quit_called is False, "外部传入的浏览器不会被自动关掉")

            data = config.load_session()
            check(data.get("uname") == "测试用户" and data.get("user_agent") == ua,
                  "会话文件里记了账号、UA 和保存时间")
            check(bool(data.get("saved_at")), "会话文件带保存时间（便于判断新鲜度）")

            h = config.headers()
            check(h["cookie"] == "SESSDATA=abc; bili_jct=def",
                  "headers() 自动改用会话文件里的 Cookie")
            check(h["user-agent"] == ua, "headers() 自动改用会话文件里的 UA")
            check(bili_login.show_status(log=silent) == 0, "show_status 报告已登录（返回 0）")

            # 搜索时顺手抓的登录态也会落盘
            core.grab_login_session(FakeBrowser(jars=[login_cookie], ua="UA-新版"), log=silent)
            check(config.load_session().get("user_agent") == "UA-新版",
                  "grab_login_session 会把浏览器登录态更新到文件")

            # 环境变量优先级最高
            os.environ["BILI_COOKIE"] = "ENV=1"
            try:
                check(config.headers()["cookie"] == "ENV=1",
                      "环境变量 BILI_COOKIE 优先级高于会话文件")
            finally:
                del os.environ["BILI_COOKIE"]

        # --- 没有会话文件、也没有环境变量时，Cookie 应为空 ---
        # 凭据不写死在源码里：源码进仓库，凭据只走环境变量或 bili_session.json
        config.SESSION_FILE = os.path.join(tempfile.gettempdir(), "不存在的会话文件.json")
        h = config.headers()
        check(h["cookie"] == "",
              "没有会话文件时 Cookie 为空（源码里不含任何凭据）")
        check(bili_login.mask("1234567890abcdef") == "1234567890ab...（共 16 字符）",
              "日志里的 Cookie 只露一小截，不会整串打印")
    finally:
        core.fetch_account = real_fetch
        config.SESSION_FILE = real_session_file


def test_download_range():
    section("8. 批量流程（搜索被替换，不真开浏览器）")
    real = {"search": core.search_videos, "check": core.check_login,
            "info": core.fetch_media_info, "download": core.download}
    captured = []

    def fake_search(keyword, limit=None, log=print, **kw):
        return [f"https://www.bilibili.com/video/BVtest{i:05d}" for i in range(1, 6)]

    def fake_info(url, **kw):
        return {"title": os.path.basename(url), "bvid": core.parse_bvid(url),
                "video_url": "v", "audio_url": "a", "quality": "q",
                "cid": 1, "source": "api"}

    def fake_download(url, save_path, **kw):
        captured.append(url)
        return os.path.join(save_path, "out.mp4")

    core.search_videos = fake_search
    core.check_login = lambda headers=None, log=print: True
    core.fetch_media_info = fake_info
    core.download = fake_download
    try:
        with tempfile.TemporaryDirectory() as d:
            r = core.download_range("关键词", 2, 4, d, log=lambda *a, **k: None)
        check(r["total"] == 5, "统计到了搜索结果总数")
        check(r["ok"] == 3 and r["failed"] == 0, "第 2~4 个被下载（共 3 个）")
        check(captured[0].endswith("BVtest00002") and captured[-1].endswith("BVtest00004"),
              "下标换算正确（1-based → 0-based）")

        captured.clear()
        with tempfile.TemporaryDirectory() as d2:
            r2 = core.download_range("关键词", 3, 99, d2, log=lambda *a, **k: None)
        check(r2["ok"] == 3, "范围超出结果数时自动收敛到最后一条")

        captured.clear()
        with tempfile.TemporaryDirectory() as d3:
            r3 = core.download_range("关键词", 9, 12, d3, log=lambda *a, **k: None)
        check(r3["ok"] == 1 and captured[-1].endswith("BVtest00005"),
              "起始下标超过结果总数时收敛到最后一条，不会报错")

        # auto_play / open_folder：下载完后把第一个文件交给播放器 / 资源管理器
        import bili_player
        played, opened = [], []
        real_play = bili_player.play_and_report
        real_open = bili_player.open_folder_and_report
        bili_player.play_and_report = lambda path, player=None, log=print: played.append(path)
        bili_player.open_folder_and_report = lambda path, log=print: opened.append(path)
        try:
            with tempfile.TemporaryDirectory() as d4:
                core.download_range("关键词", 1, 1, d4, log=silent,
                                    auto_play=True, open_folder=True)
            check(len(played) == 1 and played[0].endswith("out.mp4"),
                  "auto_play=True 会把下载好的文件交给播放器")
            check(len(opened) == 1, "open_folder=True 会打开保存目录")

            played.clear()
            opened.clear()
            with tempfile.TemporaryDirectory() as d5:
                core.download_range("关键词", 1, 1, d5, log=silent)
            check(played == [] and opened == [], "不带开关时不会弹任何东西")
        finally:
            bili_player.play_and_report = real_play
            bili_player.open_folder_and_report = real_open
    finally:
        core.search_videos = real["search"]
        core.check_login = real["check"]
        core.fetch_media_info = real["info"]
        core.download = real["download"]


def test_web_app():
    section("9. Flask 网页版")
    import web_app

    real_dl = core.download_range

    def fake_range(keyword, start, end, save_path, log=print, **kw):
        log(f"假装在下载 {keyword} {start}-{end} → {save_path}")
        return {"total": 5, "ok": 2, "failed": 0, "files": ["a.mp4", "b.mp4"], "errors": []}

    core.download_range = fake_range
    try:
        client = web_app.app.test_client()
        check(client.get("/").status_code == 200, "首页能打开")

        r = client.post("/download", data={"keyword": "", "start": "1", "end": "2", "path": "D:\\x"})
        check(r.status_code == 400 and "关键词" in r.get_json()["error"], "空关键词被拦下")
        r = client.post("/download", data={"keyword": "k", "start": "5", "end": "2", "path": "D:\\x"})
        check(r.status_code == 400, "结束小于开始被拦下")
        r = client.post("/download", data={"keyword": "k", "start": "a", "end": "2", "path": "D:\\x"})
        check(r.status_code == 400, "非数字范围被拦下")

        # --- 网址模式 ---
        r = client.post("/download", data={"urls": "BV1Bvbv6TEtT\nav123", "path": "D:\\x"})
        check(r.status_code == 400 or r.status_code == 200, "网址模式不再要求关键词")

        urls_seen = {}
        real_by_urls = core.download_by_urls

        def fake_by_urls(urls, save_path=None, log=print, **kw):
            urls_seen["urls"] = list(urls)
            urls_seen["kw"] = kw
            log("假装在按网址下载")
            return {"total": len(urls), "ok": len(urls), "failed": 0,
                    "files": [os.path.join(save_path, "x.mp4")], "errors": [],
                    "urls": len(urls)}

        core.download_by_urls = fake_by_urls
        try:
            r = client.post("/download", data={"urls": "BV1Bvbv6TEtT\n# 注释\nav123",
                                               "start": "2", "end": "5",
                                               "path": tempfile.gettempdir()})
            check(r.status_code == 200, "提交网址清单返回 200")
            url_task = r.get_json()["task_id"]
            status = None
            for _ in range(50):
                status = client.get(f"/status/{url_task}").get_json()
                if status["status"] != "running":
                    break
                time.sleep(0.05)
            check(status["status"] == "completed", "网址任务最终 completed")
            check(urls_seen["urls"] == ["BV1Bvbv6TEtT", "av123"],
                  "网址被正确切分（注释行被忽略）")
            check(urls_seen["kw"].get("start") == 2 and urls_seen["kw"].get("end") == 5,
                  "网址模式的范围透传给了核心")
            check("假装在按网址下载" in status["log"], "网址模式的日志回传给了网页")

            r = client.post("/download", data={"urls": "   ", "path": "D:\\x"})
            check(r.status_code == 400, "空网址被拦下（会退回搜索模式校验关键词）")
            r = client.post("/download", data={"urls": "BV1Bvbv6TEtT", "start": "5", "end": "2",
                                               "path": "D:\\x"})
            check(r.status_code == 400, "网址模式里结束 < 开始被拦下")
            r = client.post("/download", data={"urls": "BV1Bvbv6TEtT", "path": ""})
            check(r.status_code == 400, "网址模式仍然要求保存路径")
        finally:
            core.download_by_urls = real_by_urls

        r = client.post("/download", data={"keyword": "测试", "start": "1", "end": "2", "path": "D:\\x"})
        check(r.status_code == 200, "正常参数返回 200")
        task_id = r.get_json()["task_id"]

        status = None
        for _ in range(50):
            status = client.get(f"/status/{task_id}").get_json()
            if status["status"] != "running":
                break
            time.sleep(0.05)
        check(status["status"] == "completed", "任务状态最终变为 completed")
        check("假装在下载 测试 1-2" in status["log"], "日志通过 log 回调回传给了网页")
        check("2 个视频" in status["message"], "完成消息带上了数量")

        check(client.get("/status/不存在").status_code == 404, "未知任务返回 404")

        # --- 结果列表上的「播放 / 打开文件夹」---
        import bili_player
        real_play = bili_player.play
        real_open = bili_player.open_folder
        played, opened = [], []
        bili_player.play = lambda path, player=None: played.append(path)
        bili_player.open_folder = lambda path, select=True: opened.append(path)
        try:
            r = client.post("/download", data={"keyword": "测试", "start": "1", "end": "2",
                                               "path": tempfile.gettempdir()})
            dl_task = r.get_json()["task_id"]
            files = None
            for _ in range(50):
                data = client.get(f"/status/{dl_task}").get_json()
                if data["status"] != "running":
                    files = data.get("files")
                    break
                time.sleep(0.05)
            check(bool(files), "任务完成后 /status 会带上文件列表（前端靠它画按钮）")

            r = client.post("/open", data={"task_id": dl_task, "index": "0", "action": "play"})
            check(r.status_code == 200 and played == [files[0]], "点「播放」会打开第 0 个文件")

            played.clear()
            client.post("/open", data={"task_id": dl_task, "index": "1", "action": "play"})
            check(played == [files[1]], "点第 2 行的「播放」打开对应文件")

            client.post("/open", data={"task_id": dl_task, "index": "0", "action": "folder"})
            check(opened == [files[0]], "点「打开所在文件夹」定位到该文件")

            r = client.post("/open", data={"task_id": dl_task, "index": "9", "action": "play"})
            check(r.status_code == 400, "文件序号超出范围被拦下")
            r = client.post("/open", data={"task_id": dl_task, "index": "x", "action": "play"})
            check(r.status_code == 400, "序号不是数字被拦下")
            r = client.post("/open", data={"task_id": "不存在的任务", "index": "0"})
            check(r.status_code == 404, "未知任务返回 404")

            r = client.post("/open", data={"task_id": dl_task, "action": "save_folder"})
            check(r.status_code == 200 and opened[-1] == tempfile.gettempdir(),
                  "「打开保存目录」用的是服务端记录的路径")
        finally:
            bili_player.play = real_play
            bili_player.open_folder = real_open

        # --- 网页版「一键获取登录态」接口 ---
        import bili_login
        seen = {}
        real_login = bili_login.interactive_login

        def fake_login(timeout=180, log=print, **kw):
            seen["timeout"] = timeout
            log("假装在打开浏览器等登录")
            return {"cookie": "SESSDATA=fake", "uname": "测试用户", "vip": True,
                    "saved_path": "bili_session.json"}

        bili_login.interactive_login = fake_login
        try:
            r = client.post("/login", data={"timeout": "60"})
            check(r.status_code == 200, "/login 返回 200 并给出 task_id")
            login_task = r.get_json()["task_id"]
            status = None
            for _ in range(50):
                status = client.get(f"/status/{login_task}").get_json()
                if status["status"] != "running":
                    break
                time.sleep(0.05)
            check(status["status"] == "completed", "登录任务最终 completed")
            check("测试用户" in status["message"], "完成消息带上登录到的昵称")
            check("假装在打开浏览器等登录" in status["log"], "登录日志也回传给了网页")

            client.post("/login", data={"timeout": "3"})
            time.sleep(0.2)
            check(seen["timeout"] == 30.0, "过小的等待秒数被兜底到 30 秒")

            r = client.post("/login", data={"timeout": "abc"})
            check(r.status_code == 400, "非法等待秒数被拦下")
        finally:
            bili_login.interactive_login = real_login
    finally:
        core.download_range = real_dl


LIVE_BV = "https://www.bilibili.com/video/BV1Bvbv6TEtT"


def test_live():
    section("10. 真实接口验证（只解析，不下载视频流）")
    headers = core.make_headers()
    ok = core.check_login(headers)
    check(True, "Cookie 登录状态：" + ("已登录" if ok else "未登录/已过期（只能拿 480P，不影响能不能下载）"))
    info = core.fetch_media_info(LIVE_BV, headers=headers)
    check(info["source"] == "api", f"真实解析走通接口路径（{info['quality']}）")
    check(bool(info["title"]), f"拿到真实标题：{info['title'][:30]}")
    check(info["video_url"].startswith("http") and info["audio_url"].startswith("http"),
          "视频/音频直链都是 http 开头")

    ok_video = False
    try:
        import requests
        # 用 Range GET 取 64KB —— 和真正下载走的是同一条路（HEAD 偶尔会被 CDN 拒）
        probe_headers = dict(headers)
        probe_headers["Range"] = "bytes=0-65535"
        r = requests.get(info["video_url"], headers=probe_headers, timeout=20, stream=True)
        head = next(r.iter_content(1024), b"")
        r.close()
        ok_video = r.status_code in (200, 206) and b"ftyp" in head[:16]
        check(ok_video, f"视频流能拉到数据（HTTP {r.status_code}，开头是 MP4 分片）")
    except Exception as e:
        check(False, f"视频流拉取失败：{e}")


def test_browser():
    section("11. 真实搜索验证（会弹出 Chrome）")
    try:
        holder = {}

        def grab(page):
            # 必须在浏览器还开着的时候取 Cookie（search_videos 结束时会自己关掉浏览器）
            holder["cookie"] = core.cookies_from_page(page)

        urls = core.search_videos("无极魔尊", limit=3, on_page=grab)
        check(len(urls) > 0, f"搜索拿到 {len(urls)} 个结果")
        check(all(u.startswith("https://www.bilibili.com/video/") for u in urls),
              "结果都是完整可用的视频链接")
        if urls:
            print("       示例：", urls[0])

        cookie = holder.get("cookie")
        if cookie:
            check(True, "浏览器里有 B 站登录态，下载时会自动用它（清晰度取决于账号）")
            check(core.check_login(core.make_headers(cookie=cookie)),
                  "用浏览器登录态检查登录：成功")
        else:
            check(True, "浏览器里没有登录态 —— 用配置文件里的 Cookie（可先在弹出的 Chrome 里登录一次）")
    except Exception as e:
        check(False, f"搜索步骤失败：{e}")


def test_real_download():
    section("12. 真实下载验证（会真的下一个短视频，下完删掉）")
    import shutil
    from moviepy import VideoFileClip

    tmp = tempfile.mkdtemp(prefix="bili_selftest_")
    try:
        out = core.download(LIVE_BV, tmp, log=print)
        check(os.path.exists(out), f"完整跑通了「解析 → 下流 → 合并」：{os.path.basename(out)}")
        size = os.path.getsize(out) / 1048576
        check(size > 0.2, f"输出文件大小合理（{size:.2f} MB）")
        clip = VideoFileClip(out)
        try:
            check(clip.duration > 0, f"能正常打开，时长 {clip.duration:.1f} 秒")
            check(clip.audio is not None, "合并进去的音轨存在（不是无声视频）")
        finally:
            clip.close()
        tail = [f for f in os.listdir(tmp) if f.endswith(".m4s")]
        check(tail == [], "临时 m4s 已清理干净")
    except Exception as e:
        check(False, f"真实下载失败：{e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_player():
    section("10. 打开/播放（启动进程被替换，不会真的弹播放器）")
    import bili_player as player

    real_spawn, real_start = player._spawn, player._startfile
    calls = []
    player._spawn = lambda args: calls.append(list(args))
    player._startfile = lambda p: calls.append(["startfile", p])
    try:
        with tempfile.TemporaryDirectory() as d:
            video = os.path.join(d, "测试视频.mp4")
            other = os.path.join(d, "说明.txt")
            open(video, "w").close()
            open(other, "w").close()

            # --- 校验 ---
            try:
                player.ensure_playable(video)
                check(True, "存在的 mp4 通过校验")
            except Exception as e:
                check(False, f"mp4 校验失败：{e}")
            for bad, desc in ((os.path.join(d, "没有这个.mp4"), "不存在的文件"),
                              (other, "非音视频类型")):
                try:
                    player.ensure_playable(bad)
                    check(False, f"{desc} 应该被拒绝")
                except player.OpenError:
                    check(True, f"{desc}被拒绝（不会拿去执行）")

            # --- 播放：默认关联程序 ---
            calls.clear()
            ret = player.play(video)
            check(ret == video and calls and calls[0][-1] == video,
                  "不给播放器时交给系统默认程序打开")

            # --- 播放：指定播放器 ---
            calls.clear()
            exe = os.path.join(d, "player.exe")
            open(exe, "w").close()
            player.play(video, player=exe)
            check(calls and calls[0][0] == exe and calls[0][1] == video,
                  "给了播放器路径时用该播放器打开")
            try:
                player.play(video, player=os.path.join(d, "不存在.exe"))
                check(False, "播放器路径不存在应该报错")
            except player.OpenError:
                check(True, "播放器路径不存在时报错而不是静默失败")

            # --- 打开文件夹：选中文件 ---
            calls.clear()
            folder = player.open_folder(video)
            check(folder == d, "返回的是所在目录")
            check(calls and video in calls[0][-1], "打开目录时顺带选中了该文件")

            # --- 打开文件夹：整个目录 ---
            calls.clear()
            player.open_folder(d)
            check(calls and calls[0][-1] == d, "传目录时直接打开目录")

            # --- 带日志的包装：失败只记一行、不抛异常 ---
            logs = []
            check(player.play_and_report(os.path.join(d, "没有.mp4"), log=logs.append) is None,
                  "play_and_report 遇到坏路径返回 None 而不是崩")
            check(any("打开失败" in str(x) for x in logs), "失败原因写进了日志")
            logs.clear()
            player.play_and_report(video, log=logs.append)
            check(any("已交给播放器" in str(x) for x in logs), "成功时日志说明交给了播放器")
    finally:
        player._spawn, player._startfile = real_spawn, real_start


def test_cli_play_flags():
    section("11. 命令行 --play / --open-folder")
    import bili_player
    import main as cli

    real_range = core.download_range
    real_play = bili_player.play_and_report
    real_folder = bili_player.open_folder_and_report
    seen = {}
    played = []
    opened = []

    def fake_range(keyword, start, end, save_path, **kw):
        seen.update(kw)
        return {"total": 2, "ok": 1, "failed": 0,
                "files": [os.path.join(save_path, "a.mp4")], "errors": []}

    core.download_range = fake_range
    bili_player.play_and_report = lambda path, player=None, log=print: played.append(path)
    bili_player.open_folder_and_report = lambda path, log=print: opened.append(path)
    try:
        rc = cli.main(["关键词", "-s", "1", "-e", "1", "--play", "--open-folder",
                       "-p", tempfile.gettempdir()])
        check(rc == 0, "--play --open-folder 正常退出")
        check(seen.get("auto_play") is True and seen.get("open_folder") is True,
              "两个开关被透传给核心（批量路径由 download_range 负责打开）")

        played.clear()
        opened.clear()
        rc = cli.main(["关键词", "-s", "1", "-e", "1", "--no-play", "--no-open-folder",
                       "-p", tempfile.gettempdir()])
        check(rc == 0 and seen.get("auto_play") is False and seen.get("open_folder") is False,
              "--no-play / --no-open-folder 能覆盖配置")

        # --url 单文件路径：这条路由 main 自己负责打开
        real_info, real_download = core.fetch_media_info, core.download
        out_file = os.path.join(tempfile.gettempdir(), "单文件.mp4")
        core.fetch_media_info = lambda url, **kw: {
            "title": "t", "bvid": "BV1Bvbv6TEtT", "video_url": "v", "audio_url": "a",
            "quality": "q", "cid": 1, "source": "api"}
        core.download = lambda url, save_path, **kw: out_file
        try:
            played.clear()
            opened.clear()
            rc = cli.main(["--url", "https://www.bilibili.com/video/BV1Bvbv6TEtT",
                           "-p", tempfile.gettempdir(), "--play", "--open-folder"])
        finally:
            core.fetch_media_info, core.download = real_info, real_download
        check(rc == 0 and played == [out_file] and opened == [out_file],
              "--url 下单个视频时也会自动播放 / 打开目录")

        played.clear()
        opened.clear()
        core.fetch_media_info = lambda url, **kw: {
            "title": "t", "bvid": "BV1Bvbv6TEtT", "video_url": "v", "audio_url": "a",
            "quality": "q", "cid": 1, "source": "api"}
        core.download = lambda url, save_path, **kw: out_file
        try:
            cli.main(["--url", "https://www.bilibili.com/video/BV1Bvbv6TEtT",
                      "-p", tempfile.gettempdir(), "--no-play", "--no-open-folder"])
        finally:
            core.fetch_media_info, core.download = real_info, real_download
        check(played == [] and opened == [], "关掉开关后不会弹任何东西")
    finally:
        core.download_range = real_range
        bili_player.play_and_report = real_play
        bili_player.open_folder_and_report = real_folder


def test_url_mode():
    section("10. 按网址下载（解析 / 分P / 列表页展开 / 命令行）")

    # --- 网址识别 ---
    cases = [
        ("https://www.bilibili.com/video/BV1Bvbv6TEtT/?spm_id_from=333.337", "video", "BV1Bvbv6TEtT", None, None),
        ("https://www.bilibili.com/video/BV1Bvbv6TEtT?p=3", "video", "BV1Bvbv6TEtT", None, 3),
        ("BV1Bvbv6TEtT", "video", "BV1Bvbv6TEtT", None, None),
        ("av123456", "video", None, 123456, None),
        ("https://www.bilibili.com/video/av170001", "video", None, 170001, None),
        ("https://space.bilibili.com/123456/video", "list", None, None, None),
        ("https://www.bilibili.com/list/ml123456", "list", None, None, None),
        ("https://www.bilibili.com/medialist/detail/ml123", "list", None, None, None),
    ]
    ok_all = True
    for raw, kind, bvid, aid, page in cases:
        t = core.parse_target(raw)
        good = (t["kind"] == kind and t["bvid"] == bvid and t["aid"] == aid
                and t["page"] == page)
        ok_all = ok_all and good
    check(ok_all, f"{len(cases)} 种网址写法都识别正确（含 ?p= 、av号、BV号、列表页）")

    for bad in ("", "   ", "https://www.example.com/x", "随便一段话"):
        try:
            core.parse_target(bad)
            check(False, f"垃圾输入 {bad!r} 应该被拒绝")
        except ValueError:
            pass
    check(True, "空串 / 非 B站地址会被拒绝并给出提示")

    # --- 一段文本切网址 ---
    text = ("BV1Bvbv6TEtT, av123\n"
            "# 这行是注释，应该被忽略\n"
            "https://www.bilibili.com/video/BV1bbbbbbbbb  https://www.bilibili.com/video/BV1ccccccccc\n")
    urls = core.split_urls(text)
    check(urls == ["BV1Bvbv6TEtT", "av123",
                   "https://www.bilibili.com/video/BV1bbbbbbbbb",
                   "https://www.bilibili.com/video/BV1ccccccccc"],
          "换行/空格/逗号都能切分，井号注释被忽略")
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "links.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        check(core.read_urls_from_file(p) == urls, "网址清单文件读取正常")

    # --- 分P ---
    view = {"code": 0, "data": {
        "title": "多P视频", "cid": 111, "bvid": "BV1Bvbv6TEtT",
        "pages": [{"cid": 111, "part": "第一集"},
                  {"cid": 222, "part": "第二集"},
                  {"cid": 333, "part": "第三集"}]}}

    def session_for(view_data):
        return FakeSession([
            ("/x/web-interface/view", FakeResponse(json_data=view_data)),
            ("/x/player/playurl", FakeResponse(json_data={"code": 0, "data": {"dash": dash_fixture()}})),
        ])

    s = session_for(view)
    info = core.fetch_media_info("https://www.bilibili.com/video/BV1Bvbv6TEtT?p=2",
                                 session=s, log=silent)
    check(info["cid"] == 222, "?p=2 时用的是第 2 个分P的 cid")
    check(any(p.get("cid") == 222 for p in s.params), "playurl 请求里带的确实是第 2 个分P")
    check("_P2_" in info["title"] and "第二集" in info["title"],
          f"标题带上分P信息：{info['title']}")

    info1 = core.fetch_media_info("https://www.bilibili.com/video/BV1Bvbv6TEtT",
                                  session=session_for(view), log=silent)
    check(info1["title"] == "多P视频" and info1["cid"] == 111,
          "不带 ?p= 时还是原样下第 1 个分P、标题不加后缀")

    try:
        core.fetch_media_info("https://www.bilibili.com/video/BV1Bvbv6TEtT?p=99",
                              session=session_for(view), log=silent)
        check(False, "分P序号超范围应该报错")
    except RuntimeError as e:
        check("只有 3 个分P" in str(e), "分P序号超出范围时给出明确提示")

    # --- 按网址下载：单个视频 + 列表页 + 范围 ---
    real = {"collect": core.collect_video_links, "info": core.fetch_media_info,
            "download": core.download, "check": core.check_login}
    core.check_login = lambda headers=None, log=print: True
    core.collect_video_links = lambda url, **kw: [
        "https://www.bilibili.com/video/BVlist{:05d}".format(i) for i in range(1, 5)]
    core.fetch_media_info = lambda url, **kw: {
        "title": url, "bvid": core.parse_bvid(url), "video_url": "v",
        "audio_url": "a", "quality": "q", "cid": 1, "source": "api"}
    got = []

    def fake_download(url, save_path, **kw):
        got.append(url)
        return os.path.join(save_path, "out.mp4")

    core.download = fake_download
    try:
        with tempfile.TemporaryDirectory() as d:
            r = core.download_by_urls(["BV1Bvbv6TEtT"], d, log=silent)
        check(r["ok"] == 1 and got[0].endswith("BV1Bvbv6TEtT"),
              "单个视频网址直接下载（BV 号也能用）")

        got.clear()
        with tempfile.TemporaryDirectory() as d:
            r = core.download_by_urls(["https://space.bilibili.com/1/video"], d, log=silent)
        check(r["ok"] == 4 and r["total"] == 4, "列表页网址会被展开成 4 个视频并全部下载")

        got.clear()
        with tempfile.TemporaryDirectory() as d:
            r = core.download_by_urls(["https://space.bilibili.com/1/video"], d,
                                      start=2, end=3, log=silent)
        check(r["ok"] == 2 and got[0].endswith("BVlist00002") and got[-1].endswith("BVlist00003"),
              "列表页 + 范围只下第 2~3 个")

        got.clear()
        with tempfile.TemporaryDirectory() as d:
            r = core.download_by_urls(["不是网址", "BV1Bvbv6TEtT"], d, log=silent)
        check(r["ok"] == 1 and len(r["errors"]) == 1,
              "一个网址不合法时跳过它、其余照常下载（不整体失败）")

        got.clear()
        with tempfile.TemporaryDirectory() as d:
            r = core.download_by_urls(["BV1Bvbv6TEtT", "BV1Bvbv6TEtT"], d, log=silent)
        check(r["ok"] == 2, "重复粘贴同一个网址会各下一次（不做去重，避免误吞用户意图）")
    finally:
        core.collect_video_links = real["collect"]
        core.fetch_media_info = real["info"]
        core.download = real["download"]
        core.check_login = real["check"]

    # --- 命令行 ---
    import main as cli
    real_by_urls = core.download_by_urls
    real_range = core.download_range
    seen = {}

    def fake_by_urls(urls, save_path=None, **kw):
        seen["urls"] = list(urls)
        seen["kw"] = kw
        return {"total": len(urls), "ok": len(urls), "failed": 0,
                "files": [], "errors": [], "urls": len(urls)}

    core.download_by_urls = fake_by_urls
    core.download_range = lambda kw, s, e, p, **rest: (seen.update(ss=s, ee=e) or
                                                       {"total": 0, "ok": 0, "failed": 0,
                                                        "files": [], "errors": []})
    try:
        rc = cli.main(["--url", "BV1Bvbv6TEtT,av123", "--url", "https://x/bilibili.com/video/BV1ccccccccc",
                       "-p", tempfile.gettempdir()])
        check(rc == 0 and seen["urls"] == ["BV1Bvbv6TEtT", "av123",
                                           "https://x/bilibili.com/video/BV1ccccccccc"],
              "--url 支持重复传 + 逗号分隔")

        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "links.txt")
            with open(p, "w", encoding="utf-8") as f:
                f.write("# 注释\nBV1Bvbv6TEtT\nav999\n")
            cli.main(["--url-file", p, "-p", tempfile.gettempdir()])
        check(seen["urls"] == ["BV1Bvbv6TEtT", "av999"], "--url-file 从文件读网址清单")

        cli.main(["关键词"])           # 搜索模式：范围默认值仍然是 1~2
        check(seen.get("ss") == 1 and seen.get("ee") == 2, "搜索模式默认范围仍是 1~2")

        with tempfile.TemporaryDirectory() as d:
            cli.main(["--url", "https://space.bilibili.com/1/video", "-s", "2", "-e", "5",
                      "-p", d])
        check(seen["kw"].get("start") == 2 and seen["kw"].get("end") == 5,
              "网址模式把范围透传给核心（列表页才生效）")

        cli.main(["--url", "BV1Bvbv6TEtT", "-p", tempfile.gettempdir()])
        check(seen["kw"].get("start") is None and seen["kw"].get("end") is None,
              "网址模式不传范围时不截断（全部下载）")
    finally:
        core.download_by_urls = real_by_urls
        core.download_range = real_range


def main():
    args = sys.argv[1:]
    print("B站爬虫重构自检开始（离线部分不联网）")
    print("=" * 56)

    test_utils()
    test_html_parsing()
    test_pick_streams()
    test_fetch_media_info_api()
    test_fetch_media_info_fallback()
    test_api_error_code()
    test_cookie_and_search()
    test_login_tool()
    test_download_flow()
    test_player()
    test_cli_play_flags()
    test_url_mode()
    test_download_range()
    test_web_app()

    if "--live" in args:
        test_live()
    if "--download" in args:
        test_real_download()
    if "--browser" in args:
        test_browser()

    print("\n" + "=" * 56)
    print(f"通过 {len(PASS)} 项，失败 {len(FAIL)} 项")
    if FAIL:
        for m in FAIL:
            print("  失败：", m)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
