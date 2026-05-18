// Plan 11 · M3.T22：业务员看到红色横幅时一键报错给程序员。
//
// 数据流：
// - 按钮自带 textarea 让业务员补「想说的话」
// - 提交时凑 title + body（含 lastCrId、console errors、业务员留言）
// - POST orchestrator /admin/alert，由 orchestrator 转发到 wizard 配的 webhook
//
// 失败时显示 detail，让业务员能复制 webhook URL 自己粘到群里（兜底）。
import { useState } from 'react';

interface ReportContext {
  lastCrId?: string | null;
  consoleErrors?: string[];
  /** 可选额外字段：URL / screenshot key 等 */
  extra?: Record<string, string>;
}

interface Props {
  orchestratorUrl: string;
  adminToken: string;
  /** 业务员配的 webhook URL（chrome.storage.local 里取）；为空时按钮禁用 */
  webhookUrl: string;
  context: ReportContext;
}

type Status =
  | { kind: 'idle' }
  | { kind: 'sending' }
  | { kind: 'ok' }
  | { kind: 'error'; message: string };

export function ReportToDevButton({
  orchestratorUrl,
  adminToken,
  webhookUrl,
  context,
}: Props) {
  const [note, setNote] = useState('');
  const [status, setStatus] = useState<Status>({ kind: 'idle' });

  const disabled = !webhookUrl || status.kind === 'sending';

  const onClick = async () => {
    setStatus({ kind: 'sending' });
    const body = composeReportBody(note, context);
    try {
      const resp = await fetch(
        `${orchestratorUrl.replace(/\/$/, '')}/admin/alert`,
        {
          method: 'POST',
          headers: {
            'X-Admin-Token': adminToken,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            webhook_url: webhookUrl,
            title: '⚠️ vibe-niuma 业务员上报',
            body,
          }),
        },
      );
      if (!resp.ok) {
        let detail = `发送失败：HTTP ${resp.status}`;
        try {
          const j = (await resp.json()) as { detail?: string };
          if (j?.detail) detail = j.detail;
        } catch { /* 非 JSON 也 OK */ }
        setStatus({ kind: 'error', message: detail });
        return;
      }
      setStatus({ kind: 'ok' });
    } catch (err) {
      setStatus({
        kind: 'error',
        message: `连不上 orchestrator：${err instanceof Error ? err.message : String(err)}`,
      });
    }
  };

  return (
    <section className="report-to-dev">
      <label className="field">
        <span className="label">补充留言（可选）</span>
        <textarea
          aria-label="补充留言"
          value={note}
          onChange={(e) => setNote(e.target.value)}
          rows={3}
          placeholder="点保存按钮就报红 / 第 3 步合不进去 ..."
        />
      </label>

      {!webhookUrl && (
        <p className="hint">先在设置里配 webhook（钉钉/飞书/Discord）再用此按钮。</p>
      )}

      {status.kind === 'ok' && (
        <div className="alert alert-ok" role="status">✓ 已发给程序员，请等回复。</div>
      )}
      {status.kind === 'error' && (
        <div className="alert alert-error" role="alert">{status.message}</div>
      )}

      <button
        type="button"
        className="btn btn-primary"
        onClick={() => void onClick()}
        disabled={disabled}
      >
        {status.kind === 'sending' ? '发送中…' : '报告给程序员'}
      </button>
    </section>
  );
}

function composeReportBody(note: string, ctx: ReportContext): string {
  const lines: string[] = [];
  if (note.trim()) {
    lines.push('【业务员留言】');
    lines.push(note.trim());
    lines.push('');
  }
  if (ctx.lastCrId) {
    lines.push(`最近失败 CR: ${ctx.lastCrId}`);
  }
  if (ctx.consoleErrors?.length) {
    lines.push('');
    lines.push('【浏览器 console 错误】');
    lines.push(...ctx.consoleErrors.slice(0, 10));
  }
  if (ctx.extra) {
    lines.push('');
    for (const [k, v] of Object.entries(ctx.extra)) {
      lines.push(`${k}: ${v}`);
    }
  }
  lines.push('');
  lines.push(`上报时间: ${new Date().toISOString()}`);
  return lines.join('\n');
}
