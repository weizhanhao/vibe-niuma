// Plan 11 · M3.T23：wizard 里配「告警 webhook」的可选步。
//
// 业务员粘个钉钉/飞书/Discord 群机器人 webhook → orchestrator 出问题时
// 业务员能点 ReportToDevButton 一键报错给程序员。
//
// 可跳过 —— webhook 不是 vibe-niuma 跑起来的必需品；先用，出问题再回来配。
import { useState } from 'react';

interface Props {
  initialUrl?: string;
  /** value 为 '' 表示业务员选择跳过；非空时是合法 URL */
  onComplete: (webhookUrl: string) => void;
  allowSkip?: boolean;
}

type Detected = 'dingtalk' | 'feishu' | 'discord' | 'unknown' | 'empty';

function detectKind(url: string): Detected {
  if (!url) return 'empty';
  if (url.includes('dingtalk.com')) return 'dingtalk';
  if (url.includes('feishu.cn') || url.includes('larksuite.com')) return 'feishu';
  if (url.includes('discord.com') || url.includes('discordapp.com')) return 'discord';
  return 'unknown';
}

const LABEL: Record<Detected, string> = {
  dingtalk: '✓ 已识别：钉钉机器人',
  feishu: '✓ 已识别：飞书机器人',
  discord: '✓ 已识别：Discord webhook',
  unknown: '⚠️ 不识别此 URL（vibe-niuma 只认钉钉/飞书/Discord）',
  empty: '',
};

export function AlertWebhookStep({ initialUrl = '', onComplete, allowSkip = true }: Props) {
  const [url, setUrl] = useState(initialUrl);
  const detected = detectKind(url.trim());

  const valid = detected === 'dingtalk' || detected === 'feishu' || detected === 'discord';

  return (
    <section className="alert-webhook-step">
      <h3 className="title">配告警群（可选）</h3>
      <p className="help">
        粘贴一个群机器人 webhook URL。出问题时业务员点「报告给程序员」按钮，
        vibe-niuma 自动把上下文（最近失败 CR、console 错误、业务员留言）发到群里。
      </p>
      <p className="help">
        三家都支持：<strong>钉钉机器人</strong>（最常用）、<strong>飞书机器人</strong>、<strong>Discord webhook</strong>。
      </p>

      <label className="field">
        <span className="label">Webhook URL</span>
        <input
          type="url"
          aria-label="Webhook URL"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
        />
      </label>

      {detected !== 'empty' && (
        <p className={detected === 'unknown' ? 'alert alert-error' : 'hint'}>
          {LABEL[detected]}
        </p>
      )}

      <div className="btn-row">
        {allowSkip && (
          <button
            type="button"
            className="btn btn-ghost"
            onClick={() => onComplete('')}
          >
            跳过（后续设置里配）
          </button>
        )}
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => onComplete(url.trim())}
          disabled={!valid}
        >
          保存 →
        </button>
      </div>
    </section>
  );
}
