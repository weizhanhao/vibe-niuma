# Plan 11 — 业务员零运维端到端：多仓 + GitHub 闭环 + 阿里云全自动 + 健康自愈

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把业务员从「ssh 上 ECS / 手动 git clone / 改 systemd / 看到改动不知道怎么回 GitHub」全部解放出来。改造完业务员只做不可代劳的事（注册 / 充值 / GitHub 授权点几下）+ 业务判断（说话 / 看 / 合）。

---

## 现状 vs 目标

**现状（v0.6.0）**：
- 业务员要 ssh ECS、手动 `git clone` 每个目标仓到 `/opt/vibe-niuma/<repo>/`
- 合并完代码只在 ECS 本地 main 上，不回推 GitHub → 程序员 `git pull` 拉不到
- 服务挂了业务员看到「连不上」一脸懵，要 ssh 跑 `systemctl restart`
- 阿里云 ECS 是手动买的，安全组手动开

**目标（v0.7.0）**：
- 业务员在扩展 wizard 里：粘 DeepSeek key → 粘阿里云 access key → 用 GitHub OAuth 一键授权 → 填要管的 N 个仓库 URL → 等 8 分钟 → 进主界面就能改业务
- CR 合并 → 自动 push 到 `vibe-niuma/dev` → 自动维护 long-running PR → 程序员从 PR 看每条业务改动
- 服务挂了 → 扩展显示「服务挂了，已通知程序员」+ 一键钉钉/飞书告警
- ECS / 安全组 / 容器全是阿里云 OpenAPI 起的，业务员不碰控制台

---

## 边界

**IN scope（这次做）**：
- 多仓 schema + 自动 clone + PAT 鉴权
- `vibe-niuma/dev` 作为业务员合并 target（默认值，wizard 可改）
- CR 合并 → push 回 GitHub
- 自动维护 long-running PR（`vibe-niuma/dev → main`）
- 阿里云 OpenAPI 集成（自动开 ECS + EIP + 安全组）
- 健康监控面板 + 「报告给程序员」按钮
- **加密的配置导出 / 导入（passphrase + AES-GCM，业务员换电脑 / 同事接手用）**
- 重写 CreateProjectPanel 串接成完整 wizard

**OUT scope（明确不做）**：
- 多云适配（只阿里云，腾讯云/AWS 留下版本）
- 自定义域名 + HTTPS（用 sslip.io 兜底，`http://<ip>.sslip.io` 解析回 IP）
- 多角色 RBAC（仍单角色业务员）
- 跨业务员的 dev 分支并发锁（撞了走 GitHub conflict，让用户处理）
- GitHub 之外的代码托管（GitLab / Gitee / 自建留下版本）
- 业务员公司内 SSO / LDAP
- **中心化的 OAuth / server-side 用户账号系统** —— vibe-niuma 是私有化部署，每客户独立 ECS，没有「我们」这边的中心服务可以放 OAuth 回调 / 配置 backup

---

## 技术栈

沿用现有 + 新增：
- 阿里云 OpenAPI：[`alibabacloud-ecs20140526`](https://pypi.org/project/alibabacloud-ecs20140526/)（官方 SDK，含 Sign V3）
- GitHub API：标准 REST v3 + `httpx`（已用），无新依赖
- PR 创建 / 维护：GitHub `/repos/{owner}/{repo}/pulls` 端点
- HTTPS 兜底：[Caddy](https://caddyserver.com/) auto-https + sslip.io（一个 docker 容器）
- 告警 webhook：钉钉 / 飞书 / Discord 三选，统一 `webhook_url + secret` 字段

---

## File Structure（新增 / 改动）

```
orchestrator/
  src/orchestrator/
    aliyun_provisioner.py            # 新：阿里云 OpenAPI 客户端（开机 + 安全组 + EIP）
    github_client.py                 # 新：PAT 鉴权 + clone + push + PR API 封装
    multi_repo_sync.py               # 新：POST /projects/{id}/sync-repos 实现
    target_branch.py                 # 新：targetBranch 创建 + push 逻辑
    auto_pr.py                       # 新：long-running PR 维护
    health.py                        # 新：扩展 /health 输出更多字段
    alert.py                         # 新：钉钉 / 飞书 webhook 推送
    main.py                          # 加：/projects/{id}/sync-repos /provision-ecs /alert 等端点
    models.py                        # 加：repos JSON 列、deploy_id、alert_webhook_url
    schemas.py                       # 加：RepoConfig、ProvisionRequest、AlertConfig
    pipeline.py                      # 改：merge_to_main 改成 merge_to_target_branch + push
    git_manager.py                   # 改：targetBranch-aware（不再全合到 main）
  tests/
    test_aliyun_provisioner.py
    test_github_client.py
    test_multi_repo_sync.py
    test_target_branch_merge.py
    test_auto_pr.py
    test_health_endpoint.py

extension/
  src/
    lib/
      config.ts                      # ConfigSchema 加 repos / targetBranch / githubPAT / aliyunAccessKey
      projects.ts                    # 配置 server-side backup hook
      github-oauth.ts                # 新：GitHub OAuth client（可选，PAT 路径仍保留）
      aliyun-client.ts               # 新：扩展端调阿里云 provision 的 REST 包装
    ui/
      panels/
        CreateProjectPanel.tsx       # 大改：6 步 wizard 串接全部新流程
        SettingsPanel.tsx            # 加：repos 编辑 / GitHub 重新授权 / 告警 webhook
        HealthDashboard.tsx          # 新：服务健康面板（业务员能看）
      components/
        RepoListEditor.tsx           # 新：repo URL 列表 + 主分支编辑
        AliyunProvisionStep.tsx      # 新：access key 收集 + 进度条
        GitHubAuthStep.tsx           # 新：OAuth / PAT 选择
        ReportToDevButton.tsx        # 新：一键打包错误上下文 + 推 webhook
      helpContent/
        aliyun-access-key.md         # 新：教业务员怎么在阿里云 RAM 创 access key（含截图）
        github-pat.md                # 新：教业务员怎么创 PAT（含截图）
        target-branch.md             # 新：解释 vibe-niuma/dev 是什么
        alert-webhook.md             # 新：教业务员配钉钉/飞书机器人

deploy/
  caddy/
    Caddyfile                       # 新：sslip.io 自动 HTTPS 配置
    docker-compose.yml              # 新：caddy 容器
  systemd/
    vibe-niuma-caddy.service        # 新（可选，如果不走 docker）
```

---

## 数据契约

### Project.config 加字段（chrome.storage.local）

```ts
interface Config {
  // 原有 ...

  // 新：多仓配置
  repos: Array<{
    url: string;                 // git@github.com:org/repo.git 或 https://...
    mainBranch?: string;         // 默认 "main"
    targetBranch?: string;       // 默认 "vibe-niuma/dev"
  }>;

  // 新：GitHub 授权（PAT only —— 私有化部署不做 OAuth）
  githubAuth?: {
    token: string;               // chrome.storage.session（关浏览器即清）
    username?: string;           // 用 GET /user 校验时拿到，给业务员看「✓ 已绑定 @xxx」
  };

  // 新：阿里云授权
  aliyunAuth?: {
    accessKeyId: string;
    accessKeySecret: string;     // chrome.storage.session 短期；wizard 完成后只留 RAM 自动续 token
    regionId: string;            // 默认 cn-hangzhou
    instanceId?: string;         // provision 完成后填回
  };

  // 新：告警
  alertConfig?: {
    kind: 'dingding' | 'feishu' | 'discord';
    webhookUrl: string;
    secret?: string;
  };

  // 新：自动 PR 开关
  autoOpenPR?: boolean;          // 默认 true
}
```

### Orchestrator DB 加字段

```sql
ALTER TABLE system_config ADD COLUMN repos JSON DEFAULT NULL;
ALTER TABLE system_config ADD COLUMN aliyun_deploy_id VARCHAR(64);
ALTER TABLE system_config ADD COLUMN alert_webhook_url TEXT;
ALTER TABLE system_config ADD COLUMN alert_kind VARCHAR(16);
ALTER TABLE system_config ADD COLUMN auto_open_pr BOOLEAN DEFAULT TRUE;
```

`githubPAT` / `aliyunAccessKeySecret` **不入 orchestrator DB**（只在扩展 `chrome.storage.session`，关浏览器即清）。需要持久的就走「配置导出文件 + passphrase 加密」（见 M4），业务员自己保管文件 + 密码。

---

## 4 个 Milestone（按 ROI 顺序）

### M1 · 多仓 + GitHub 闭环（最大爽点，先做）

**目标**：业务员配项目时填仓库 URL + 主分支，CR 合并后自动 push 回 GitHub `vibe-niuma/dev` 并维护 PR。**Stage 4 + Stage 6 直接消失**。

**前置**：现有 multi_repo.py 两阶段原子 merge（Plan 8 已做）

**Tasks**:

- [ ] **T1**: `Config` schema 加 `repos: Array<{url, mainBranch?, targetBranch?}>` + zod 校验 + migration 兼容旧 single-path 项目
- [ ] **T2**: `chrome.storage.session` 收 GitHub PAT；wizard 新加 `GitHubAuthStep.tsx`（先支持 PAT，OAuth 留 M4）
- [ ] **T3**: orchestrator 新增 `github_client.py` —— `clone_with_pat(url, target_dir, pat)` / `push(repo, branch, pat)` / `create_or_update_pr(repo, head, base, body, pat)`
- [ ] **T4**: orchestrator 新增 `multi_repo_sync.py` + 端点 `POST /projects/{id}/sync-repos`，幂等：已存在的仓 `git fetch + reset --hard origin/<main>`，没有的 clone bare 到 `<cache>/<project_id>/<hash>.git/`，再 `git worktree add` 出可写目录
- [ ] **T5**: `target_branch.py` —— 首次见 repo 时若 remote 没有 `vibe-niuma/dev` 就 `checkout -b vibe-niuma/dev <mainBranch> && push -u origin`
- [ ] **T6**: 改 `pipeline.py` 的 merge 逻辑：从 `cr/<id>` rebase 到 `vibe-niuma/dev`（不是 main）→ ff-merge → push origin
- [ ] **T7**: 新 `auto_pr.py` —— 业务员合每条 CR 后调 GitHub API：找已存在的 `vibe-niuma/dev → main` PR 就在 body 追加这条 CR 的 spec / 截图 link；没有就 create
- [ ] **T8**: 改 `CreateProjectPanel.tsx` 加 step「关联 GitHub 仓库」—— `RepoListEditor` 组件，业务员填 URL + 选主分支
- [ ] **T9**: 改 `main-demo.sh` 从 `vibe-niuma/dev` 重建（不再从 main）
- [ ] **T10**: 集成测试：起 fake GitHub server，跑「业务员配 2 个仓 → sync → 起 CR → merge → 验证 push 进 dev + PR 自动 update」

**Done definition**：业务员在扩展 wizard 填 GitHub PAT + 2 个仓 URL，点完成。再起一条 CR，merge 成功后 `gh pr view` 能看到 PR，body 里有这条 CR 的需求描述。

**估时**：6-7 天

---

### M2 · 阿里云全自动部署（Stage 3B 消失）

**目标**：业务员粘阿里云 access key → 扩展用 OpenAPI 自动开 ECS + 配安全组 + 上软件 + 起服务 + 健康检查。**业务员永远不开阿里云控制台**（除了首次注册 + RAM 创 access key）。

**前置**：M1 完成（business case 验证：「我能填仓库」之后再让他「我能开 ECS」）

**Tasks**:

- [ ] **T11**: 装 `alibabacloud-ecs20140526` SDK + 写 `aliyun_provisioner.py`：`provision_ecs(access_key, region, spec) → {instance_id, public_ip, password}` 含 CreateInstance + AllocatePublicIpAddress + AuthorizeSecurityGroup（22 / 9000 / 5100-5199）
- [ ] **T12**: orchestrator 端点 `POST /admin/provision-ecs` —— 用业务员提供的 access key 调上面那个函数；轮询 `DescribeInstances` 等实例 Running
- [ ] **T13**: 扩展新 wizard step `AliyunProvisionStep.tsx`：业务员粘 access key + 选地域 → 进度条「开机中... 配安全组... 装 Docker... 部署 orchestrator... 验证 11/11 项」
- [ ] **T14**: 实例起来后自动 ssh 跑 `ecs-bootstrap.sh`（access key 临时存 chrome.storage.session，部署完销毁）
- [ ] **T15**: 失败回滚：`DeleteInstance` 清理半成品 + 提示业务员「实例已退款，请检查 access key 权限」
- [ ] **T16**: 帮助内容 `aliyun-access-key.md` —— 截图教业务员在 RAM 创 access key + 给 `AliyunECSFullAccess` 权限的 6 步
- [ ] **T17**: 集成测试：mock 阿里云 SDK 跑「access key → 拿到 public_ip → orchestrator 部署成功」

**Done definition**：业务员粘 access key、选「华东 1（杭州）」、点开始 → 10 分钟后扩展显示「✓ 部署完成」+ 自动填好 orchestrator URL + admin token。

**估时**：4-5 天

---

### M3 · 健康面板 + 自愈 + 告警

**目标**：服务异常时业务员**看得见 + 知道下一步**。Stage 7 从 ❌ 降到 ⚠（业务员仍不能修，但能正确报告）。

**Tasks**:

- [ ] **T18**: orchestrator `/health` 扩展输出：`{services: {orchestrator, llm_proxy, mysql, main_demo}, last_cr_at, last_error, uptime_seconds}`
- [ ] **T19**: 扩展 `HealthDashboard.tsx` —— 业务员在主界面右上角看健康指示灯，绿色就 hover 才出详情，黄/红色直接弹横幅
- [ ] **T20**: systemd unit 全部 `Restart=on-failure RestartSec=10s`；docker `--restart=unless-stopped` 全检查；不到位的补
- [ ] **T21**: orchestrator `alert.py` —— 钉钉/飞书/Discord 三家 webhook 通用接口；签名计算（钉钉用 HMAC-SHA256）
- [ ] **T22**: 扩展 `ReportToDevButton.tsx` —— 业务员看到红色横幅时点「报告给程序员」：自动 POST 给 orchestrator `/alert`，body 含最近一条失败 CR id + 截图 + 浏览器 console 错误 + 业务员留言
- [ ] **T23**: wizard 加 step 配 alert webhook URL（可跳过）
- [ ] **T24**: 集成测试：模拟 mysql 挂掉 → 验证 /health 报 red + 业务员点按钮 → 钉钉收到带上下文的告警

**Done definition**：在测试 ECS 上手动 `docker stop vibe-niuma-mysql` → 30s 内扩展显示红色横幅「数据库挂了」+「报告给程序员」按钮 → 点完钉钉群收到含 CR id + 截图的告警。

**估时**：3-4 天

---

### M4 · 配置导出 / 导入 + Wizard 串接 + HTTPS

**目标**：把 M1-M3 串成一个 6-step wizard，业务员一气呵成。**业务员换电脑 / 同事接手**走加密的配置导出文件（**不做 server-side backup 也不做 OAuth** —— 私有化部署没有中心服务可以放）。

**核心设计 · 加密导出方案**：

- 业务员在 SettingsPanel 点「📤 导出配置」→ 弹框让他设一个 passphrase（明确警告「密码丢了文件就废了」）→ 用 Web Crypto API 走 **PBKDF2 (300k iters, SHA-256) 派生 key → AES-GCM 加密 JSON** → 下载 `.vbn-config.enc` 文件
- 文件内容（加密前的 JSON）：项目列表 + repo URL + targetBranch + alert webhook URL + **全部 secret**（DeepSeek key / GitHub PAT / admin token / 阿里云 access key + secret / webhook secret）
- 业务员在 CreateProjectPanel 点「📥 导入配置」→ 选 `.vbn-config.enc` 文件 → 输 passphrase → AES-GCM 解密 → 校验 JSON schema → 写 chrome.storage（local 部分）+ chrome.storage.session（secret 部分）→ 弹 toast「已导入 N 个项目」
- 密码错 / 文件损坏 / schema 不匹配：明确错误提示，不要静默吞
- 加密用 Web Crypto API（浏览器原生，无新依赖）

**Tasks**:

- [ ] **T25**: 写 `extension/src/lib/config-export.ts` —— PBKDF2 + AES-GCM 加密 / 解密 utility，单元测试覆盖：正确密码能解、错误密码报清晰错误、文件被改一个字节即解密失败（GCM 自带完整性校验）
- [ ] **T26**: SettingsPanel 加「📤 导出配置」按钮 + 密码框 + 警告文案 + 下载 `.vbn-config.enc`；CreateProjectPanel 加「📥 导入配置」按钮 + 文件选择 + 密码框 + 解密 + schema 校验 + 写 storage
- [ ] **T27**: 重写 `CreateProjectPanel.tsx` 成 6 步 wizard：项目名 → DeepSeek key → 阿里云 access key（M2）→ GitHub PAT（M1）→ 配仓库（M1）→ 部署 + 验证（M2 + M3）。每步可前进/后退保留字段；任一 step 挂了能从上一步继续
- [ ] **T28**: 每个 wizard step 配套 `helpContent/*.md` —— 截图教学：DeepSeek 哪买、阿里云 RAM 怎么创 access key、GitHub PAT 怎么生（选哪些 scope）、export/import 怎么用
- [ ] **T29**: HTTPS via Caddy + sslip.io：deploy 起一个 caddy 容器，`http://<ip>.sslip.io:5147` → 自动签 Let's Encrypt 证书 → 业务员预览站浏览器不再警告
- [ ] **T30**: e2e 集成测试：在测试阿里云账户跑完整 wizard +「业务员 A 导出 → 业务员 B 导入 → 看到同样的项目列表」+「预览 URL 是 https://...sslip.io 且证书有效」

**Done definition**：
1. 同事 A 全新 Chrome 走完 6 步 wizard，能改一条 CR 合到自己 fork
2. 同事 A 导出配置（设密码 `hunter2`）→ 把 `.vbn-config.enc` 发给同事 B
3. 同事 B 全新 Chrome 装扩展，点「📥 导入配置」选文件、输 `hunter2` → 进主界面直接能改业务（不用重走 wizard）

**估时**：5 天

---

## Total

**15-18 工作日**（M4 从 7d 减到 5d，去掉 server-side backup + GitHub OAuth）。M1 / M2 / M3 / M4 之间松耦合，M1 做完就可发 v0.6.5，M2 完发 v0.6.6 等等。**不必等全做完才发布**。

---

## 关键 Risk

| Risk | Mitigation |
|---|---|
| 阿里云 OpenAPI 调用失败（业务员 access key 没权限）| `DescribeRegions` 先调一下当 healthcheck；失败给具体错误「请给 access key 加 AliyunECSFullAccess」+ 截图引导 |
| GitHub PAT 过期 / 权限被撤 | sync-repos 失败时返 401 → 扩展提示「PAT 失效，请重新授权」+ 跳 wizard step |
| sslip.io 服务挂了（依赖单个第三方）| 兜底走纯 IP（弹个「浏览器会警告 http，要 https 请自备域名」） |
| `vibe-niuma/dev` 撞客户已有的 `vibe-niuma/dev` 分支 | wizard 默认值 + 业务员可改，撞了就提示「改成 `<贵司前缀>/dev`」 |
| 业务员合 CR 太快，PR 描述累积成 100 条 | PR body 滚动窗口（最近 20 条），老的折叠成 summary line |
| 多业务员同时 push `vibe-niuma/dev` 冲突 | rebase 失败时回滚 + 告诉业务员「先稍等，另一位同学正在合」 |

---

## 验收 / Smoke

跑完 M1-M4 后端到端验收：

1. 同事 A 全新 Chrome，装扩展。
2. 填 DeepSeek key（5 分钟自己注册 + 充值）。
3. 跟 wizard 走完 6 步（access key + GitHub auth + 仓库 URL）。
4. 等 8 分钟看到「✓ 部署完成」。
5. 进主界面，打开同事 A fork 的 demo repo 的样板间。
6. 框选 + 「徽章改红色」 → 等 90s → 看预览 → 满意点合并。
7. 同事 A 打开 GitHub.com 看自己 fork：`vibe-niuma/dev` 分支多了 1 个 commit，`vibe-niuma/dev → main` 有个 long-running PR，body 写了业务员说的需求。
8. 同事 A 在 SettingsPanel 点「📤 导出配置」、设密码 `hunter2`、把 `.vbn-config.enc` 发给同事 B；同事 B 全新浏览器装扩展、点「📥 导入配置」选文件输 `hunter2` → 进主界面看到同事 A 的所有项目 + 直接能改业务（不用重走 wizard）。
9. 模拟服务挂掉 → 扩展显示红色横幅 → 点告警 → 钉钉群收到通知。

全部 ✓ → 发 v0.7.0。

---

## 不在本 Plan 的事

- 多云 / 多代码托管（Plan 12+）
- 自定义域名 / 企业级 SSO / RBAC（Plan 13+）
- 业务员协作锁 / 分支并发管理（Plan 14+）
- AGENTS.md per-repo 而非 per-project（Plan 12+）
- 自动 CI / 测试集成（依赖客户测试栈，Plan 15+）
