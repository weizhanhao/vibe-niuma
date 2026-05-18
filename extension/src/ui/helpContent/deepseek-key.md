**DeepSeek API key** 用于 dev runner（改代码的 AI）和澄清模型。这是 vibe-niuma 主力调用对象，没填就跑不通。

1. 到 [platform.deepseek.com](https://platform.deepseek.com) 注册账号（手机号 / Google 登录均可）
2. 进控制台 → 左边「API Keys」→「创建新密钥」
3. 复制 `sk-...` 开头的字符串粘到这里
4. 首次注册一般送少量额度试用；后续按 token 计费，[计费页](https://platform.deepseek.com/usage) 看明细

**验证**：填好保存后新建一条 CR；sidebar 出现「澄清中」「编码中」流式输出就说明 key 通了。如果一直卡「澄清中」不动，多半是 key 失效或没充值 —— 回控制台对账户余额。
