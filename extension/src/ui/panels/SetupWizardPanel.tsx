// Plan 6 · Task 8: 首次安装扩展时的 4 步引导。
//
// 设计要点：
//   - 4 步串行：URL → token → API key → 完成；每步必须通过校验才能 go next
//   - Step 1 用 testConnection（无 token，打 /health）
//   - Step 2 用 getConfig（带 token，打 /admin/config）→ 返回 version 暂存
//   - Step 3 用 putConfig 提交 3 个 key，带 expectedVersion=Step 2 拿到的 version
//   - Step 4 把 {orchestratorUrl, adminToken, configVersion=putConfig 返回的新 version}
//     写进 chrome.storage，调 onComplete() 让父路由出 wizard
//   - 跳过引导：弹 confirm 后调 onComplete()（父决定显示 SettingsPanel 还是别的）
//   - 顶部 4 步指示器：完成=绿/√，当前=蓝，未开始=灰
//
// 调用方：
//   - extension/tests/setup-wizard.test.tsx —— 单测
//   - 未来 App.tsx（Plan 6 Task 10）会基于 isConfigured() 路由进来

import { useState } from 'react';
import type { ReactNode } from 'react';
import {
  AdminClient,
  AuthError,
  NetworkError,
  StaleVersionError,
  ValidationError,
  type ServerConfig as WireServerConfig,
} from '../../lib/admin-client';
import { saveConfig } from '../../lib/config';
import { HelpBubble } from '../components/HelpBubble';
import { WizardStep } from '../components/WizardStep';

// 帮助文档 markdown 内容（构建时打包成字符串，运行时不读文件）
import orchestratorUrlHelp from '../helpContent/orchestrator-url.md?raw';
import adminTokenHelp from '../helpContent/admin-token.md?raw';
import deepseekKeyHelp from '../helpContent/deepseek-key.md?raw';
import dashscopeKeyHelp from '../helpContent/dashscope-key.md?raw';
// Anthropic 可选 key，没专属 md。用 ecs-setup.md 兜底（也跟服务器关联）。
import anthropicKeyHelp from '../helpContent/ecs-setup.md?raw';

// 业务员调试时常错的 URL 格式（缺 http://、含中文等）就让「测试连接」按钮 disable
function isValidUrl(s: string): boolean {
  try {
    const u = new URL(s);
    return u.protocol === 'http:' || u.protocol === 'https:';
  } catch {
    return false;
  }
}

// AdminClient 抛错文案归一化 —— 所有 4xx/5xx/网络错误统一成可读 message
function describeError(err: unknown): string {
  if (err instanceof AuthError) return '令牌错误（401）';
  if (err instanceof StaleVersionError) {
    return '服务端配置已被其他人修改，请重启引导';
  }
  if (err instanceof ValidationError) return `字段校验失败：${err.message}`;
  if (err instanceof NetworkError) return `网络错误：${err.message}`;
  if (err instanceof Error) return err.message;
  return String(err);
}

export interface SetupWizardPanelProps {
  onComplete: () => void;
}

// 4 步指示器：完成（绿√）/ 当前（蓝）/ 未开始（灰）
function ProgressDots({ current }: { current: 1 | 2 | 3 | 4 }) {
  const steps: Array<1 | 2 | 3 | 4> = [1, 2, 3, 4];
  return (
    <ol className="wizard-dots" aria-label="安装进度">
      {steps.map((n) => {
        let cls = 'wizard-dot';
        if (n < current) cls += ' is-done';
        else if (n === current) cls += ' is-current';
        else cls += ' is-pending';
        return (
          <li
            key={n}
            className={cls}
            data-testid={`wizard-dot-${n}`}
            aria-current={n === current ? 'step' : undefined}
          >
            <span className="wizard-dot-num">{n < current ? '✓' : n}</span>
          </li>
        );
      })}
    </ol>
  );
}

export function SetupWizardPanel({ onComplete }: SetupWizardPanelProps) {
  // ── 状态：每步表单值 + 校验结果 ────────────────────────────────
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1);

  // Step 1
  const [orchestratorUrl, setOrchestratorUrl] = useState('http://localhost:9000');
  const [step1Tested, setStep1Tested] = useState(false);
  const [step1Error, setStep1Error] = useState<string | null>(null);

  // Step 2
  const [adminToken, setAdminToken] = useState('');
  const [step2Verified, setStep2Verified] = useState(false);
  const [step2Version, setStep2Version] = useState<number | null>(null);
  const [step2Error, setStep2Error] = useState<string | null>(null);

  // Step 3
  const [deepseekKey, setDeepseekKey] = useState('');
  const [dashscopeKey, setDashscopeKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');
  const [step3Saved, setStep3Saved] = useState(false);
  const [step3FinalVersion, setStep3FinalVersion] = useState<number | null>(null);
  const [step3Error, setStep3Error] = useState<string | null>(null);

  // ── 业务员看得见的「测试连接」/「验证」/「保存」按钮显式推进 ──

  const onTestConnection = async () => {
    setStep1Error(null);
    setStep1Tested(false);
    try {
      const client = new AdminClient(orchestratorUrl, '');
      const ok = await client.testConnection();
      if (ok) {
        setStep1Tested(true);
      } else {
        setStep1Error('连接失败：服务器无响应或不是 vibe-niuma orchestrator');
      }
    } catch (err) {
      setStep1Error(`连接失败：${describeError(err)}`);
    }
  };

  const onVerifyToken = async () => {
    setStep2Error(null);
    setStep2Verified(false);
    try {
      const client = new AdminClient(orchestratorUrl, adminToken);
      const resp = await client.getConfig();
      setStep2Verified(true);
      setStep2Version(resp.version);
    } catch (err) {
      setStep2Error(describeError(err));
    }
  };

  const onSaveKeys = async () => {
    setStep3Error(null);
    if (step2Version === null) {
      setStep3Error('请先完成第 2 步');
      return;
    }
    try {
      const client = new AdminClient(orchestratorUrl, adminToken);
      // Anthropic 可选：留空就传 null（让后端清掉/保持不设）
      const patch: Partial<WireServerConfig> = {
        deepseek_api_key: deepseekKey,
        dashscope_api_key: dashscopeKey,
        anthropic_api_key: anthropicKey.trim() === '' ? null : anthropicKey,
      };
      const resp = await client.putConfig(patch, step2Version);
      setStep3Saved(true);
      setStep3FinalVersion(resp.version);
      setStep(4);
    } catch (err) {
      setStep3Error(describeError(err));
    }
  };

  const onFinish = async () => {
    // 保存到 chrome.storage —— 父组件 Task 10 会基于 isConfigured() 路由出 wizard
    const finalVersion = step3FinalVersion ?? step2Version ?? 0;
    await saveConfig({
      orchestratorUrl,
      adminToken,
      configVersion: finalVersion,
    });
    onComplete();
  };

  const onSkip = () => {
    // 业务员明确放弃 = 后端没配，扩展无法工作；二次确认避免误点
    const ok = window.confirm('跳过会导致功能不可用，确认？');
    if (ok) onComplete();
  };

  // ── 4 步渲染 ────────────────────────────────────────────────

  let body: ReactNode;
  if (step === 1) {
    const canTest = isValidUrl(orchestratorUrl);
    body = (
      <WizardStep
        stepNumber={1}
        title="服务器地址"
        description="填 vibe-niuma orchestrator 部署的入口 URL；本地开发用默认值即可。"
        canGoNext={step1Tested}
        onNext={() => setStep(2)}
        isFirstStep
      >
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">Orchestrator URL</span>
            <HelpBubble content={orchestratorUrlHelp} ariaLabel="Orchestrator URL 帮助" />
          </div>
          <input
            aria-label="Orchestrator URL"
            type="url"
            value={orchestratorUrl}
            onChange={(e) => {
              setOrchestratorUrl(e.target.value);
              setStep1Tested(false);
              setStep1Error(null);
            }}
          />
        </div>
        <div className="btn-row">
          <button
            type="button"
            className={step1Tested ? 'btn btn-accent' : 'btn btn-secondary'}
            onClick={onTestConnection}
            disabled={!canTest}
          >
            {step1Tested ? '✓ 连接成功' : '测试连接'}
          </button>
        </div>
        {step1Error && (
          <p className="wizard-error" role="alert">{step1Error}</p>
        )}
      </WizardStep>
    );
  } else if (step === 2) {
    body = (
      <WizardStep
        stepNumber={2}
        title="管理员令牌"
        description="服务器上 cat /opt/vibe-niuma/admin.token 输出的那串字符串。"
        canGoNext={step2Verified}
        onNext={() => setStep(3)}
        onPrev={() => setStep(1)}
      >
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">Admin Token</span>
            <HelpBubble content={adminTokenHelp} ariaLabel="Admin Token 帮助" />
          </div>
          <input
            aria-label="Admin Token"
            type="password"
            value={adminToken}
            onChange={(e) => {
              setAdminToken(e.target.value);
              setStep2Verified(false);
              setStep2Error(null);
            }}
          />
        </div>
        <div className="btn-row">
          <button
            type="button"
            className={step2Verified ? 'btn btn-accent' : 'btn btn-secondary'}
            onClick={onVerifyToken}
            disabled={adminToken.length < 20}
          >
            {step2Verified ? '✓ 令牌有效' : '验证'}
          </button>
        </div>
        {step2Verified && step2Version !== null && (
          <p className="wizard-success" role="status">
            令牌有效，当前配置版本 v{step2Version}
          </p>
        )}
        {step2Error && (
          <p className="wizard-error" role="alert">{step2Error}</p>
        )}
      </WizardStep>
    );
  } else if (step === 3) {
    // 必填字段（DeepSeek + DashScope）都填了才启用按钮。Anthropic 可选。
    const canSave = deepseekKey.trim().length > 0 && dashscopeKey.trim().length > 0;
    body = (
      <WizardStep
        stepNumber={3}
        title="模型 API Key"
        description="DeepSeek 改代码、DashScope 看截图，都是必填；Anthropic 可选（备用模型）。"
        canGoNext={step3Saved}
        onNext={() => setStep(4)}
        onPrev={() => setStep(2)}
        nextLabel="保存并下一步"
      >
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">DeepSeek API Key（必填）</span>
            <HelpBubble content={deepseekKeyHelp} ariaLabel="DeepSeek Key 帮助" />
          </div>
          <input
            aria-label="DeepSeek API Key"
            type="password"
            value={deepseekKey}
            onChange={(e) => {
              setDeepseekKey(e.target.value);
              setStep3Saved(false);
              setStep3Error(null);
            }}
          />
        </div>
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">DashScope API Key（必填）</span>
            <HelpBubble content={dashscopeKeyHelp} ariaLabel="DashScope Key 帮助" />
          </div>
          <input
            aria-label="DashScope API Key"
            type="password"
            value={dashscopeKey}
            onChange={(e) => {
              setDashscopeKey(e.target.value);
              setStep3Saved(false);
              setStep3Error(null);
            }}
          />
        </div>
        <div className="field">
          <div className="field-label-row">
            <span className="field-label">Anthropic API Key（可选）</span>
            <HelpBubble content={anthropicKeyHelp} ariaLabel="Anthropic Key 帮助" />
          </div>
          <input
            aria-label="Anthropic API Key"
            type="password"
            value={anthropicKey}
            onChange={(e) => {
              setAnthropicKey(e.target.value);
              setStep3Saved(false);
              setStep3Error(null);
            }}
          />
        </div>
        <div className="btn-row">
          <button
            type="button"
            className={step3Saved ? 'btn btn-accent' : 'btn btn-primary'}
            onClick={onSaveKeys}
            disabled={!canSave}
          >
            {step3Saved ? '✓ 已保存' : '保存到服务器'}
          </button>
        </div>
        {step3Error && (
          <p className="wizard-error" role="alert">{step3Error}</p>
        )}
      </WizardStep>
    );
  } else {
    // Step 4：总结 + 「开始使用 vibe-niuma」
    body = (
      <WizardStep
        stepNumber={4}
        title="一切就绪"
        description="确认下方信息，然后开始第一个 change request。"
        canGoNext
        onNext={onFinish}
        onPrev={() => setStep(3)}
        isLastStep
      >
        <ul className="wizard-summary" aria-label="配置总结">
          <li>
            <span className="wizard-summary-check">✓</span>
            服务器 URL：<code>{orchestratorUrl}</code>
          </li>
          <li>
            <span className="wizard-summary-check">✓</span>
            管理员令牌（v{step3FinalVersion ?? step2Version ?? 0}）
          </li>
          <li>
            <span className="wizard-summary-check">✓</span>
            DeepSeek API Key
          </li>
          <li>
            <span className="wizard-summary-check">✓</span>
            DashScope API Key
          </li>
          <li>
            {anthropicKey.trim() === '' ? (
              <>
                <span className="wizard-summary-skip">×</span>
                Anthropic API Key（可选，未设置）
              </>
            ) : (
              <>
                <span className="wizard-summary-check">✓</span>
                Anthropic API Key
              </>
            )}
          </li>
        </ul>
      </WizardStep>
    );
  }

  return (
    <div className="wizard">
      <ProgressDots current={step} />
      {body}
      <div className="wizard-skip-row">
        <button
          type="button"
          className="wizard-skip"
          onClick={onSkip}
        >
          跳过引导
        </button>
      </div>
    </div>
  );
}
