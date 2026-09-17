# bili-workspace

个人工作区。目前主要放一个自用的 **B站视频下载器**。

## 📦 bili-downloader

搜索关键词或直接粘贴链接，把 B 站视频下载到本地，自动把分离的视频轨和音频轨合并成 MP4。
命令行和网页两个界面，共用同一套核心实现。

**主要能力**

- 关键词搜索批量下载，按序号范围取（如「第 1~5 个」）
- 按链接下载：BV号 / av号 / b23.tv 短链 / 带 `?p=` 的分P
- 合集、收藏夹、UP主主页、频道、搜索结果页自动展开成列表，整包下载
- 一键获取登录态，解锁 1080P（不登录上限 480P）
- 下载完直接调用系统播放器打开 / 在资源管理器里定位文件
- 重复下载自动加 `(2)(3)` 后缀，不覆盖已有文件

**快速开始**

```bat
pip install -r requirements.txt
run_login.bat                  :: 获取登录态（可选，推荐；不登录也能下）
python main.py 关键词            :: 搜索并下载
python main.py --url "BV1xx"   :: 或者直接给链接
python web_app.py              :: 网页界面 → http://127.0.0.1:5000
```

**环境要求**：Python 3.10+、Chrome（搜索和获取登录态要用浏览器驱动）

👉 **[完整文档：命令行参数、配置项、实现说明 →](bili-downloader/README.md)**

## 📒 Git 学习笔记

这个仓库同时是我练 Git 的地方。[`git-notes.md`](git-notes.md) 里记了几个基础概念——
三个区域、救命命令、以及"凭据一旦提交就是永久泄露"这类踩过的坑。

## 仓库结构

```
bili-workspace/
├── README.md              ← 你在这里
├── LICENSE                MIT
├── git-notes.md           Git 学习笔记
├── app/                   预留目录（暂时为空）
└── bili-downloader/       B站视频下载器
    ├── README.md          ← 完整文档
    ├── bili_config.py     配置：请求头、清晰度、保存路径
    ├── bili_core.py       核心：搜索 / 解析 / 下载 / 合并
    ├── bili_login.py      一键获取登录态
    ├── bili_player.py     调用播放器 / 定位文件
    ├── main.py            命令行入口
    ├── web_app.py         网页入口
    ├── selftest.py        自检脚本（离线 143 项）
    ├── requirements.txt
    └── legacy/            项目前身，不参与运行
```

## 许可

[MIT](LICENSE) —— 可自由使用、修改、分发。

## 声明

下载内容的版权归原作者和平台所有。本项目仅供个人学习和技术研究使用，
请遵守 B 站用户协议，勿用于批量抓取或二次分发。
