# Git 学习笔记

这个仓库同时是我练 Git 的地方。下面记的是**真正理解了的**东西，不是命令清单。

## 三个区域 —— git 唯一的难点

几乎所有的困惑，都来自没分清这三个区域。

```
  工作区              暂存区              仓库
(Working Dir)  --add--> (Staging)  --commit--> (Repository)
  你能编辑的文件        "购物车"           永久历史 (.git/)
```

**`git status` 是你和"把密钥提交到 GitHub"之间唯一的防线。** 永远在 `add` 和 `commit` 之前看一眼它。

### 暂存区为什么要存在？

这是所有人都会问、大多数教程都不答的问题。

设想你正在改一个 bug，顺手又修了个错别字，还试了点新功能。**现在你只想提交 bug 修复。**

没有暂存区，你只有两个烂选择：全部提交（混在一起），或者手动把其他改动撤掉。
有暂存区，你能挑——`git add` 那个文件，`git commit`，剩下的留在工作区下次再提交。

**一句话：暂存区让你能"挑着提交"，并且在提交前先看一眼要提交什么。**

## 日常循环

```bash
git status                  # 我在哪？改了什么？
git diff                    # 具体改了什么内容？
git add <文件>               # 放进暂存区
git commit -m "说明"         # 拍快照
git log --oneline           # 看历史
```

**提交说明写"为什么"，不是"改了什么"**（改了什么 `git diff` 会告诉你）。

## 救命命令

| 情况 | 命令 |
| --- | --- |
| 改乱了文件，想回到上次提交 | `git restore <文件>` ⚠️ 改动会永久消失 |
| `add` 错了，想拿出来但保留改动 | `git restore --staged <文件>` |
| `commit` 了但想撤销（保留改动） | `git reset --soft HEAD~1` |
| 彻底回到上一次提交 | `git reset --hard HEAD~1` ⚠️⚠️ **真的会删代码** |

**终极保险是 `git reflog`** —— 它记录 HEAD 去过的每个位置，包括你以为弄丢的提交。

> **只要 commit 过，代码就几乎不可能真的丢。** 这就是为什么要"能跑就提交一次"。

## 忽略规则要验证，不要靠肉眼读

`.gitignore` 写完，用 `git check-ignore -v <路径>` 验一遍：

```bash
git check-ignore -v node_modules/anything.js
# 命中规则: 20:node_modules/    node_modules/anything.js
```

**规则顺序、`!` 取反、目录末尾的斜杠，全是坑。** 别靠读。

一份 `.gitignore` 一般分五类：

| 类别 | 例子 |
| --- | --- |
| 敏感信息 | `config.ini`、`.env`、`cookies.txt`、`*.key` |
| 依赖与构建产物 | `node_modules/`、`dist/`、`__pycache__/`、`*.o` |
| 运行日志与临时文件 | `*.log`、`*.m4s`、`cache.json` |
| 编辑器配置 | `.vscode/`、`.idea/` |
| 系统文件 | `Thumbs.db`、`.DS_Store` |

**判据：能由其他文件重建的东西 = 产物 = 排除。** 源码、配置、锁文件（`package-lock.json`）= 保留。

## 空目录不会被跟踪

**Git 跟踪的是文件，不是目录。空目录在 git 眼里根本不存在** —— 提交后它会消失。

标准的应付办法是往里放个占位文件，约定俗成叫 `.gitkeep`（这不是 git 的功能，纯粹是社区约定）。

## 凭据一旦提交，就是永久泄露

这条比上面所有条加起来都重要。

`git rm` 只删最新版本，**历史里那份还在**。别人 `git log -p` 一样能翻出来。
真正清掉要重写历史（`git filter-repo`），代价很高。

**所以唯一的可靠办法是不让它进去。** 两个具体做法：

- 配置外置：`config.example.ini`（入库）+ `config.ini`（`.gitignore`）
- 代码里从环境变量读：`os.environ.get("BILI_COOKIE", "")`

## 上传前检查清单

```bash
git status                    # 看清单里有没有意外文件
git diff --staged             # 看清楚要提交什么
git grep -i "password\|token\|secret\|SESSDATA"   # 搜凭据
```

**我在这个仓库里踩过的坑**：一个项目的 6 个提交里有 5 个含硬编码 Cookie。
最后的处理是**不带 `.git` 重新迁移**——历史丢掉了，但换来了从第一个提交起就干净。

## 已经练过的命令

- [x] `git init` / `git config` / `git remote add`
- [x] `git status` / `git diff` / `git log`
- [x] `git add` / `git commit` / `git push`
- [x] `git restore` / `git reset`
- [x] `git check-ignore -v`
- [x] `gh auth login` / `gh repo view` / `gh repo edit`

## 还没碰过的（等真遇到再说）

`branch` / `merge` / `rebase` / `stash` / `tag` / 冲突解决。

**这些现在学只会忘。**
