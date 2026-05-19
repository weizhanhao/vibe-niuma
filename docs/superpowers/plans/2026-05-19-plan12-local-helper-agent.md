# Plan 12 — Local Helper + Agent Loop

**日期**：2026-05-19
**前置 Plan**：[Plan 11 业务员零运维](2026-05-18-plan11-business-user-zero-ops.md) 已落地（HEAD `faa660b`）
**目标**：业务员把 ECS 当水电 —— 永远不打开终端、不输 SSH 密码、不看命令长啥样

---

## 0. 先校准一下「拉代码」的归属

业务员实际上**早就不直接拉代码**了 —— Plan 11 落地的 `multi_repo_sync.py` + `auto_pr.py` + `git_manager.py` 全部在 orchestrator (ECS) 上跑，业务员合 CR 时代码自动 push 到 `vibe-niuma/dev` 分支。

**所以 Plan 12 不解决「拉代码」**（Plan 11 已经解决）。Plan 12 解决三件**业务员今天还得自己摸 ECS** 的事：

1. **首次 ECS 接入**：现在 IP + admin token 裸存 `chrome.storage.local`，弱安全；密码型登录场景下扩展拿不到 ssh key，业务员要手贴。
2. **CR 挂了不会自助 debug**：现在只能截图甩程序员，业务员不知道 `journalctl` / `docker logs` 长啥样。
3. **agent 只决策不动手**：扩展里的 AI 现在只能告诉业务员「你去 ssh 跑 X」，跑不动。Plan 12 后 agent 能真跑 ECS 命令，业务员只负责按授权弹框。

---

## 1. 终态业务员体验

```
首次安装：
  ① curl ... | bash               (30s 装 helper)
  ② 扩展弹「填 ECS IP + 密码」     (10s)
  ③ ✓ 已连通

日常用：
  ④ 扩展开着用 vibe-niuma         (helper 全程隐形)
  ⑤ CR 挂了                       agent 弹「想看 orchestrator 日志，授权？」
                                  业务员点 [允许] → 看到原因 → agent 弹
                                  「想重启服务修复，授权？」→ 点 [允许]
                                  → ✓ 已恢复

整个流程业务员心智里只剩「扩展 + 弹出来时点允许」两件事。
```

---

## 2. 架构

```
[业务员 Chrome 扩展]
   ↕ chrome.runtime.connectNative   (native messaging 本机管道)
[/usr/local/bin/vibe-niuma-helper]   (Go binary, ~5MB, launchd 自启)
   ↕ ssh                            (SSH 走公网)
[ECS]
```

**helper 责任**：
- 听 native messaging 消息
- 按消息里的 `project_id` 从 Keychain 取该项目的 ECS 凭据
- SSH 跑命令、流式回灌 stdout/stderr
- 写本地 audit log
- 不开 HTTP 端口、不联 vibe-niuma 服务器

**helper 不做**：
- 不存项目业务数据（那是 orchestrator 的事）
- 不缓存命令结果（每次问每次 SSH，stateless）
- 不实现 LLM 决策（LLM 跑在扩展里，helper 只是工具执行手）

---

## 3. Milestone 拆分（4 周）

### M1 — Helper Foundation（1 周）

让 chrome 扩展和本机 binary 能通讯，跑通最窄路径。

| Task | 说明 |
|------|------|
| T1 | Go 项目结构 + go.mod + 单 binary 构建（`helper/`）|
| T2 | Native messaging 协议实现（stdin/stdout 4-byte length prefix + JSON）|
| T3 | `golang.org/x/crypto/ssh` 跑 `ssh root@host 'cmd'`，stream stdout |
| T4 | `extension/src/lib/helper-client.ts`：`chrome.runtime.connectNative('com.vibe_niuma.helper')` 封装，Promise 接口 |
| T5 | 扩展加 `<HelperStatusBar>` 组件：检测 helper 装没装，没装就显示「装一下助手」按钮 |
| T6 | install.sh：下 binary + 写 native messaging manifest + 加可执行权限。**macOS only 先**。|
| T7 | 端到端 smoke test：扩展点按钮 → 扩展发 `{cmd: "echo ok"}` → helper SSH ECS 跑 echo → 回灌 "ok" 显在扩展 |

**M1 完成标志**：业务员能在扩展里点一个按钮，看到 ECS `uname -a` 输出。整套通讯链路 work。

### M2 — Credentials + Authorization（1 周）

把凭据存好、操作分级、加 audit。

| Task | 说明 |
|------|------|
| T8 | macOS Keychain 集成（Go `keychain` 包 / 直接调 `security` CLI）：按 project_id 存取 ECS IP + password / private key |
| T9 | 扩展加 `<ProjectECSCredentialsForm>`：新建项目时弹一次性的「填这个项目的 ECS 凭据」 |
| T10 | `helper-protocol.ts` 加 `project_id` 字段；helper 收到消息按它路由 keychain item |
| T11 | 命令分类器：helper 里硬编码 read / write / destructive 三档（regex 匹配 `journalctl|systemctl status|docker ps|cat|ls|grep` → read，其它默认 write，含 `rm|drop|delete|reset --hard|force` → destructive）|
| T12 | 扩展加 `<AuthorizationDialog>`：read 静默直跑、write 弹一次单选授权、destructive 弹二次确认（输入「确认删除」字符串）|
| T13 | helper 写 audit log `~/Library/Logs/vibe-niuma-helper/helper.log`：每行 timestamp + project_id + cmd + 谁授权的 + exit_code |
| T14 | 扩展加「查 helper 日志」入口（settings → 高级 → 显示最近 100 行 audit log）|

**M2 完成标志**：业务员配好凭据，agent 跑 `journalctl` 直接出结果；跑 `systemctl restart` 弹一次授权；跑 `rm -rf` 弹二次确认 + 输文字。

### M3 — Agent Loop（1 周）

扩展里把 LLM 接进来，让它能调 helper 跑工具。

| Task | 说明 |
|------|------|
| T15 | `extension/src/ai/tools/` 目录建起来：每个 tool 一个 ts 文件（schema + dispatcher）|
| T16 | Tool 列表 v1：`shell.exec` / `shell.exec_stream` / `orchestrator.api`（包好现有 REST）/ `file.read` |
| T17 | `extension/src/ai/agent-loop.ts`：LLM 输出含 `tool_call` 时调 dispatcher → 拿结果回灌 → 再调 LLM；最多 N 轮停止 |
| T18 | systemPrompt 加 tool 注册表 + few-shot 示例；prompt 里硬性约束 destructive 命令必须先 read 看再 write |
| T19 | 通过 llm-proxy 切到 Claude API（DeepSeek 工具调用稳定性差一档；llm-proxy 已经在 ECS 上跑）|
| T20 | `<GoalRunnerPanel>` 组件：业务员输入「目标」（"我项目挂了帮我看看"）→ 流式显示 agent 思考 + 每个 tool call + 授权弹框 + 结果 |
| T21 | 3 个预置 goal 模板（一键触发）：「看健康 + 自动诊断」「重启所有服务」「清掉本周失败的 CR」|

**M3 完成标志**：业务员输入「我项目挂了」→ agent 自动跑 reads 找原因（journalctl / docker logs / systemctl status）→ 报告根因 → 提建议（"重启 orchestrator？"）→ 业务员点 [允许] → 修复 → "✓ 已恢复"。

### M4 — 加固 + Windows（1 周）

| Task | 说明 |
|------|------|
| T22 | Windows 端口：Win32 native messaging 通过 Registry 注册；Windows Credential Manager 存密码；同一份 Go 代码 cross-compile |
| T23 | install.sh / install.ps1 加 `--uninstall` 子命令：删 5 个文件 + Keychain item |
| T24 | helper 自检 ping endpoint（扩展每 30s 探活）；helper 挂时扩展 banner 提示「helper 离线，点这里重启」|
| T25 | menubar 错误图标（只在异常时出现，正常 0 入口）|
| T26 | 自动更新：helper 启动时 GET `https://vibe-niuma.io/helper/latest.json`，新版本下载替换 |
| T27 | E2E 测试串：装 → 配 ECS → 跑 5 个 goal → 卸 |
| T28 | M4 整体验收：实测业务员从全新 Mac 到「自助 debug 一次真故障」完整闭环 |

**M4 完成标志**：业务员从下载到自助修一次故障，全程没碰过终端。

---

## 4. 风险 + 折中点

### 风险

| 风险 | 处理 |
|------|------|
| Chrome 升级时 native messaging 协议改 | manifest 锁 v0.1 + helper 兼容多版本 |
| 业务员装了 helper 但卡在 ECS 防火墙 | M1.T7 加连通自检（ssh + echo），失败 banner 显错 |
| LLM 幻觉乱跑 destructive 命令 | M2.T11 helper 端硬阻 destructive（regex 拦截就算 LLM 突破授权弹框也不真跑）|
| 业务员误点 [允许] 触发删数据 | M2.T12 destructive 必须输文字二次确认 |
| 多 chrome tab 同时跑 agent loop 互相打架 | helper 单 SSH session 串行，扩展端排队 |

### 暂不做（YAGNI）

- 跨平台多用户共享 helper（每个业务员一台机器 = 一个 helper）
- helper 自带 GUI 配置面板（凭据全在扩展里改）
- helper 跑非 ECS 目标（VPS / 自建机房）—— v2 加
- 不可逆操作的回滚 snapshot（业务员实际场景下不会用到，省）

### 折中点

- **同时只支持密码 + SSH key**：业务员要么填密码，要么贴私钥（粘 textarea），不支持 1Password / Bitwarden 等密码管理器集成（v2 再考虑）
- **agent 用 Claude 而不 DeepSeek**：成本 ~3x，但工具调用稳定 + 幻觉低（业务员误操作风险高，值得这个钱）

---

## 5. 验收标准（演给非程序员看）

找一个**没用过 vibe-niuma 的业务员**坐下来，给他：
- 一台干净 Mac
- 一台预先 provision 好的 ECS 的 IP + 密码
- 一句话：「帮我看下这个项目啥情况」

业务员应该能：
1. 30s 装 helper
2. 10s 填 ECS IP + 密码
3. 在扩展里输「我想看下这个项目啥情况」
4. 看到 agent 自动跑读类命令、报告结果
5. 看到 agent 发现错误、弹「想重启 X，授权？」
6. 点 [允许]，看到「✓ 已恢复」

**全程零终端、零文档查阅、零程序员介入。** 这是 v1 的 Pass 标准。

---

## 6. 时间表（粗估）

- 全职 4 周（160h）跑完 M1-M4。Go 写得熟、agent 调试有经验情况下。
- 可压缩到 3 周（120h）—— M4 的 Windows + 自动更新挪到 v1.1
- 拉到 6 周（240h）—— 加测试覆盖 / 加更精细的命令分类 / 加企业级审计

**建议起手节奏**：M1 单周突破（哪怕只 macOS）—— 通了再继续，没通先 debug。Plan 11 已经证明 vibe-niuma 后端能扛，前端 + helper 这层是新地块，先**实证**比规划重要。
