**告警 webhook** 让业务员看到红灯横幅时一键报错给程序员所在的群（钉钉/飞书/Discord）。

> 这个 URL 存到 `chrome.storage.local`，本机所有项目共用；不入服务器 DB。

## 怎么拿（任选其一）

### 钉钉（最常用）

1. 钉钉群 → 群设置 → **机器人** → **添加机器人** → **自定义**
2. 机器人名字随便（比如「vibe-niuma 告警」）
3. **安全设置** 三选一（推荐「加签」，签名 vibe-niuma 自动算）：
   - **自定义关键词**：填 `vibe-niuma`（告警 title 必含此词 → 默认满足）
   - **加签**：复制出来的 `SEC...` 串
   - **IP 段**：你的 ECS 公网 IP
4. 完成后复制 webhook URL：`https://oapi.dingtalk.com/robot/send?access_token=XXX`
5. **如果选了「加签」**，把 SEC... 也粘到 vibe-niuma 设置里

### 飞书

1. 飞书群 → 设置 → **群机器人** → **添加机器人** → **自定义机器人**
2. 复制 webhook URL：`https://open.feishu.cn/open-apis/bot/v2/hook/XXX`
3. 安全设置可选「关键词 `vibe-niuma`」

### Discord

1. Discord 服务器 → 频道设置 → **整合** → **Webhooks** → **新建 Webhook**
2. 复制 URL：`https://discord.com/api/webhooks/XXX/YYY`

## 业务员点了「报告给程序员」会发什么

```
⚠️ vibe-niuma 业务员上报

【业务员留言】
点保存按钮就报红了

最近失败 CR: cr-12345

【浏览器 console 错误】
TypeError: x is undefined at saveOrder.tsx:42

上报时间: 2026-05-18T16:30:00.000Z
```

## 跳过会怎样

跳过的话，业务员看到红灯只能截屏丢群里手动描述。功能稳定后再回 Settings 配也 OK。
