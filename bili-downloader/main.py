# -*- coding: utf-8 -*-
"""命令行入口 —— 只负责解析参数，干活全交给 bili_core。

用法：
    python main.py                              # 搜索默认关键词，下载第 1~2 个
    python main.py 无极魔尊 -s 1 -e 3            # 指定关键词和范围
    python main.py 无极魔尊 -s 1 -e 3 -p "D:\\视频"
    python main.py --url "https://www.bilibili.com/video/BV1xx"          # 按网址下单个视频
    python main.py --url "https://www.bilibili.com/video/BV1xx?p=3"      # 只下第 3 个分P
    python main.py --url "BV1Bvbv6TEtT" --url "av123456"                 # 直接粘号也行
    python main.py --url "https://space.bilibili.com/123/video" -s 1 -e 5  # UP主主页取前 5 个
    python main.py --url-file 网址清单.txt                                # 从文件批量读网址
    python main.py --login                      # 打开浏览器登录，自动保存 Cookie
    python main.py --check                      # 只检查登录状态
"""

import argparse
import sys

import bili_config
import bili_core
import bili_player


def build_parser():
    p = argparse.ArgumentParser(
        description="B站视频批量下载：搜索关键词 → 按范围下载（自动搜索、解析、合并）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("keyword", nargs="?", default=bili_config.SEARCH_KEYWORD_DEFAULT,
                   help=f"搜索关键词（默认：{bili_config.SEARCH_KEYWORD_DEFAULT}）")
    p.add_argument("-s", "--start", type=int, default=None,
                   help="从第几个开始（搜索模式默认 1；网址模式默认全部/从第一个）")
    p.add_argument("-e", "--end", type=int, default=None,
                   help="下载到第几个（搜索模式默认 2；网址模式默认全部）")
    p.add_argument("-p", "--path", default=bili_config.DEFAULT_SAVE_PATH,
                   help=f"保存目录（默认：{bili_config.DEFAULT_SAVE_PATH}）")
    p.add_argument("-q", "--quality", type=int, default=bili_config.QUALITY,
                   help="清晰度：80=1080P, 64=720P, 32=480P, 16=360P（默认 80，实际能拿到多高看 Cookie）")
    p.add_argument("--url", action="append", default=[], metavar="网址",
                   help="按网址下载：单个视频（支持 ?p=3 分P、BV号、av号、b23.tv 短链），"
                        "也可以是合集/收藏夹/UP主主页等列表页。可重复传，也可逗号分隔")
    p.add_argument("--url-file", metavar="文件",
                   help="从文本文件读网址清单（每行一个，# 开头是注释）")
    p.add_argument("--check", action="store_true", help="只检查登录状态后退出")
    p.add_argument("--login", action="store_true",
                   help="打开浏览器登录并自动保存 Cookie（等于 python bili_login.py）")
    p.add_argument("--keep-temp", action="store_true", help="保留临时的 m4s 文件，方便排查问题")
    p.add_argument("--play", action="store_true", default=None,
                   help="下载完后用播放器打开第一个视频（默认读配置 BILI_AUTO_PLAY）")
    p.add_argument("--no-play", dest="play", action="store_false",
                   help="本次不自动播放（覆盖配置）")
    p.add_argument("--open-folder", action="store_true", default=None,
                   help="下载完后打开保存目录（默认读配置 BILI_AUTO_OPEN_FOLDER）")
    p.add_argument("--no-open-folder", dest="open_folder", action="store_false",
                   help="本次不自动打开目录（覆盖配置）")
    p.add_argument("--player", default=bili_config.PLAYER,
                   help='指定播放器，例如 --player "C:\\Program Files\\...\\PotPlayerMini64.exe"')
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    headers = bili_core.make_headers()

    # 命令行没显式指定时，用配置文件里的开关
    auto_play = bili_config.AUTO_PLAY if args.play is None else args.play
    open_folder = bili_config.AUTO_OPEN_FOLDER if args.open_folder is None else args.open_folder

    try:
        if args.login:
            import bili_login
            session = bili_login.interactive_login()
            return 0 if session else 1

        if args.check:
            ok = bili_core.check_login(headers)
            return 0 if ok else 1

        if args.url or args.url_file:
            urls = []
            for chunk in args.url:
                urls.extend(bili_core.split_urls(chunk))
            if args.url_file:
                urls.extend(bili_core.read_urls_from_file(args.url_file))
            print(f"[任务] 按网址下载，共 {len(urls)} 个网址")
            for u in urls:
                print(f"       · {u}")
            print(f"[任务] 保存到：{args.path}")
            result = bili_core.download_by_urls(
                urls, args.path, headers=headers, quality=args.quality,
                start=args.start, end=args.end,
                auto_play=auto_play, open_folder=open_folder, player=args.player,
            )
            for f in result["files"]:
                print(f"  ✔ {f}")
            for e in result["errors"]:
                print(f"  ✘ {e}")
            return 0 if result["failed"] == 0 else 2

        result = bili_core.download_range(
            args.keyword, args.start or 1, args.end or 2, args.path,
            headers=headers, quality=args.quality,
            auto_play=auto_play, open_folder=open_folder, player=args.player,
        )
        for f in result["files"]:
            print(f"  ✔ {f}")
        for e in result["errors"]:
            print(f"  ✘ {e}")
        return 0 if result["failed"] == 0 else 2

    except KeyboardInterrupt:
        print("\n[中断] 用户取消了下载")
        return 130
    except Exception as e:
        print(f"\n[错误] {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
