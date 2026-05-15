**Admin token** 是扩展和服务器之间的「钥匙」。所有 `/admin/*` 接口（改配置、查 API key）必须带这个 token，否则后端 401 拒绝。

1. 用 SSH 登录服务器：`ssh root@<你的-IP>`（Windows 用户推荐 [WindTerm](https://kingtoolbox.github.io) 或 [PuTTY](https://www.putty.org)）
2. 第一次启动 orchestrator 时，systemd 脚本会自动生成并保存到 `/opt/doskill/admin.token`
3. 在服务器上执行：`cat /opt/doskill/admin.token`
4. 把输出（一长串字母数字）整个复制到这里

**验证**：保存后，扩展会调一次 `/admin/config` 拉服务端配置；成功 → 模型 / API key 那几个字段会自动填上现有值。失败 → 显示「401 token 无效」，回服务器重新 `cat` 一次。

**安全提示**：这串 token 等价于服务器 root 权限，**别**贴到聊天群、PR 评论或公开仓库。
