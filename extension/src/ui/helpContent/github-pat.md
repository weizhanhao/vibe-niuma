**代码托管 PAT（Personal Access Token）** 让 orchestrator 帮你 push 到代码托管平台。**支持 GitHub / Gitee / 阿里云云效**，按你用的平台拿一个就行。

> 🔒 PAT 只活在浏览器 session 内存里 —— 关浏览器即清，不入 chrome.storage.local，不入服务器 DB。

## GitHub

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

- Developer settings → Personal access tokens → **Tokens (classic)**
- Generate new token (classic) → Scopes 勾 `repo`
- 拿到 `ghp_...` 串

## Gitee

1. 登 https://gitee.com → 右上角头像 → **个人设置**
2. 左侧：**安全设置** → **私人令牌** → **生成新令牌**
3. 描述写 `vibe-niuma-bot`，权限勾 `projects` + `pull_requests` + `user_info`
4. 提交 → 拿到一串 token（**只显示一次**，立刻复制）

## 阿里云云效（Codeup）

1. 登 https://codeup.aliyun.com → 右上角头像 → **个人设置**
2. 左侧：**个人访问令牌** → **新建令牌**
3. 名称写 `vibe-niuma-bot`，**权限选 `api`**（代码读写 + PR 用）
4. **过期时间**：90 天 / 1 年都行
5. **生成** → 拿到令牌串，立刻复制（关页面就再也看不到）

## 跳过会怎样

跳过的话，扩展不会触发 sync-repos（不 push 回你的代码仓），只在 ECS 本地工作。
后续到 Settings 里随时可以补 PAT。
