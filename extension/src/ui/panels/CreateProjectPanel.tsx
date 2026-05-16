// Plan 9 Task 8: 新建项目向导。两步：
//   1. 起名
//   2. 走 Plan 7 部署助手（DeploymentAssistantPanel）；助手 onComplete 时
//      读 chrome.storage 里临时 config（助手已写到 doskill_config_v2），
//      pack 成新 Project saveProject + setActive。
import React, { useState } from 'react';
import { loadConfig } from '../../lib/config';
import { createProject, setActiveProject } from '../../lib/projects';
import { DeploymentAssistantPanel } from './DeploymentAssistantPanel';

interface Props {
  onDone: () => void;
  onCancel: () => void;
}

export function CreateProjectPanel({ onDone, onCancel }: Props) {
  const [step, setStep] = useState<1 | 2>(1);
  const [name, setName] = useState('');

  const onAssistantComplete = async () => {
    const cfg = await loadConfig();
    if (!cfg) {
      alert('配置未保存？请重试。');
      return;
    }
    const project = await createProject(name, cfg);
    await setActiveProject(project.id);
    onDone();
  };

  if (step === 1) {
    return (
      <div className="app-body">
        <section>
          <div className="eyebrow"><span className="ix">NEW PROJECT</span><span>step 1</span><span className="rule" /></div>
          <h3 className="title">给新项目起个名字</h3>
          <p className="help">业务上你会怎么叫这套东西？「订单管理」「内部 OA」「客服后台」之类。</p>
          <label className="field">
            <span className="label"><span>项目名 · NAME</span><span className="count">{name.length} / 50</span></span>
            <input
              type="text"
              aria-label="项目名"
              value={name}
              maxLength={50}
              onChange={(e) => setName(e.target.value)}
              placeholder="订单管理 / 内部工具 ..."
            />
          </label>
          <div className="btn-row">
            <button className="btn btn-ghost" onClick={onCancel}>取消</button>
            <button
              className="btn btn-primary"
              onClick={() => setStep(2)}
              disabled={!name.trim()}
            >下一步 →</button>
          </div>
          <p className="hint">下一步会走 AI 部署助手，引导你填 orchestrator + API key。</p>
        </section>
      </div>
    );
  }

  return <DeploymentAssistantPanel onComplete={() => void onAssistantComplete()} />;
}
