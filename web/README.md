# vplatform-web —— 并行开发调度台

v2 的 Web 控制台。设计语言沿用
[原型](../docs/mockups/v2-web-console-demo.html)：调度台，
IBM Plex（拉丁）+ PingFang（中文），扁平深电靛为唯一强调色，语义色独立
（赭金=等你处理 / 砖红=冲突 / 深绿=通过）。

```bash
npm i && npm run dev      # 代理 /api → http://127.0.0.1:9000
npm test && npm run build
```

## 三个刻意的呈现决定

**① 看板列由流水线配置推导，不硬编码。**
`columnsFrom()` 按语义把环节归组；**没被认领的环节自成一列** ——
否则加一个环节（D8 允许只改 YAML）会让那一列的需求从看板上凭空消失。

**② 「未发现」不写成「没有问题」。**
实测同一份 diff 三次跑出 2/0/0，复核召回不稳定（用召回换精确的设计取舍）。
所以文案是「本次未发现问题（不等于没有问题）」，且明确提示不要据此跳过人工审核。

**③ wide refactor 不报冲突预警。**
`touchCollisions()` 跳过带 `sequence_kind` 的需求 —— 它们的 touches 大面积相交是
**预期的**（expand → migrate → contract）。按普通规则卡住是错的。
