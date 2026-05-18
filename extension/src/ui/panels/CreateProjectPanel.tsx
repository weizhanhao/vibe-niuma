// Plan 9 Task 8: 新建项目向导。两步：
//   1. 起名
//   2. 走 Plan 7 部署助手（DeploymentAssistantPanel）；助手 onComplete 时
//      读 chrome.storage 里临时 config（助手已写到 vibe_niuma_config_v2），
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

  // 进 step 2 前清掉上次未完成的 wizard state，让新项目从 deepseek key 卡片开始
  // 不被上次留下的 phase=choosing_path 之类污染（业务员反馈：空 chat 框不知道干嘛）。
  // 注：只清 wizard FSM + history，全局 deepseek key（KEY_KEY）保留 —— 业务员不用重填。
  const goToStep2 = async () => {
    if (chrome?.storage?.local?.remove) {
      try {
        await chrome.storage.local.remove([
          'vibe_niuma_deployment_state',
          'vibe_niuma_deployment_history',
        ]);
      } catch {
        /* 清不掉也别拦着用户进 step 2 */
      }
    }
    setStep(2);
  };

  if (step === 1) {
    return (
      <div className="app-body">
        <section>
          <h3 className="title">给新项目起个名字</h3>
          <p className="help">业务上你会怎么叫这套东西？</p>
          <label className="field">
            <span className="label"><span>项目名</span><span className="count">{name.length} / 50</span></span>
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
              onClick={() => void goToStep2()}
              disabled={!name.trim()}
            >下一步 →</button>
          </div>
        </section>
      </div>
    );
  }

  return <DeploymentAssistantPanel onComplete={() => void onAssistantComplete()} />;
}
