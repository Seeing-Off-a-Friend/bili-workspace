# -*- coding: utf-8 -*-
"""B站视频搜索 / 解析 / 下载 —— 核心库（命令行与网页共用这一份实现）。

对外只暴露 4 件事：

    search_videos(keyword, ...)                    搜索，返回视频链接列表
    fetch_media_info(url, ...)                     解析标题 + 音视频直链
    download(url, save_path, ...)                  下载单个视频并合并成 mp4
    download_range(keyword, start, end, ...)       搜索 + 按下标批量下载

设计约定：
    1. 所有函数都不调用 input()，参数全部显式传入（旧版网页版就是死在这里）。
    2. 所有函数都接受 log=print 回调输出进度，不劫持 sys.stdout。
    3. 解析优先走官方 JSON 接口（稳），失败才回退到页面正则（旧版的老办法）。
"""

import os
import re
import time

import requests

from bili_config import DEFAULT_SAVE_PATH, QUALITY, TIMEOUT
from bili_config import headers as build_headers

import bili_player

# ------------------------- 接口地址 -------------------------
HOME = "https://www.bilibili.com/"
API_NAV = "https://api.bilibili.com/x/web-interface/nav"
API_VIEW = "https://api.bilibili.com/x/web-interface/view"
API_PLAYURL = "https://api.bilibili.com/x/player/playurl"

# ------------------------- 正则 -------------------------
BV_RE = re.compile(r"(BV[0-9A-Za-z]{10})")
AV_RE = re.compile(r"\bav(\d+)\b", re.I)
BASEURL_RE = re.compile(r'"baseUrl":"(.+?)"')
TAG_RE = re.compile(r"<[^>]+>")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
# Windows 文件名非法字符
ILLEGAL_RE = re.compile(r'[\\/:*?"<>|\r\n\t]')

# 页面回退解析标题时依次尝试的 XPath
TITLE_XPATHS = (
    '//div[@class="video-info-title-inner"]/h1/@title',
    '//h1[@class="video-title"]/@title',
    '//h1[@class="video-title"]/text()',
    '//span[@class="tit"]/text()',
)

# <title> 里常见的站点后缀，按长度从长到短匹配后剥掉
TITLE_SUFFIXES = (
    "_哔哩哔哩_bilibili",
    "哔哩哔哩_bilibili",
    "-哔哩哔哩-bilibili",
    "_哔哩哔哩",
    "哔哩哔哩",
    "_bilibili",
)

CHUNK = 256 * 1024


class BiliError(RuntimeError):
    """确定性错误：重试或换解析方式都没用（分P不存在、视频被删、权限不足……）。

    和普通网络异常区分开，是为了不让它触发"页面正则回退"——
    否则请求第 99 个分P失败后，回退逻辑可能把第 1 个分P当成功下载，静默给错文件。
    """


# ============================== 纯工具函数 ==============================

def make_headers(cookie=None, user_agent=None):
    """构造请求头（默认取 bili_config 里的那份）。"""
    return build_headers(cookie, user_agent)


def absolute_url(href, base=HOME):
    """把 '//www.bilibili.com/video/BVxxx' 这类协议相对地址补全。"""
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return base.rstrip("/") + href
    return href


def parse_bvid(url):
    """从链接里取出 BV 号。"""
    m = BV_RE.search(url or "")
    return m.group(1) if m else None


def sanitize_filename(name, max_len=80):
    """把视频标题清洗成能当文件名用的字符串。"""
    name = ILLEGAL_RE.sub("_", (name or "").strip())
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > max_len:
        name = name[:max_len].rstrip(" .")
    return name or "video"


def unique_path(path):
    """目标已存在时自动加 (2)(3)...，避免覆盖已下载的视频。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    i = 2
    while os.path.exists(f"{base}({i}){ext}"):
        i += 1
    return f"{base}({i}){ext}"


def extract_media_from_html(html):
    """旧版的老办法：从页面 HTML 里正则扒 baseUrl。"""
    return BASEURL_RE.findall(html or "")


def extract_title_from_html(html):
    """从页面 HTML 里尽力猜出标题。"""
    html = html or ""
    try:
        from lxml import etree
        doc = etree.HTML(html)
        for xp in TITLE_XPATHS:
            got = doc.xpath(xp)
            if got:
                t = str(got[0]).strip()
                if t:
                    return t
    except Exception:
        pass
    m = TITLE_RE.search(html)
    if m:
        t = TAG_RE.sub("", m.group(1))
        t = re.sub(r"\s+", " ", t).strip()
        lower = t.lower()
        for suffix in TITLE_SUFFIXES:
            if lower.endswith(suffix):
                t = t[: len(t) - len(suffix)].strip(" _-|")
                break
        return t
    return ""


def pick_streams(dash):
    """从 dash 里挑一路视频 + 一路音频。

    视频：先按清晰度 id 取最高，同一清晰度下优先 avc 编码（播放器兼容性最好）。
    音频：按码率取最高。
    """
    videos = (dash or {}).get("video") or []
    audios = (dash or {}).get("audio") or []
    if not videos or not audios:
        raise RuntimeError("接口没返回可用的音视频流（可能是充电专属 / 付费 / 已下架视频）")

    def vkey(v):
        codec = str(v.get("codecs") or "")
        return (int(v.get("id") or 0),
                1 if codec.startswith("avc") else 0,
                int(v.get("bandwidth") or 0))

    def akey(a):
        return (int(a.get("bandwidth") or 0), int(a.get("id") or 0))

    return max(videos, key=vkey), max(audios, key=akey)


def describe_stream(video):
    """把一路视频流描述成人话，用于日志。"""
    codec = str(video.get("codecs") or "?")
    family = "AVC/H.264" if codec.startswith("avc") else ("HEVC/H.265" if codec.startswith("hev") else codec)
    return (f"{video.get('width')}x{video.get('height')}  {family}  "
            f"{int(video.get('bandwidth') or 0) / 1024:.0f} Kbps")


# ============================== 接口访问 ==============================

def fetch_account(headers=None):
    """调 nav 接口问一句"我是谁"。返回：

        {'is_login': True,  'uname': '昵称', 'vip': False}   已登录
        {'is_login': False, 'uname': None,  'vip': False}    未登录/Cookie 过期
        None                                                 请求本身失败（网络问题）
    """
    headers = headers or make_headers()
    try:
        r = requests.get(API_NAV, headers=headers, timeout=10)
        data = r.json().get("data") or {}
        return {
            "is_login": bool(data.get("isLogin")),
            "uname": data.get("uname") or None,
            "vip": bool(data.get("vipStatus")),
        }
    except Exception:
        return None


def check_login(headers=None, log=print):
    """检查 Cookie 是否还有效 —— 只影响能拿到多高的清晰度，不影响能不能下。"""
    acct = fetch_account(headers)
    if acct is None:
        log("[登录] 检查失败（不影响下载，可能是网络问题）")
        return False
    if acct["is_login"]:
        log(f"[登录] Cookie 有效：{acct['uname']}（{'大会员' if acct['vip'] else '普通账号'}）")
        return True
    log("[登录] Cookie 无效或未登录 —— 只能拿到 480P 及以下的清晰度")
    return False


def user_agent_from_page(page, log=None):
    """读浏览器的真实 User-Agent —— 和 Cookie 配套使用，避免 UA/Cookie 不匹配被风控。"""
    say = log or (lambda *a, **k: None)
    try:
        ua = page.run_js("return navigator.userAgent")
    except Exception as e:
        say(f"[UA] 读取浏览器 User-Agent 失败（{e}）")
        return None
    if isinstance(ua, str) and ua.strip():
        return ua.strip()
    say("[UA] 浏览器没返回 User-Agent")
    return None


def session_from_page(page, log=None):
    """从浏览器里取出登录态：{'cookie': ..., 'user_agent': ...}。

    只认含 SESSDATA 的 Cookie；浏览器没登录就返回 None（静默，不打印）。
    log 传 None（默认）表示不输出日志，避免轮询时刷屏。
    """
    say = log or (lambda *a, **k: None)
    if page is None:
        return None
    try:
        # all_domains=False：只取当前页面所在站点（bilibili.com）的 Cookie，
        # 免得把别的网站的 Cookie 也塞进请求头
        cookies = page.cookies(all_domains=False)
        text = cookies.as_str() if hasattr(cookies, "as_str") else "; ".join(
            f"{c.get('name')}={c.get('value')}" for c in cookies)
    except Exception as e:
        say(f"[Cookie] 从浏览器取 Cookie 失败（{e}）")
        return None
    if not text or "SESSDATA" not in text:
        return None
    return {"cookie": text, "user_agent": user_agent_from_page(page, log=log)}


def cookies_from_page(page, log=print):
    """（旧接口）只取 Cookie 字符串，没有登录态时返回 None。"""
    if page is None:
        return None
    session = session_from_page(page)
    if not session:
        log("[Cookie] 浏览器里没有登录态，继续用配置文件里的那份")
        return None
    log("[Cookie] 已从浏览器取到登录态（含 SESSDATA），本次用它请求")
    return session["cookie"]


def grab_login_session(page, log=print, persist=True):
    """从浏览器取登录态；取到就默认写进 bili_session.json，返回 session 字典或 None。

    有它之后：Cookie 过期不用手抄 —— 浏览器里登录一次就自动更新，
    下次运行（哪怕不搜视频）直接读文件即可。
    """
    session = session_from_page(page)
    if not session:
        log("[Cookie] 浏览器里没有登录态，继续用配置文件里的那份")
        return None
    log("[Cookie] 已从浏览器取到登录态（含 SESSDATA），本次用它请求")
    if persist:
        try:
            from bili_config import save_session
            path = save_session(session["cookie"], session.get("user_agent"),
                                source="browser-search")
            log(f"[Cookie] 登录态已更新到 {os.path.basename(path)}")
        except Exception as e:
            log(f"[Cookie] 写入会话文件失败（不影响本次下载）：{e}")
    return session


def api_view(bvid=None, headers=None, session=None, aid=None):
    """取视频基本信息（含 cid 和分P列表）。bvid 和 aid 给一个就行。"""
    s = session or requests
    params = {"aid": aid} if (aid and not bvid) else {"bvid": bvid}
    r = s.get(API_VIEW, params=params,
              headers=headers or make_headers(), timeout=TIMEOUT)
    j = r.json()
    if j.get("code") != 0:
        raise BiliError(f"取视频信息失败：{j.get('message')}（code={j.get('code')}）")
    return j["data"]


def api_playurl(bvid, cid, headers=None, session=None, quality=None):
    """取播放地址（dash 音视频分离流）。"""
    s = session or requests
    params = {"bvid": bvid, "cid": cid, "qn": quality or QUALITY,
              "fnval": 4048, "fourk": 1}
    r = s.get(API_PLAYURL, params=params,
              headers=headers or make_headers(), timeout=TIMEOUT)
    j = r.json()
    if j.get("code") != 0:
        raise BiliError(f"取播放地址失败：{j.get('message')}（code={j.get('code')}）")
    dash = (j.get("data") or {}).get("dash")
    if not dash:
        raise BiliError("接口返回里没有 dash 字段（可能是番剧/付费内容）")
    return dash


# ============================== 网址解析 ==============================

def expand_short_url(url, session=None, log=None, timeout=None):
    """把 b23.tv 这类短链还原成真实地址；还原失败就原样返回。"""
    say = log or (lambda *a, **k: None)
    if "b23.tv" not in (url or ""):
        return url
    s = session or requests
    try:
        r = s.get(url, headers=make_headers(), timeout=timeout or TIMEOUT,
                  allow_redirects=True)
        final = getattr(r, "url", None)
        if final and "bilibili.com" in final:
            say(f"[网址] 短链已还原：{final}")
            return final
    except Exception as e:
        say(f"[网址] 短链还原失败（{e}），按原样处理")
    return url


def parse_target(url, session=None, log=None):
    """解析用户给的任意一个 B站网址 / 号，返回：

        {'kind': 'video'|'list', 'url': 规范化地址, 'bvid': BV 号或 None,
         'aid': av 号或 None, 'page': 分P序号或 None, 'raw': 用户原始输入}

    · 单个视频：/video/BVxxx、/video/av123、直接粘 BV 号、带 ?p=2 的分P
    · 列表页：合集 / 收藏夹 / UP主空间 / 搜索结果页 / 稍后再看等（后面用浏览器展开）
    """
    raw = (url or "").strip()
    if not raw:
        raise ValueError("网址是空的")

    # 允许直接粘贴 BV 号 / av 号 / 无协议的 b23.tv
    if not raw.lower().startswith("http"):
        if raw.lower().startswith("b23.tv"):
            raw = "https://" + raw
        elif BV_RE.fullmatch(raw):
            bvid = BV_RE.fullmatch(raw).group(1)
            return {"kind": "video", "url": f"https://www.bilibili.com/video/{bvid}",
                    "bvid": bvid, "aid": None, "page": None, "raw": url}
        elif AV_RE.fullmatch(raw):
            aid = int(AV_RE.fullmatch(raw).group(1))
            return {"kind": "video", "url": f"https://www.bilibili.com/video/av{aid}",
                    "bvid": None, "aid": aid, "page": None, "raw": url}
        else:
            raise ValueError(f"看不懂这个地址：{raw}")

    full = expand_short_url(raw, session=session, log=log)
    low = full.lower()

    # 分P：?p=3 / &p=3
    page = None
    m = re.search(r"[?&]p=(\d+)", full)
    if m:
        page = int(m.group(1)) or None

    bvid = parse_bvid(full)
    m_av = AV_RE.search(full)
    aid = int(m_av.group(1)) if (m_av and not bvid) else None

    if "/video/" in low and (bvid or aid):
        return {"kind": "video", "url": full, "bvid": bvid, "aid": aid,
                "page": page, "raw": url}

    # 列表类：合集、收藏夹、UP主、搜索、稍后再看、频道……
    list_marks = ("/list/", "medialist", "/favlist", "collectiondetail", "/series",
                  "/space/", "/search", "/channel/", "watchlater", "/readlist")
    if any(k in low for k in list_marks):
        return {"kind": "list", "url": full, "bvid": None, "aid": None,
                "page": None, "raw": url}

    # 认不出但确实是 B站 的地址：也按列表页试试（用浏览器去抓里面的视频链接）
    if "bilibili.com" in low:
        return {"kind": "list", "url": full, "bvid": None, "aid": None,
                "page": None, "raw": url}

    raise ValueError(f"这不是 B站地址：{raw}")


def split_urls(text):
    """把一大段粘贴的文本切成网址列表：换行、空格、中文逗号都算分隔符，井号开头当注释。"""
    out = []
    for line in re.split(r"[\r\n]+", text or ""):
        line = line.split("#", 1)[0]
        for piece in re.split(r"[,\uFF0C;\s]+", line):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


def read_urls_from_file(path):
    """从文本文件读网址清单（每行一个，# 开头是注释）。"""
    with open(path, "r", encoding="utf-8") as f:
        return split_urls(f.read())


# ============================== 解析 ==============================

def fetch_media_info(url, headers=None, log=print, session=None, quality=None):
    """解析一个视频页，返回：

        {
          'title':     视频标题,
          'bvid':      BV 号,
          'cid':       分P id（走页面正则回退时为 None）,
          'video_url': 视频流地址,
          'audio_url': 音频流地址,
          'quality':   清晰度描述,
          'source':    'api' 或 'html',
        }

    网址带 ?p=3 时只解析第 3 个分P。
    """
    headers = headers or make_headers()
    session = session or requests
    bvid = parse_bvid(url)
    aid = None
    page_no = None
    try:
        target = parse_target(url, session=session)
        bvid, aid, page_no = target["bvid"], target["aid"], target["page"]
    except Exception as e:
        log(f"[解析] 网址解析有问题（{e}），按普通 BV 链接处理")

    if bvid or aid:
        try:
            view = api_view(bvid, headers=headers, session=session, aid=aid)
            pages = view.get("pages") or []
            cid = view["cid"]
            title = view.get("title") or bvid or f"av{aid}"

            if page_no and page_no > 1:
                if page_no > len(pages):
                    raise BiliError(
                        f"这个视频只有 {len(pages) or 1} 个分P，没有第 {page_no} 个")
                part = pages[page_no - 1]
                cid = part.get("cid") or cid
                part_title = (part.get("part") or "").strip()
                title = f"{title}_P{page_no}" + (f"_{part_title}" if part_title else "")
                log(f"[解析] 已定位到第 {page_no} 个分P：{part_title or '（无分P标题）'}")

            dash = api_playurl(view.get("bvid") or bvid, cid, headers=headers,
                               session=session, quality=quality)
            video, audio = pick_streams(dash)
            quality_desc = describe_stream(video)
            log(f"[解析] 官方接口解析成功：{quality_desc}")
            return {
                "title": title,
                "bvid": view.get("bvid") or bvid,
                "cid": cid,
                "video_url": video["baseUrl"],
                "audio_url": audio["baseUrl"],
                "quality": quality_desc,
                "source": "api",
            }
        except BiliError:
            # 确定性错误直接抛：换解析方式也没用，硬套回退会下错东西
            raise
        except Exception as e:
            log(f"[解析] 官方接口失败（{e}），改用页面正则回退...")

    # ---- 回退：页面正则（旧版老办法，没有清晰度信息也可能已失效）----
    if page_no and page_no > 1:
        raise BiliError(f"官方接口不可用，而页面回退不支持分P，无法保证拿到第 {page_no} 个")
    log("[解析] 正在抓取视频页面...")
    r = session.get(url, headers=headers, timeout=TIMEOUT)
    urls = extract_media_from_html(r.text)
    if len(urls) < 2:
        raise RuntimeError("页面里找不到 baseUrl（B站改版、或该视频需要登录/有权限限制）")
    title = extract_title_from_html(r.text) or bvid or "video"
    log(f"[解析] 页面正则回退成功：共 {len(urls)} 条地址，取第 1 条作视频、最后 1 条作音频")
    log("[解析] 注意：这条路径拿不到清晰度信息，也不保证音视频配对正确")
    return {
        "title": title,
        "bvid": bvid,
        "cid": None,
        "video_url": urls[0],
        "audio_url": urls[-1],
        "quality": "未知（页面正则）",
        "source": "html",
    }


# ============================== 下载 / 合并 ==============================

def http_download(url, dest, headers=None, log=print, session=None, label="文件"):
    """带进度的流式下载，返回写入字节数。"""
    session = session or requests
    headers = headers or make_headers()
    done = 0
    last_pct = -1
    with session.get(url, headers=headers, stream=True, timeout=TIMEOUT) as r:
        r.raise_for_status()
        total = int(r.headers.get("Content-Length") or 0)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(CHUNK):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if total:
                    pct = done * 100 // total
                    if pct >= last_pct + 10:
                        last_pct = pct
                        log(f"    {label} {pct}%  ({done / 1048576:.1f}/{total / 1048576:.1f} MB)")
    size_mb = done / 1048576
    log(f"    {label} 下载完成：{size_mb:.1f} MB")
    return done


def merge_av(video_path, audio_path, out_path, log=print, audio_codec="aac"):
    """用 moviepy 把分离的音视频合并成 mp4（画面直拷 + 音频转 aac）。"""
    from moviepy import AudioFileClip, VideoFileClip

    video = VideoFileClip(video_path)
    audio = AudioFileClip(audio_path)
    try:
        merged = video.with_audio(audio) if hasattr(video, "with_audio") else video.set_audio(audio)
        merged.write_videofile(out_path, audio_codec=audio_codec, logger=None)
    finally:
        for clip in (audio, video):
            try:
                clip.close()
            except Exception:
                pass


def download(url, save_path, headers=None, log=print, info=None,
             session=None, keep_temp=False):
    """下载单个视频：解析 → 下音视频流 → 合并 → 清理临时文件。返回输出文件路径。"""
    headers = headers or make_headers()
    save_path = save_path or DEFAULT_SAVE_PATH
    os.makedirs(save_path, exist_ok=True)

    info = info or fetch_media_info(url, headers=headers, log=log, session=session)
    tag = info.get("bvid") or "tmp"
    video_tmp = os.path.join(save_path, f"{tag}_video.m4s")
    audio_tmp = os.path.join(save_path, f"{tag}_audio.m4s")
    out_path = unique_path(os.path.join(save_path, f"{sanitize_filename(info.get('title'))}.mp4"))

    log(f"[标题] {info.get('title')}")
    log(f"[清晰度] {info.get('quality')}")
    try:
        log("[下载] 视频流 ...")
        http_download(info["video_url"], video_tmp, headers=headers, log=log,
                      session=session, label="视频")
        log("[下载] 音频流 ...")
        http_download(info["audio_url"], audio_tmp, headers=headers, log=log,
                      session=session, label="音频")
        log("[合并] 正在合并音视频 ...")
        merge_av(video_tmp, audio_tmp, out_path, log=log)
        log(f"[完成] {out_path}")
        return out_path
    finally:
        if not keep_temp:
            for p in (video_tmp, audio_tmp):
                try:
                    if os.path.exists(p):
                        os.remove(p)
                except OSError:
                    pass


# ============================== 搜索 ==============================

def search_videos(keyword, limit=None, log=print, page=None, sort_newest=True,
                  on_page=None):
    """用 DrissionPage 打开浏览器搜索关键词，返回视频链接列表（绝对地址）。

    page    传入则复用这个浏览器对象；不传就自己开一个、用完关掉。
    on_page 可选回调，在浏览器还开着时被调用一次（download_range 用它取登录态）。
    """
    own = page is None
    if own:
        from DrissionPage import ChromiumPage
        log("[搜索] 正在打开浏览器 ...")
        page = ChromiumPage()
    try:
        page.get(HOME)
        time.sleep(1.5)
        page.ele('xpath://input[@class="nav-search-input"]').input(keyword)
        page.ele('xpath://div[@class="nav-search-btn"]').click()
        time.sleep(1.5)

        tab = page.latest_tab
        if sort_newest:
            # 第二个按钮 = "最新发布"
            tab.eles('xpath://div[@class="search-condition-row"]/button')[1].click()
            time.sleep(1.5)

        cards = tab.eles('xpath://div[@class="bili-video-card__wrap"]/a')
        urls = []
        for card in cards:
            u = absolute_url(card.attr("href"))
            if u and u not in urls:
                urls.append(u)
        if limit:
            urls = urls[:limit]
        log(f"[搜索] 关键词「{keyword}」命中 {len(cards)} 个结果"
            + (f"，只取前 {len(urls)} 个" if limit else ""))

        if on_page is not None:
            try:
                on_page(page)
            except Exception as e:
                log(f"[搜索] on_page 回调出错（不影响搜索）：{e}")
        return urls
    finally:
        if own:
            try:
                page.quit()
            except Exception:
                pass


# ============================== 按网址下载 ==============================

def collect_video_links(page_url, log=print, page=None, scroll_rounds=5,
                        limit=None, sleep=time.sleep):
    """打开一个列表页（合集 / 收藏夹 / UP主主页 / 搜索结果），滚动加载后抓出所有视频链接。

    用浏览器抓而不是调接口，是为了绕开 WBI 签名和登录限制 —— 页面能看到什么就能抓到什么。
    """
    own = page is None
    if own:
        from DrissionPage import ChromiumPage
        log("[列表] 正在打开浏览器 ...")
        page = ChromiumPage()
    try:
        page.get(page_url)
        sleep(2.0)
        links, seen = [], set()
        for _ in range(max(1, scroll_rounds)):
            try:
                page.scroll.to_bottom()
            except Exception:
                pass
            sleep(0.8)
            for a in page.eles('xpath://a[contains(@href, "/video/BV")]'):
                full = absolute_url(a.attr("href"))
                bvid = parse_bvid(full)
                if not bvid:
                    continue
                key = (bvid, re.search(r"[?&]p=(\d+)", full).group(1)
                       if re.search(r"[?&]p=(\d+)", full) else None)
                if key in seen:
                    continue
                seen.add(key)
                links.append(full)
            if limit and len(links) >= limit:
                break
        if limit:
            links = links[:limit]
        log(f"[列表] 从该页面抓到 {len(links)} 个视频")
        return links
    finally:
        if own:
            try:
                page.quit()
            except Exception:
                pass


def expand_target(target, log=print, page=None, limit=None):
    """把一个"列表类"网址展开成视频目标列表。"""
    links = collect_video_links(target["url"], log=log, page=page, limit=limit)
    if not links:
        raise RuntimeError("这个页面里没抓到视频链接（可能没登录、页面结构变了或它不是列表页）")
    return [{"kind": "video", "url": u, "bvid": parse_bvid(u), "aid": None,
             "page": None, "raw": u} for u in links]


def download_by_urls(urls, save_path=None, log=print, headers=None, quality=None,
                     session=None, start=None, end=None, expand=True, page=None,
                     auto_play=False, open_folder=False, player=None):
    """按网址下载 —— 每个网址可以是单个视频（含 ?p= 分P），也可以是合集/收藏夹/UP主主页。

    网址里可以直接粘 BV 号、av 号、b23.tv 短链。
    start / end 只对"展开出多个视频"的网址生效（单个视频链接不受影响）。
    """
    save_path = save_path or DEFAULT_SAVE_PATH
    headers = headers or make_headers()
    os.makedirs(save_path, exist_ok=True)

    targets = []
    errors = []
    for raw in urls:
        try:
            targets.append(parse_target(raw, session=session, log=log))
        except Exception as e:
            errors.append(f"{raw} -> {e}")
            log(f"[网址] 跳过无法识别的输入：{raw}（{e}）")
    if not targets:
        raise RuntimeError("没有可用的 B站网址")

    log(f"[任务] 共 {len(targets)} 个网址    保存到：{save_path}")
    if not check_login(headers, log=log):
        log("[提示] 未登录也能下载，只是清晰度最高 480P；")
        log("       想要高清请运行： python bili_login.py  （弹出浏览器登录，自动保存）")

    result = {"total": 0, "ok": 0, "failed": 0, "files": [], "errors": errors,
              "urls": len(targets)}

    for seq, target in enumerate(targets, 1):
        log("")
        log("=" * 52)
        kind = "单个视频" if target["kind"] == "video" else "列表页"
        log(f"[网址] 第 {seq}/{len(targets)} 个（{kind}）：{target['url']}")
        log("=" * 52)

        try:
            if target["kind"] == "video":
                items = [target]
            elif expand:
                items = expand_target(target, log=log, page=page)
            else:
                raise RuntimeError("这是列表页，但当前不允许展开（expand=False）")
        except Exception as e:
            result["failed"] += 1
            result["errors"].append(f"{target['url']} -> {e}")
            log(f"[失败] 这个网址处理不了：{e}")
            continue

        if start or end:
            a = max(1, min(start or 1, len(items)))
            b = max(a, min(end or len(items), len(items)))
            if (a, b) != (1, len(items)):
                log(f"[范围] 这个网址共 {len(items)} 个视频，取第 {a} ~ {b} 个")
            items = items[a - 1:b]

        for item in items:
            result["total"] += 1
            try:
                info = fetch_media_info(item["url"], headers=headers, log=log,
                                        session=session, quality=quality)
                out = download(item["url"], save_path, headers=headers, log=log,
                               info=info, session=session)
                result["ok"] += 1
                result["files"].append(out)
            except Exception as e:
                result["failed"] += 1
                result["errors"].append(f"{item['url']} -> {e}")
                log(f"[失败] {item['url']}")
                log(f"       原因：{e}")

    log("")
    log(f"[结束] 成功 {result['ok']} 个，失败 {result['failed']} 个")
    if result["files"]:
        if open_folder:
            bili_player.open_folder_and_report(result["files"][0], log=log)
        if auto_play:
            bili_player.play_and_report(result["files"][0], player=player, log=log)
    return result


# ============================== 一站式批量下载 ==============================

def download_range(keyword, start=1, end=3, save_path=None, log=print,
                   headers=None, quality=None, session=None,
                   auto_play=False, open_folder=False, player=None):
    """搜索关键词，下载第 start ~ end 个结果。命令行和网页都调它。

    auto_play   下载完后用播放器打开第一个视频
    open_folder 下载完后打开保存目录
    player      指定播放器路径（配合 auto_play；为空则用系统默认关联程序）

    返回 {'total': 结果总数, 'ok': 成功数, 'failed': 失败数,
          'files': [输出文件...], 'errors': [错误描述...]}
    """
    save_path = save_path or DEFAULT_SAVE_PATH
    headers = headers or make_headers()
    log(f"[任务] 关键词：{keyword}")
    log(f"[任务] 范围：第 {start} ~ {end} 个    保存到：{save_path}")
    os.makedirs(save_path, exist_ok=True)

    # 搜索时浏览器已经打开，顺手把浏览器里的登录态取出来并落盘（Cookie 过期也不用手抄）
    grabbed = {}

    def grab_session(page):
        got = grab_login_session(page, log=log)
        if got:
            grabbed.update(got)

    urls = search_videos(keyword, log=log, on_page=grab_session)
    if grabbed.get("cookie"):
        headers = make_headers(cookie=grabbed["cookie"],
                               user_agent=grabbed.get("user_agent"))

    if not check_login(headers, log=log):
        log("[提示] 未登录不影响下载，只是清晰度最高 480P；")
        log("       想要高清请运行： python bili_login.py  （弹出浏览器登录，自动保存）")

    total = len(urls)
    if total == 0:
        raise RuntimeError("没有搜到任何视频（可能是页面结构变了或网络异常）")

    first = max(1, min(start, total))
    last = max(first, min(end, total))
    if (first, last) != (start, end):
        log(f"[范围] 结果只有 {total} 个，已自动调整为第 {first} ~ {last} 个")

    picked = urls[first - 1:last]
    result = {"total": total, "ok": 0, "failed": 0, "files": [], "errors": []}

    for offset, url in enumerate(picked):
        index = first + offset
        log("")
        log("=" * 52)
        log(f"[进度] 第 {index}/{total} 个（本次第 {offset + 1}/{len(picked)} 个）")
        log("=" * 52)
        try:
            info = fetch_media_info(url, headers=headers, log=log,
                                    session=session, quality=quality)
            out = download(url, save_path, headers=headers, log=log,
                           info=info, session=session)
            result["ok"] += 1
            result["files"].append(out)
        except Exception as e:
            result["failed"] += 1
            result["errors"].append(f"{url} -> {e}")
            log(f"[失败] {url}")
            log(f"       原因：{e}")

    log("")
    log(f"[结束] 成功 {result['ok']} 个，失败 {result['failed']} 个")

    if result["files"]:
        if open_folder:
            bili_player.open_folder_and_report(result["files"][0], log=log)
        if auto_play:
            bili_player.play_and_report(result["files"][0], player=player, log=log)
    return result
