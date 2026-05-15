// Plan 6 · Task 8: 通用 wizard 步骤外壳。
// 设计要点：
//   - 仅负责布局/导航：title + description + children + 底部按钮行
//   - 不持有任何业务状态（URL / token / api key 都在父 SetupWizardPanel 里）
//   - canGoNext 由父传入；按钮 disabled 完全受控
//   - Step 1 时 isFirstStep=true 隐藏「← 上一步」
//   - Step 4 时 isLastStep=true 用「开始使用 doskill」作为按钮文案，由父通过 nextLabel 注入
import type { ReactNode } from 'react';

export interface WizardStepProps {
  stepNumber: number;
  title: string;
  description: string;
  children: ReactNode;
  canGoNext: boolean;
  onNext?: () => void;
  onPrev?: () => void;
  isFirstStep?: boolean;
  isLastStep?: boolean;
  nextLabel?: string;
}

export function WizardStep({
  stepNumber,
  title,
  description,
  children,
  canGoNext,
  onNext,
  onPrev,
  isFirstStep = false,
  isLastStep = false,
  nextLabel,
}: WizardStepProps) {
  const computedNextLabel = nextLabel ?? (isLastStep ? '开始使用 doskill' : '下一步 →');
  return (
    <section className="wizard-step" aria-label={`第 ${stepNumber} 步`}>
      <div className="eyebrow">第 {stepNumber} 步 / 共 4 步</div>
      <h3 className="title">{title}</h3>
      <p className="help">{description}</p>
      <div className="wizard-step-body">{children}</div>
      <div className="btn-row">
        {!isFirstStep && (
          <button
            type="button"
            className="btn btn-secondary"
            onClick={onPrev}
          >
            ← 上一步
          </button>
        )}
        <button
          type="button"
          className="btn btn-primary"
          onClick={onNext}
          disabled={!canGoNext}
        >
          {computedNextLabel}
        </button>
      </div>
    </section>
  );
}
