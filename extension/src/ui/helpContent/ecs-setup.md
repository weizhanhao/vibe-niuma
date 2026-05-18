**ECS 是部署 orchestrator 的服务器**。vibe-niuma 需要一台能跑 Docker 的 Linux 机器（≥ 4 GiB 内存，≥ 40 GiB 硬盘）。

**新手推荐**（按价格升序）：

- [阿里云轻量应用服务器](https://www.aliyun.com/product/swas) — 约 60 元/月，控制台对新手最友好
- [腾讯云轻量](https://cloud.tencent.com/product/lighthouse) — 同价位备选
- [DigitalOcean Droplet](https://www.digitalocean.com/products/droplets) — 海外，约 6 美元/月

**关键设置**：

- 镜像选 **Alibaba Cloud Linux 4** 或 **Ubuntu 22.04**
- 安全组（防火墙）开放端口：`22`（SSH）/ `8000` / `8787` / `9000` / `5100-5199`
- 创建后用 SSH 登录，按仓库 `docs/RUNBOOK.md` 跑 `bash deploy/provision.sh`（首次开荒脚本，安装 Docker / Nginx / systemd 服务等）

**验证**：开荒完后浏览器访问 `http://<公网-IP>:9000/health` 应该返回 `{"status":"ok"}`；访问不到就回安全组检查 9000 是否放行。

**详细步骤** 参考仓库 `docs/RUNBOOK.md`。
