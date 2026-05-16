// Plan 9 Task 8: head 项目切换 dropdown。
// 点击 active 项目名展开列表 + 「+ 新建」按钮。每行 hover 显示 trash。
import React, { useEffect, useRef, useState } from 'react';
import { deleteProject, loadProjects, setActiveProject, type Project } from '../../lib/projects';

interface Props {
  active: Project | null;
  onSwitch: () => void;
  onCreateNew: () => void;
}

export function ProjectSwitcher({ active, onSwitch, onCreateNew }: Props) {
  const [open, setOpen] = useState(false);
  const [projects, setProjects] = useState<Project[]>([]);
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    void loadProjects().then(setProjects);
  }, [open]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    return () => document.removeEventListener('mousedown', onDown);
  }, []);

  const onPick = async (id: string) => {
    await setActiveProject(id);
    setOpen(false);
    onSwitch();
  };

  const onDelete = async (e: React.MouseEvent, id: string) => {
    e.stopPropagation();
    if (!confirm('删除这个项目？配置 + 关联的对话不会动 server 端数据，但扩展不再访问。')) return;
    await deleteProject(id);
    const next = await loadProjects();
    setProjects(next);
    onSwitch();
  };

  return (
    <div className="project-switcher" ref={ref}>
      <button
        type="button"
        className="project-switcher-trigger"
        onClick={() => setOpen((v) => !v)}
        aria-label="切换项目"
      >
        <span className="project-switcher-name">{active?.name ?? '未选项目'}</span>
        <span className="project-switcher-caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <ul className="project-switcher-menu" role="listbox">
          {projects.length === 0 && (
            <li className="project-switcher-empty">还没有项目</li>
          )}
          {projects.map((p) => (
            <li
              key={p.id}
              className={`project-switcher-item ${p.id === active?.id ? 'is-active' : ''}`}
              onClick={() => void onPick(p.id)}
              role="option"
              aria-selected={p.id === active?.id}
            >
              <span className="project-switcher-item-name">{p.name}</span>
              <button
                type="button"
                className="project-switcher-item-del"
                onClick={(e) => void onDelete(e, p.id)}
                aria-label="删除项目"
                title="删除"
              >×</button>
            </li>
          ))}
          <li className="project-switcher-divider" />
          <li
            className="project-switcher-new"
            onClick={() => { setOpen(false); onCreateNew(); }}
            role="option"
          >+ 新建项目</li>
        </ul>
      )}
    </div>
  );
}
