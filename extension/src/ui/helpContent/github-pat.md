**GitHub PAT（Personal Access Token）** 让 orchestrator 帮你 push 到 GitHub。

> 🔒 PAT 只活在浏览器 session 内存里 —— 关浏览器即清，不入 chrome.storage.local，不入服务器 DB。

## 怎么拿

### Fine-grained PAT（推荐，权限可控）

1. 登 GitHub → 右上角头像 → **Settings**
2. 左侧最下：**Developer settings** → **Personal access tokens** → **Fine-grained tokens**
3. **Generate new token**
4. 几个字段：
   - **Token name**：随便，比如 `vibe-niuma-bot`
   - **Expiration**：90 天起步，跑稳了改 1 年
   - **Repository access**：**Only select repositories** → 勾你这个项目要绑的几个 repo
5. **Repository permissions**：
   - `Contents`：**Read and write**（push 用）
   - `Pull requests`：**Read and write**（auto-PR 用）
   - 其它都 No access
6. **Generate token** —— 出来的 `github_pat_...` 字符串只显示这一次，立刻复制

### Classic PAT（兼容备用）

不能限定 repo，但所有 repo 都自动有权限。简单粗暴：
- Developer settings → Personal access tokens → **Tokens (classic)**
- Generate new token (classic)
- Scopes 只勾 `repo`（含子项 `repo:status` / `public_repo` / `repo_deployment` 之类）
- 拿到 `ghp_...` 串

## 跳过会怎样

跳过的话，扩展不会触发 sync-repos（不 push 回 GitHub），只在 ECS 本地工作。
后续到 Settings 里随时可以补 PAT。
