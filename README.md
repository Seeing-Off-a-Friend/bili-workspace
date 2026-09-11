# bili-workspace

学习 Git 与 GitHub 的练习仓库。

## 这个仓库是干什么的

不是用来放某个具体项目的，而是**练 git 本身**。

所以内容会不断变化——这正是它的用途。

## 学习进度

- [x] `git init` —— 把普通目录变成 git 仓库
- [x] `git config user.name / user.email` —— 设置提交者身份
- [x] `git remote add origin` —— 关联远程仓库
- [ ] `git add` —— 把改动放进暂存区
- [ ] `git commit` —— 提交，形成一次快照
- [ ] `git log` —— 查看提交历史
- [ ] `git push` —— 推送到 GitHub
- [ ] `git diff` —— 查看具体改了什么
- [ ] `git restore` —— 撤销工作区的改动

## 三个区域

这是 git 唯一的难点，也是理解一切命令的基础：

```
  工作区              暂存区              仓库
(Working Dir)  --add--> (Staging)  --commit--> (Repository)
  你能编辑的文件        "购物车"           永久历史 (.git/)
```

**暂存区存在的理由**：让你能"挑着提交"。

比如你改了 5 个文件，但只想提交其中属于同一个改动的 2 个——先 `git add` 那 2 个，再 `git commit`，剩下的留在工作区下次再提交。

## 笔记

（自己往下写，写你踩过的坑）
