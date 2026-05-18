# vibe-niuma 浏览器扩展

业务员侧的薄客户端：框选页面区域 + 输入需求 → Orchestrator → 看预览 → 确认合并 / 丢弃。

## 开发

```bash
cd extension
npm install                  # 用 .npmrc 里的 npmmirror 镜像
npm test                     # 运行 vitest
npm run build                # 输出到 dist/
```

## 在 Chrome 加载

1. `npm run build` 生成 `dist/`
2. 打开 `chrome://extensions`，开启「开发者模式」
3. 点「加载已解压的扩展程序」，选 `extension/dist`
4. 点 vibe-niuma 图标 → 侧栏弹出
5. 在侧栏齿轮里填 Orchestrator 地址（默认 `http://localhost:9000`）

## 连真实 ECS

设置面板里把 Base URL 改成 ECS 公网地址 + Orchestrator 端口，例如
`http://1.2.3.4:9000`（生产建议加 HTTPS / 反代 / nginx 等，本 MVP 不考虑安全）。

## 已知限制（MVP 边界）

- 单用户、单角色、自审；无认证。
- `host_permissions: <all_urls>`：能注入到任何页面。生产应收敛。
- 截图用 `chrome.tabs.captureVisibleTab` —— 只是当前可视区，不滚屏拼接。
- side panel API 需要 Chrome 116+。

## 架构

```
content/
  capture-overlay.ts   框选 overlay（拖拽采集 boxCoords + viewport）
  content-entry.ts     content script 入口（监听 START_CAPTURE）
background/
  orchestrator-client.ts   REST + SSE 封装（带 EventSource 重连 + GET 兜底）
  request-store.ts         状态镜像 reducer + chrome.storage 持久化
  service-worker.ts        消息编排中枢
ui/
  App.tsx               按 RequestState 路由到 panel
  panels.tsx            7 个 panel + ProgressTrail
  hooks/useRequestState.ts   订阅 background 镜像
  tokens.css            设计 token
lib/
  types.ts              与 Orchestrator REST/SSE 对齐的 TS 类型
  messages.ts           content↔background↔ui 消息协议
```
