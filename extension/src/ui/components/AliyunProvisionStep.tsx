// Plan 11 · M2.T13：让业务员粘 Aliyun access key 自动开机 ECS 的 wizard step。
//
// 业务员视角：
//   - 三个必填：Access Key ID / Secret / DeepSeek API Key
//   - 一个可选：通义视觉（看截图理解业务员指什么）
//   - 一个可选下拉：region（默认 cn-hangzhou）
//   - 点「自动开机」→ 调 /admin/provision-ecs 的「桥接 orchestrator」
//     （bootstrapOrchestratorUrl + bootstrapAdminToken 由调用方提供）
//   - 成功 → onComplete(publicIp, orchestratorUrl, adminToken)
//
// 安全：access key 只活在本组件 state 里，不入 chrome.storage；关页面即丢。
import { useState } from 'react';
import aliyunAccessKeyHelp from '../helpContent/aliyun-access-key.md?raw';
import { HelpBubble } from './HelpBubble';

interface Props {
  /** 接受 access key 的「桥接 orchestrator」URL —— 调用方负责凑齐 */
  bootstrapOrchestratorUrl: string;
  bootstrapAdminToken: string;
  /** 开机 + bootstrap 完成后回调 */
  onComplete: (result: ProvisionResult) => void;
}

export interface ProvisionResult {
  publicIp: string;
  orchestratorUrl: string;
  adminToken: string;
}

const REGIONS = [
  { id: 'cn-hangzhou', label: '华东 1（杭州）' },
  { id: 'cn-shanghai', label: '华东 2（上海）' },
  { id: 'cn-beijing', label: '华北 2（北京）' },
  { id: 'cn-shenzhen', label: '华南 1（深圳）' },
  { id: 'cn-hongkong', label: '香港' },
] as const;

type Status =
  | { kind: 'idle' }
  | { kind: 'running' }
  | { kind: 'partial'; publicIp: string; error: string }
  | { kind: 'error'; message: string };

export function AliyunProvisionStep({
  bootstrapOrchestratorUrl,
  bootstrapAdminToken,
  onComplete,
}: Props) {
  const [accessKeyId, setAccessKeyId] = useState('');
  const [accessKeySecret, setAccessKeySecret] = useState('');
  const [deepseekKey, setDeepseekKey] = useState('');
  const [dashscopeKey, setDashscopeKey] = useState('');
  const [regionId, setRegionId] = useState<string>('cn-hangzhou');
  const [status, setStatus] = useState<Status>({ kind: 'idle' });

  const canSubmit =
    accessKeyId.trim() !== '' &&
    accessKeySecret.trim() !== '' &&
    deepseekKey.trim() !== '' &&
    status.kind !== 'running';

  const onClick = async () => {
    setStatus({ kind: 'running' });
    try {
      const resp = await fetch(
        `${bootstrapOrchestratorUrl.replace(/\/$/, '')}/admin/provision-ecs`,
        {
          method: 'POST',
          headers: {
            'X-Admin-Token': bootstrapAdminToken,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            access_key_id: accessKeyId.trim(),
            access_key_secret: accessKeySecret.trim(),
            region_id: regionId,
            bootstrap: true,
            deepseek_api_key: deepseekKey.trim(),
            dashscope_api_key: dashscopeKey.trim() || null,
          }),
        },
      );

      if (!resp.ok) {
        let detail = `开机失败：HTTP ${resp.status}`;
        try {
          const body = (await resp.json()) as { detail?: string };
          if (body?.detail) detail = body.detail;
        } catch {
          /* 非 JSON 也 OK */
        }
        setStatus({ kind: 'error', message: detail });
        return;
      }

      const body = (await resp.json()) as ProvisionApiResponse;
      const bootstrap = body.bootstrap ?? { ok: false, error: 'bootstrap 字段缺失' };

      if (!bootstrap.ok || !bootstrap.admin_token || !bootstrap.orchestrator_url) {
        setStatus({
          kind: 'partial',
          publicIp: body.public_ip,
          error: bootstrap.error ?? 'bootstrap 失败但没回 admin_token',
        });
        return;
      }

      onComplete({
        publicIp: body.public_ip,
        orchestratorUrl: bootstrap.orchestrator_url,
        adminToken: bootstrap.admin_token,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setStatus({
        kind: 'error',
        message: `连不上 bootstrap 服务（${msg}）。检查 URL/token 或网络。`,
      });
    }
  };

  return (
    <section className="aliyun-step">
      <div className="title-with-help">
        <h3 className="title">自动开机：阿里云 ECS</h3>
        <HelpBubble content={aliyunAccessKeyHelp} ariaLabel="阿里云 Access Key 帮助" />
      </div>
      <p className="help">
        把你的 Aliyun Access Key 粘进来，vibe-niuma 给你开一台 4C8G ECS、装好所有东西。
        密钥只活在浏览器，不入服务器 DB、不进 log。开完即丢。
      </p>

      <label className="field">
        <span className="label">Access Key ID</span>
        <input
          type="text"
          aria-label="Access Key ID"
          value={accessKeyId}
          onChange={(e) => setAccessKeyId(e.target.value)}
          placeholder="LTAI5tXXXXXXXXXXXXX"
          disabled={status.kind === 'running'}
        />
      </label>

      <label className="field">
        <span className="label">Access Key Secret</span>
        <input
          type="password"
          aria-label="Access Key Secret"
          value={accessKeySecret}
          onChange={(e) => setAccessKeySecret(e.target.value)}
          disabled={status.kind === 'running'}
        />
      </label>

      <label className="field">
        <span className="label">区域</span>
        <select
          aria-label="区域"
          value={regionId}
          onChange={(e) => setRegionId(e.target.value)}
          disabled={status.kind === 'running'}
        >
          {REGIONS.map((r) => (
            <option key={r.id} value={r.id}>{r.label}</option>
          ))}
        </select>
      </label>

      <label className="field">
        <span className="label">DeepSeek API Key（必填）</span>
        <input
          type="password"
          aria-label="DeepSeek API Key"
          value={deepseekKey}
          onChange={(e) => setDeepseekKey(e.target.value)}
          placeholder="sk-XXXXXXXXXXXXXXXX"
          disabled={status.kind === 'running'}
        />
      </label>

      <label className="field">
        <span className="label">通义千问视觉 Key（可选 —— 看截图理解你想改哪儿）</span>
        <input
          type="password"
          aria-label="通义千问视觉"
          value={dashscopeKey}
          onChange={(e) => setDashscopeKey(e.target.value)}
          placeholder="sk-XXXXXXXXXXXXXXXX"
          disabled={status.kind === 'running'}
        />
      </label>

      {status.kind === 'running' && (
        <div className="alert alert-info" role="status">
          ⏳ 正在开机 + 装环境... 大约 3-5 分钟，请别关页面。
        </div>
      )}

      {status.kind === 'partial' && (
        <div className="alert alert-error" role="alert">
          ECS 已开（公网 IP <strong>{status.publicIp}</strong>），但 bootstrap 失败：
          <br />
          {status.error}
          <br />
          <small>你可以 ssh root@{status.publicIp} 手动跑 ecs-bootstrap.sh 重试。</small>
        </div>
      )}

      {status.kind === 'error' && (
        <div className="alert alert-error" role="alert">{status.message}</div>
      )}

      <div className="btn-row">
        <button
          type="button"
          className="btn btn-primary"
          onClick={() => void onClick()}
          disabled={!canSubmit}
        >
          {status.kind === 'running' ? '开机中…' : '自动开机'}
        </button>
      </div>
    </section>
  );
}

interface ProvisionApiResponse {
  instance_id: string;
  public_ip: string;
  region_id: string;
  root_password: string;
  open_ports: number[];
  bootstrap?: {
    ok: boolean;
    admin_token?: string;
    orchestrator_url?: string;
    error?: string;
  };
  next_step?: string;
}
