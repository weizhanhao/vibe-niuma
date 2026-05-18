**DashScope（阿里通义）API key** 用于视觉模型（看截图、定位框选的区域）。vibe-niuma 默认走通义千问 VL；没填就只能靠 URL 猜改动位置，准度差很多。

1. 到 [bailian.console.aliyun.com](https://bailian.console.aliyun.com) 用阿里云账号登录
2. 左侧「API Key」→「创建我的 API-KEY」
3. 复制 `sk-...` 字符串粘到这里
4. 首次开通需要在 [模型广场](https://bailian.console.aliyun.com/?productCode=p_efm) 给 `qwen-vl-plus` 点一下「开通」，否则调用会报「模型未开通」

**验证**：保存后新建一条带框选的 CR；sidebar log 里会看到「视觉模型回答中」流式行，说明 key 通了。报「ModelNotAvailable」就回模型广场去开通该模型。
