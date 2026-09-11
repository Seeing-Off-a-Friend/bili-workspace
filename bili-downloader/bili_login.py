# -*- coding: utf-8 -*-
"""一键获取并保存 B站登录态（Cookie + User-Agent）—— 给别人用最省事的入口。

用法：
    python bili_login.py                # 弹出浏览器，登录后自动检测并保存
    python bili_login.py --timeout 300  # 自定义等待秒数（默认 180）
    python bili_login.py --keep-open    # 保存后不关浏览器
    python bili_login.py --check        # 不开浏览器，只检查当前登录态

流程：
    1. 打开 Chrome 访问 bilibili.com
    2. 你在那个窗口里登录（扫码 / 密码 / 短信都行）
    3. 程序每 2 秒检测一次，发现 Cookie 里有 SESSDATA 就调 nav 接口确认
    4. 确认登录后，把 Cookie + 浏览器真实 UA + 昵称 + 大会员状态写进 bili_session.json
    5. 之后 main.py / web_app.py 会自动读它，不需要任何人手抄 Cookie

会话文件优先级高于 bili_config.py 里硬编码的 Cookie，所以旧 Cookie 过期也无所谓。
"""

import argparse
import sys
import time

import bili_config as config
import bili_core as core

DEFAULT_TIMEOUT = 180
POLL_INTERVAL = 2.0


def wait_for_login(page, timeout=DEFAULT_TIMEOUT, interval=POLL_INTERVAL, log=print,
                   sleep=time.sleep, clock=time.monotonic):
    """轮询等待用户登录。成功返回带账号信息的 session 字典，超时返回 None。"""
    deadline = clock() + timeout
    last_hint = clock()

    while True:
        session = core.session_from_page(page)
        if session:
            # 有 SESSDATA 还不够，得让服务器确认（可能是过期 Cookie 残留）
            acct = core.fetch_account(core.make_headers(cookie=session["cookie"],
                                                        user_agent=session.get("user_agent")))
            if acct and acct.get("is_login"):
                session["uname"] = acct.get("uname")
                session["vip"] = acct.get("vip")
                return session

        now = clock()
        if now >= deadline:
            log(f"[登录] 等待超过 {timeout:.0f} 秒仍未检测到登录")
            return None
        if now - last_hint >= 15:
            log(f"[登录] 仍在等待登录……（还剩 {deadline - now:.0f} 秒，"
                f"请在浏览器窗口里完成登录）")
            last_hint = now
        sleep(interval)


def interactive_login(timeout=DEFAULT_TIMEOUT, log=print, keep_open=False,
                      page=None, save=True, sleep=time.sleep, clock=time.monotonic):
    """打开浏览器 → 等用户登录 → 保存会话。返回 session 字典或 None。"""
    own = page is None
    if own:
        try:
            from DrissionPage import ChromiumPage
        except ImportError as e:
            log(f"[错误] 没有安装 DrissionPage：{e}")
            log("       请先执行： pip install DrissionPage")
            return None
        log("[登录] 正在打开浏览器 ...")
        page = ChromiumPage()

    try:
        page.get(core.HOME)
        sleep(1.5)
        log("=" * 54)
        log("请在弹出的浏览器窗口里登录 B 站（扫码 / 密码 / 短信都行）。")
        log(f"登录成功后程序会自动检测并保存，最多等 {timeout:.0f} 秒。")
        log("=" * 54)

        session = wait_for_login(page, timeout=timeout, log=log,
                                 sleep=sleep, clock=clock)
        if not session:
            log("[登录] 没检测到登录态，本次不保存（原有配置照旧可用）")
            return None

        who = session.get("uname") or "未知账号"
        log(f"[登录] 检测到登录：{who}（{'大会员' if session.get('vip') else '普通账号或未确认'}）")

        if save:
            path = config.save_session(session["cookie"], session.get("user_agent"),
                                       uname=session.get("uname"), vip=session.get("vip"),
                                       source="interactive-login")
            session["saved_path"] = path
            log(f"[登录] 已保存到：{path}")
            log("[登录] 之后运行 main.py / web_app.py 会自动使用它，不必再手抄 Cookie。")
        return session
    finally:
        if own and not keep_open:
            try:
                page.quit()
            except Exception:
                pass


def mask(cookie, keep=12):
    """日志里只露一小截 Cookie，避免整串凭据被打印到终端或网页日志里。"""
    if not cookie:
        return "(空)"
    return cookie[:keep] + f"...（共 {len(cookie)} 字符）"


def show_status(log=print):
    """不开浏览器，报告当前用的是哪份登录态，并联网确认。返回退出码。"""
    data = config.load_session()
    if data.get("cookie"):
        log(f"[会话] 已加载 {config.SESSION_FILE}")
        log(f"[会话] 账号：{data.get('uname') or '未知'}    "
            f"大会员：{'是' if data.get('vip') else '否/未确认'}    "
            f"保存于：{data.get('saved_at') or '未知'}")
        log(f"[会话] Cookie：{mask(data.get('cookie'))}")
    else:
        log(f"[会话] 没有 {config.SESSION_FILE}，正在用 bili_config.py 里硬编码的 Cookie")

    headers = core.make_headers()
    acct = core.fetch_account(headers)
    if acct is None:
        log("[会话] 联网确认失败（网络问题），无法判断登录态")
        return 2
    if acct.get("is_login"):
        log(f"[会话] 联网确认：已登录 {acct.get('uname')}"
            f"（{'大会员' if acct.get('vip') else '普通账号'}）—— 可以下高清晰度")
        return 0
    log("[会话] 联网确认：未登录或 Cookie 已过期 —— 只能拿 480P")
    log("[会话] 修复方法：运行 python bili_login.py 打开浏览器登录一次")
    return 1


def build_parser():
    p = argparse.ArgumentParser(
        description="一键获取并保存 B站登录态（Cookie + UA）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                   help=f"等待登录的秒数（默认 {DEFAULT_TIMEOUT}）")
    p.add_argument("--keep-open", action="store_true",
                   help="保存后不自动关闭浏览器窗口")
    p.add_argument("--check", action="store_true",
                   help="不开浏览器，只检查当前登录态")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.check:
        return show_status()
    session = interactive_login(timeout=args.timeout, keep_open=args.keep_open)
    return 0 if session else 1


if __name__ == "__main__":
    sys.exit(main())
