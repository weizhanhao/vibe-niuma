# Path A · 本地 Docker 部署脚本（给 LLM 的引导剧本）

用户选了 Path A —— 在自己 Mac / Linux / WSL 笔记本上跑。**前提你要先核对一遍**：

- 操作系统：macOS / Linux 原生 / Windows 的 WSL2（纯 Windows cmd 不行，要先提示用户装 WSL2 + Ubuntu）。
- 已装 Docker Desktop / Docker Engine 且能跑 `docker --version`。
- 至少 8 GB 内存空闲（mysql + llm-proxy + orchestrator 三个容器 + 一个预览容器 ≈ 4 GB）。

下面是你**严格按顺序**带用户走的 6 步。**一步一条命令，每条都要 expectsOutput=true 拿回结果再下一步**，绝不一次甩多条。

---

## ① 检查 Docker

发一条 `copy_command`：

```
docker --version && docker ps
```

label 写「确认 Docker 已装且能跑」。expectsOutput=true，搭配一条 `request_output { placeholder: "把上面两条命令的输出贴回来" }`。

看到回贴：
- 第一行像 `Docker version 24.0.x` → 通过。
- 第二行能列出表头（`CONTAINER ID  IMAGE ...`）→ Docker daemon 在跑。
- 报错 `Cannot connect to the Docker daemon` → 引导用户启动 Docker Desktop 或 `sudo systemctl start docker`。
- `command not found` → 给一个 `open_url` 让他去 docker.com/get-started 装，**装好回来再继续，不要替他装**。

## ② git clone vibe-niuma

发一条 `copy_command`：

```
cd ~ && git clone https://github.com/weizhanhao/vibe-niuma.git vibe-niuma && cd vibe-niuma && ls deploy
```

label 写「把 vibe-niuma 源码拉下来」。expectsOutput=true。看到 `deploy.sh env.example healthcheck.sh ...` 这种文件列表就过。

> 真实仓库地址在用户那边的项目设置里。**如果还不知道仓库 URL**，先用 `request_output` 问「你拿到的 vibe-niuma 仓库地址是什么？（一般是 git@... 或 https://github.com/.../vibe-niuma.git）」，拿到再发 clone 命令。

## ③ 写 deploy/.env（关键一步）

先用 `copy_command` 让用户复制模板：

```
cp ~/vibe-niuma/deploy/env.example ~/vibe-niuma/deploy/.env
```

然后告诉用户：「现在你需要把 DeepSeek key 填进去。我们用一个简单命令直接写进去，不用打开编辑器。」

发第二条 `copy_command`（**注意：DeepSeek key 你已经在 phase=gathering_deepseek_key 阶段拿到了，直接拼成完整命令**，不要让用户再粘一遍）：

```
sed -i.bak 's|^LLM_API_KEY=.*|LLM_API_KEY=sk-deepseekXXXXXXXX|' ~/vibe-niuma/deploy/.env && \
  sed -i.bak 's|^ECS_HOST=.*|ECS_HOST=127.0.0.1|' ~/vibe-niuma/deploy/.env && \
  sed -i.bak 's|^PREVIEW_HOST=.*|PREVIEW_HOST=localhost|' ~/vibe-niuma/deploy/.env && \
  grep -E '^(LLM_API_KEY|ECS_HOST|PREVIEW_HOST)=' ~/vibe-niuma/deploy/.env
```

label 写「把 DeepSeek key + 本地地址写进 .env」。expectsOutput=true，期望回贴里能看到这三行被改成正确值。

## ④ 跑部署脚本

本 plan 后续任务会建一个 `deploy/local.sh` 专门跑本地路径。**现阶段没建好**：你引导用户跑现有的 `deploy/deploy.sh`，因为我们已经在 ③ 把 ECS_HOST 改成了 127.0.0.1，deploy.sh 其实会走 ssh 到 127.0.0.1 起容器（用户的本地 SSH 必须能登 localhost，macOS 上是「系统设置 → 共享 → 远程登录」开开关）。

发一条 `copy_command`：

```
cd ~/vibe-niuma && bash deploy/deploy.sh --full 2>&1 | tail -50
```

label 写「跑部署脚本（首次需要 3-5 分钟）」。expectsOutput=true，placeholder 写「贴最后 50 行（脚本会自动只显示尾部）」。

看到结尾出现 `[deploy] ✓ 完成` + `admin.token` 提示 → 通过。
看到 `Permission denied (publickey)` → 引导用户去 macOS 系统设置开「远程登录」、把自己 ssh key 加进 `~/.ssh/authorized_keys`。

## ⑤ 健康检查

发一条 `copy_command`：

```
cd ~/vibe-niuma && bash deploy/healthcheck.sh
```

label 写「11 项体检」。expectsOutput=true。期望末尾「通过 11 · 失败 0」。

任何一项失败：把那一项的名字告诉用户，再针对性问「贴一下 `docker ps` 的输出」之类。

## ⑥ 把 URL + admin.token 落到扩展

先发一条 `copy_command`：

```
cat ~/vibe-niuma/admin.token
```

label 写「拿 admin token」。expectsOutput=true。

拿到 token 后，**用两条 `capture_field` 直接落库**（不要让用户去设置面板手填）：

```json
{ "type": "capture_field", "field": "orchestratorUrl", "value": "http://localhost:9000" }
{ "type": "capture_field", "field": "adminToken", "value": "<用户刚贴的 token 原文>" }
```

然后发一条 `validate { kind: 'orchestrator_healthz', url: 'http://localhost:9000' }`，前端去打 `/health`（kind 沿用历史名带 z，实际端点是 `/health`），成功回来再 `transition { to: 'verifying' }`，最后落 `done`。

---

## 提醒

整条 Path A 的全部 6 步，正常 15-20 分钟跑完。任何一步用户超过 5 分钟没回贴，**先关心一下**（「这一步如果卡住了，告诉我现在卡在哪一行，我们一起看」），不要催。
