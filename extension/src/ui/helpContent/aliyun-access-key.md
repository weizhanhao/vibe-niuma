**阿里云 Access Key** 是 vibe-niuma 替你开 ECS 用的钥匙 —— 粘进来后只活在浏览器里，**永远不入服务器 DB、不进 log、不存 chrome.storage**。开完即丢，下次开新机要重新粘。

> ⚠️ **强烈建议用「RAM 子账号」的 key，不要用主账号的。** 子账号权限可控，泄露能秒撤；主账号 key 一旦泄露就是全账号风险。

## 怎么拿（5 分钟，第一次跑）

### 1. 建 RAM 子账号

1. 登 [ram.console.aliyun.com](https://ram.console.aliyun.com)（主账号身份）
2. 左侧「身份管理」→「用户」→「创建用户」
3. 登录名比如 `vibe-niuma-bot`，**访问方式必勾「OpenAPI 调用访问」**（不要勾控制台访问，子账号不需要登 console）
4. 创建完页面会给你 **AccessKey ID + AccessKey Secret** —— 这俩**只显示这一次**，关掉就再也看不到，立刻复制好

### 2. 给子账号授权（只给开 ECS 用的，最小权限）

1. 上一步用户详情页 → 「权限管理」→「新增授权」
2. 系统策略里勾这几个：
   - `AliyunECSFullAccess` —— 开/关/查 ECS
   - `AliyunVPCFullAccess` —— 配安全组（开 22 / 9000 / 5100-5199 端口）
   - `AliyunEIPFullAccess` —— 分配公网 IP

> 🤔 嫌全权限不放心？可以建个**自定义策略**只放 `ecs:RunInstances` / `ecs:DescribeInstances` / `ecs:AllocatePublicIpAddress` / `ecs:AuthorizeSecurityGroup` / `ecs:DeleteInstance` 五个 action。但首次跑建议先用 Full，跑通了再收紧。

### 3. 粘到这里

- **Access Key ID**：以 `LTAI` 开头的一串字母数字
- **Access Key Secret**：32 位字符串（区分大小写）
- **区域**：选离你/你客户最近的，例如华东杭州（`cn-hangzhou`）。开完不能改区域

## 安全 FAQ

- **key 安全吗？** —— 只走 HTTPS 到你自己的 bootstrap orchestrator，业务员关页面就清。
- **会扣多少钱？** —— 默认开 `ecs.t6-c1m4.large`（4C8G）按量付费，**¥0.27 / 小时**左右；vibe-niuma 跑起来 + 你的项目跑起来一个月 ≈ ¥200 上下。觉得不用了直接到 ECS 控制台释放实例。
- **失败了怎么办？** —— vibe-niuma 自动 rollback（开了的实例自动删），不会留下扣费实例。
- **要不要预付费包月？** —— 第一次先按量付费试试，跑稳了再到 ECS 控制台改成包月（更便宜）。

## 常见报错

- **`The specified access key is forbidden by RAM`** —— 子账号没勾「OpenAPI 调用访问」，回 step 1 重建
- **`OperationDenied.NoPermission`** —— 授权不够，回 step 2 加 ECS/VPC/EIP 三个 Full Access
- **`InvalidAccountStatus.NotEnoughBalance`** —— 阿里云账户余额不足，去 [账户中心](https://expense.aliyun.com) 充值（最低 ¥100）
- **`InstanceTypeNotSupported`** —— 选的区域没这个规格，换个区域（推荐 `cn-hangzhou` / `cn-shanghai`）
