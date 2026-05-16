// Plan 9 Task 8: 「请选择项目」全屏面板。
// 触发场景：没 active project（首次安装 / 删了最后一个 / 主动切到 null）。
import React, { useEffect, useState } from 'react';
import { loadProjects, setActiveProject, type Project } from '../../lib/projects';

interface Props {
  onPicked: () => void;
  onCreateNew: () => void;
}

export function ProjectSelectorPanel({ onPicked, onCreateNew }: Props) {
  const [projects, setProjects] = useState<Project[]>([]);
  useEffect(() => {
    void loadProjects().then(setProjects);
  }, []);

  const pick = async (id: string) => {
    await setActiveProject(id);
    onPicked();
  };

  return (
    <div className="app-body">
      <section>
        <div className="eyebrow"><span className="ix">PROJECTS</span><span>选项目</span><span className="rule" /></div>
        <h3 className="title">选一个项目，或新建一个</h3>
        <p className="help">每个项目独立绑一台 orchestrator + git 仓库 + API key。在项目之间切就像 Cursor workspace 切换。</p>

        {projects.length === 0 ? (
          <div className="card" style={{ padding: 16, textAlign: 'center' }}>
            <div style={{ color: 'var(--ink-soft)', marginBottom: 12 }}>还没有项目</div>
            <button className="btn btn-primary" onClick={onCreateNew}>+ 新建第一个项目</button>
          </div>
        ) : (
          <>
            <ul className="project-list">
              {projects.map((p) => (
                <li key={p.id} className="project-list-item" onClick={() => void pick(p.id)}>
                  <span className="project-list-name">{p.name}</span>
                  <span className="project-list-meta">{p.config.orchestratorUrl}</span>
                </li>
              ))}
            </ul>
            <div className="btn-row" style={{ marginTop: 12 }}>
              <button className="btn btn-secondary" onClick={onCreateNew}>+ 新建项目</button>
            </div>
          </>
        )}
      </section>
    </div>
  );
}
