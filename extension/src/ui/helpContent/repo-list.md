**关联 GitHub 仓库** 是告诉 orchestrator：业务员后续每条 CR 改的代码要 push 回哪个仓库。

## 每行三个字段

### 1. 仓库 URL

两种格式都行：
- `https://github.com/your-org/your-repo.git`（**推荐**，PAT 走 HTTPS）
- `git@github.com:your-org/your-repo.git`（需要 ECS 上配 SSH key，麻烦，少用）

### 2. 主分支（mainBranch）

业务员的代码最终要合到哪个分支。常见：
- `main`（现代默认）
- `master`（老 repo）
- `develop`（有些团队用 git-flow）

### 3. 业务员专用分支（targetBranch）

**vibe-niuma 不直接 push main**。业务员的每条 CR 先合到这个隔离分支，
程序员 review 后再合 main → PR。默认值 `vibe-niuma/dev` 99% 情况不用改。

## 流程示意

```
业务员每条 CR
     ↓ 合并
vibe-niuma/dev  ←── 业务员永远只动这里
     ↓ 程序员手动 review + 合
main ←── 上线分支
```

## 跳过会怎样

跳过的话，扩展只在 ECS 本地代码工作，不 push 到 GitHub。
后续到 Settings 加 repo 再触发 sync-repos 同步过去。

## 常见报错

- **"sync 失败：HTTP 401"** —— PAT 失效或权限不够，回上一步换个 PAT
- **"sync 失败：HTTP 404"** —— URL 拼错（少了 `.git`？大小写不对？）
- **"sync 失败：git ref not found"** —— mainBranch 写错（`main` vs `master`）
