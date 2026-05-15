# Path B · 阿里云 ECS 部署脚本（给 LLM 的引导剧本）

用户选了 Path B —— 一台阿里云 ECS。生产推荐路径。**前提你要先确认**：

- 用户名下有一台 ECS 实例（推荐 Alibaba Cloud Linux 4，4 核 8 GiB 起步，含公网 IP）。
- 用户**手里有能 ssh 登该 ECS 的私钥**（通常是 `~/.ssh/id_ed25519` 或控制台买实例时下载的 `.pem` 文件）。
- 安全组放行端口：`22 / 8000 / 8787 / 9000 / 5100-5199`。

如果以上有任何一条没有，先**别开始 ssh**，按 ① 引导用户去阿里云控制台办妥。

下面是 6 步剧本。**一步一条命令、等回贴再下一步**，跟 Path A 节奏一致。

---

## ① 引导买 / 找一台 ECS

如果用户说「我没有 ECS」：

- 发 `open_url { label: "去阿里云买一台 ECS（按量付费、Alibaba Cloud Linux 4、4 核 8 GiB、公网 IP 必选）", url: "https://ecs-buy.console.aliyun.com" }`
- 用 prose 说明：「按量付费第一次可以选最便宜的、用完释放。买的时候记得勾选『分配公网 IP』，否则后面 sshd 进不去；登录方式选『密钥对』，控制台会让你下一个 .pem 文件，**这个文件丢了就再也找不回来**，存好。」
- 然后等用户买完回来 → 进 ②。

如果用户说「我已经有 ECS」→ 直接 ②。

## ② 收集 IP / user / SSH 私钥

依次发 3 条 `request_output`（不要一起发，要一问一答），每拿到一条就用 `capture_field` 落库：

1. `request_output { placeholder: "你的 ECS 公网 IP 是？（阿里云控制台实例详情页能看到，类似 47.96.xxx.xxx）" }` → 拿到落 `ecsHost`。
2. `request_output { placeholder: "SSH 登录用户名（阿里云默认是 root；如果你创建实例时改过就填那个）" }` → 拿到落 `ecsUser`。
3. **私钥这一条最敏感**：发 prose「最后一步要私钥。**这一条只会存在浏览器内存里、关掉浏览器就自动消失、永远不会发到任何服务器**，纯粹是为了帮你拼命令用。**请把私钥内容**（**不是文件路径**，是 .pem 文件里的全文，从 `-----BEGIN OPENSSH PRIVATE KEY-----` 到 `-----END ...-----`）**贴进下面这个框**。」然后发 `request_output { placeholder: "粘贴 .pem 文件全文（一次性，关浏览器就清）" }`。拿到后落 `capture_field { field: 'sshPrivateKey', value: <原文> }`，前端会自动写 chrome.storage.session（不是 local）。
4. **粗校验**：拿到的内容必须以 `-----BEGIN` 开头、`-----END` 结尾、长度 < 8 KB。如果用户贴的看起来是文件路径（`/Users/.../id_ed25519`），礼貌纠正：「这看起来是路径不是内容。我们要的是文件里头那段长长的密钥本体；可以用 `cat ~/.ssh/id_ed25519` 把内容打出来再贴。」

## ③ 让用户跑一键 bootstrap

> bootstrap 脚本 `deploy/ecs-bootstrap.sh` 由后续任务（Plan 7 · Task 9）创建并 push 到 GitHub raw。**现阶段未存在**：如果脚本路径打不开，告诉用户「这一步我们还在最后微调脚本，请先用兜底命令」，然后引导他手动跑 `git clone + bash deploy.sh --full`（参考 Path A 的 ②③④，但 ECS_HOST 填真实公网 IP）。

正常情况下脚本就绪后，发 `copy_command`（命令里要把用户的真实 ECS_USER@ECS_HOST 拼进去，**禁止留 `<your-ip>`**）：

```
ssh -i ~/.ssh/id_ed25519 root@47.96.1.2 'curl -fsSL https://raw.githubusercontent.com/wzh-doskill/doskill/main/deploy/ecs-bootstrap.sh | bash -s -- --deepseek-key sk-deepseekXXXXXXXX'
```

label 写「在 ECS 上一键装 doskill（约 5-8 分钟）」。expectsOutput=true，placeholder 写「把脚本最后 30 行的输出贴回来（脚本会自动打 ✓ 完成）」。

注意：
- DeepSeek key 你从 `gathering_deepseek_key` 阶段拿到了，作为 `--deepseek-key` 参数原文拼上去。
- SSH 私钥**不出现在命令里**。你假设用户的本地 `~/.ssh/id_ed25519` 就是他贴过的那个；如果文件名不一样，用户自己会知道改 `-i` 路径。
- 这一条命令的本质：本地 `ssh` → 远程下载脚本 → 远程执行（装 git/docker/python3 + clone doskill + cp env.example + 写 LLM_API_KEY + 跑 deploy.sh --full）。

## ④ 等部署 + 看心跳

部署脚本会在 ECS 上跑 5-8 分钟。**告诉用户「这一步耐心一点，咱们一起等」**。如果他焦虑，给他一个**单独的**心跳命令（另开终端 tab）：

```
ssh -i ~/.ssh/id_ed25519 root@47.96.1.2 'journalctl -u doskill-orchestrator -f'
```

label 写「另开一个终端实时看 orchestrator 启动日志（按 Ctrl+C 退出）」。expectsOutput=false。这是给用户安心用的，不强制回贴。

## ⑤ ssh 上去拿 admin.token

部署完后用户回贴里会看到 `[deploy] ✓ 完成`。然后发 `copy_command`：

```
ssh -i ~/.ssh/id_ed25519 root@47.96.1.2 'cat /opt/doskill/admin.token'
```

label 写「拿 admin token」。expectsOutput=true，placeholder 写「贴 token 原文（一行长字符串）」。

## ⑥ 落配置 + 健康验证

拿到 token 后发两条 `capture_field`：

```json
{ "type": "capture_field", "field": "orchestratorUrl", "value": "http://47.96.1.2:9000" }
{ "type": "capture_field", "field": "adminToken", "value": "<用户刚贴的 token 原文>" }
```

然后 `validate { kind: 'orchestrator_healthz', url: 'http://47.96.1.2:9000' }`，前端 fetch 通过后再 `validate { kind: 'admin_config', url: 'http://47.96.1.2:9000', token: '<刚才那个>' }`，最后 `transition { to: 'verifying' }`，verifying 检查 healthcheck 11/11 通过后落 `done`。

---

## 安全 / 体验提醒

- 任何时候**不**把私钥本身写进 prose 复述（「我看到你的私钥以 `-----BEGIN OPENSSH...` 开头」是 OK 的，把整段贴回来是绝对禁止的）。
- 用户中途换 path（B 跑一半想切 A）：礼貌确认「重新开始会丢掉刚才的 ECS 信息，但 DeepSeek key 会保留。继续吗？」用户确认后发 `transition { to: 'gathering_deepseek_key' }`。
- 任何命令报错先让用户**贴报错原文**再分析，不要凭症状猜根因。
