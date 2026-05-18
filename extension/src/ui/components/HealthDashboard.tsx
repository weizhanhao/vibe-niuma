// Plan 11 · M3.T19：主界面右上角的健康指示灯。
//
// 设计：
//   - 灯本身永远在 header 右上角，业务员看一眼就知道系统状态
//   - ok 绿灯（小，不打扰），hover 才出详情
//   - yellow 黄灯（中等存在感），hover 详情
//   - red 红灯（醒目）+ 横幅（业务员看到必有反应）
//   - 每 pollIntervalMs（默认 30s）轮询 /health
//   - /health 自身挂掉时 status='red'，业务员被引导点 ReportToDevButton（T22）
import { useEffect, useRef, useState } from 'react';

interface Props {
  orchestratorUrl: string;
  /** 留口给后续 /alert 用 —— 暂时不用，但不增字段重构 */
  adminToken: string;
  /** 默认 30s 一轮 —— 测试可改快 */
  pollIntervalMs?: number;
}

type ServiceStatus = 'ok' | 'down' | 'unknown';
type HealthStatus = 'ok' | 'yellow' | 'red';

interface HealthPayload {
  status: HealthStatus;
  services: Record<string, ServiceStatus>;
  uptime_seconds: number;
  last_cr_at: string | null;
  last_error?: string | null;
}

const DEFAULT_POLL_MS = 30_000;

export function HealthDashboard({
  orchestratorUrl,
  pollIntervalMs = DEFAULT_POLL_MS,
}: Props) {
  const [health, setHealth] = useState<HealthPayload | null>(null);
  const [tooltipOpen, setTooltipOpen] = useState(false);
  const inFlightRef = useRef(false);

  useEffect(() => {
    let mounted = true;

    const probe = async () => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      try {
        const url = `${orchestratorUrl.replace(/\/$/, '')}/health`;
        const resp = await fetch(url);
        if (!resp.ok) {
          if (mounted) {
            setHealth({
              status: 'red',
              services: { orchestrator: 'down' },
              uptime_seconds: 0,
              last_cr_at: null,
              last_error: `/health HTTP ${resp.status}`,
            });
          }
          return;
        }
        const body = (await resp.json()) as HealthPayload;
        if (mounted) setHealth(body);
      } catch (err) {
        if (mounted) {
          setHealth({
            status: 'red',
            services: { orchestrator: 'down' },
            uptime_seconds: 0,
            last_cr_at: null,
            last_error: err instanceof Error ? err.message : String(err),
          });
        }
      } finally {
        inFlightRef.current = false;
      }
    };

    void probe();
    const timer = setInterval(() => { void probe(); }, pollIntervalMs);
    return () => {
      mounted = false;
      clearInterval(timer);
    };
  }, [orchestratorUrl, pollIntervalMs]);

  const status: HealthStatus = health?.status ?? 'ok';
  const services = health?.services ?? {};
  const downServices = Object.entries(services)
    .filter(([, s]) => s === 'down')
    .map(([k]) => k);

  return (
    <>
      <div
        className={`health-indicator health-${status}`}
        role="status"
        aria-label="系统状态"
        data-status={status}
        onMouseEnter={() => setTooltipOpen(true)}
        onMouseLeave={() => setTooltipOpen(false)}
      >
        <span className="health-dot" />
        {tooltipOpen && health && (
          <div className="health-tooltip">
            <ul className="health-tooltip-services">
              {Object.entries(services).map(([name, s]) => (
                <li key={name}>
                  <span className={`health-svc-dot health-svc-${s}`} />
                  <strong>{name}</strong>：{s}
                </li>
              ))}
            </ul>
            <div className="health-tooltip-meta">
              <small>uptime: {formatUptime(health.uptime_seconds)}</small>
              {health.last_cr_at && (
                <small>最近 CR: {health.last_cr_at}</small>
              )}
              {health.last_error && (
                <small className="health-error-detail">err: {health.last_error}</small>
              )}
            </div>
          </div>
        )}
      </div>

      {status === 'red' && (
        <div className="health-banner" role="alert">
          ⚠️ 系统异常：
          {downServices.length > 0
            ? downServices.join(' / ') + ' 不可达。'
            : '/health 不通。'}
          {' '}请联系程序员。
        </div>
      )}
    </>
  );
}

function formatUptime(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  const m = Math.floor(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h${m % 60}m`;
  const d = Math.floor(h / 24);
  return `${d}d${h % 24}h`;
}
