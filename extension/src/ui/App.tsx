// 主路由（Plan 9 后）：
//   - 加载中 → 占位
//   - 没项目 → ProjectSelectorPanel
//   - 正在新建项目 → CreateProjectPanel
//   - 有 active project + config 全 → MainShell（head 含 ProjectSwitcher）
//   - 有 active project 但 config 残缺 → CreateProjectPanel（修补）
//
// 兼容老用户：boot 时 migrateLegacyConfig 把 doskill_config_v2 包成「默认项目」+
// setActive。setActive 内部同步 project.config → doskill_config_v2 让 service-worker
// 不必改读路径。
import React, { useEffect, useState } from 'react';
import { isConfigSufficient, loadConfig, type Config } from '../lib/config';
import {
  loadActiveProject, loadProjects, migrateLegacyConfig, type Project,
} from '../lib/projects';
import { AgentTabBar, type AgentTab } from './components/AgentTabBar';
import { ChatInputBar } from './components/ChatInputBar';
import { ChatStream } from './components/ChatStream';
import { HistoryDropdown } from './components/HistoryDropdown';
import { PreviewDock } from './components/PreviewDock';
import { ProjectSwitcher } from './components/ProjectSwitcher';
import {
  createConversation, listConversations, type Conversation,
} from '../lib/conversations';
import type { Attachment } from '../lib/types';
import { useActiveConversation } from './hooks/useActiveConversation';
import { useMirrors } from './hooks/useMirrors';
import { usePendingCapture } from './hooks/usePendingCapture';
import { useRequestState } from './hooks/useRequestState';
import { useTabs } from './hooks/useTabs';
import {
  ClarifyPanel, FailedPanel, ReviewCapturePanel,
  StatusPanel, VariantsPanel,
} from './panels';
import { CreateProjectPanel } from './panels/CreateProjectPanel';
import { ProjectSelectorPanel } from './panels/ProjectSelectorPanel';
import { SettingsPanel } from './panels/SettingsPanel';
import type { ChangeRequestState } from '../lib/types';
import { loadTheme, readThemeSync, saveTheme, type Theme } from '../lib/theme';

// loading sentinel：避免在 cfg 还没拉到时短暂渲染 wizard 闪一下。
type ConfigState = 'loading' | Config | null;
const CONFIG_STORAGE_KEY = 'doskill_config_v2';

// FSM → head 状态徽章（READY/RUNNING/SELECTING/MERGED/FAIL）。design 里 head 右侧 pill。
function statusPillFromState(state: ChangeRequestState | null, pendingCapture: boolean): { label: string; tone: string } {
  if (pendingCapture) return { label: 'SELECTING', tone: 'tone-warn' };
  if (!state) return { label: 'READY', tone: '' };
  switch (state) {
    case 'created':
    case 'clarifying':
    case 'located':
    case 'coding':
    case 'building':
      return { label: 'RUNNING', tone: 'tone-running' };
    case 'preview-ready':
      return { label: 'READY', tone: '' };
    case 'merged':
      return { label: 'MERGED', tone: 'tone-merged' };
    case 'failed':
    case 'expired':
      return { label: 'FAIL', tone: 'tone-fail' };
    case 'discarded':
      return { label: 'READY', tone: '' };
  }
}

function GearIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true">
      <circle cx="7" cy="7" r="1.6" />
      <path d="M7 1v1.6M7 11.4V13M1 7h1.6M11.4 7H13M2.76 2.76l1.13 1.13M10.11 10.11l1.13 1.13M2.76 11.24l1.13-1.13M10.11 3.89l1.13-1.13" />
    </svg>
  );
}

function SunIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true">
      <circle cx="7" cy="7" r="2.4" />
      <path d="M7 0.6V2M7 12V13.4M0.6 7H2M12 7H13.4M2.46 2.46l1l1M10.54 10.54l1 1M2.46 11.54l1-1M10.54 3.46l1-1" />
    </svg>
  );
}
function MoonIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.2" aria-hidden="true">
      <path d="M11.5 8.4A4.6 4.6 0 0 1 5.6 2.5a.5.5 0 0 0-.66-.6 5.6 5.6 0 1 0 7.16 7.16.5.5 0 0 0-.6-.66z" />
    </svg>
  );
}

// 小型主题切换钩子 —— App 启动校正 + 提供 toggle。
function useThemeToggle(): [Theme, () => void] {
  // 初始值用 sync read，避免首帧错主题
  const [theme, setTheme] = useState<Theme>(() => readThemeSync());
  useEffect(() => {
    // mount 后从 chrome.storage 校正一次（别处可能改过）
    let mounted = true;
    void loadTheme().then((t) => {
      if (mounted && t !== theme) setTheme(t);
    });
    return () => { mounted = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  const toggle = () => {
    const next: Theme = theme === 'light' ? 'dark' : 'light';
    setTheme(next);
    void saveTheme(next);
  };
  return [theme, toggle];
}

type AppBootState =
  | { kind: 'loading' }
  | { kind: 'no-projects' }                       // 一个都没有
  | { kind: 'no-active'; first: Project | null }  // 有 projects 但 active 是 null（删完最后一个、或主动切空）
  | { kind: 'creating' }                          // 正在新建
  | { kind: 'has-active'; project: Project; config: Config | null };

export function App() {
  const [boot, setBoot] = useState<AppBootState>({ kind: 'loading' });

  const reload = async () => {
    // 启动时一次性迁移老 doskill_config_v2 → Project（幂等）
    await migrateLegacyConfig();
    const projects = await loadProjects();
    if (projects.length === 0) {
      setBoot({ kind: 'no-projects' });
      return;
    }
    const active = await loadActiveProject();
    if (!active) {
      setBoot({ kind: 'no-active', first: projects[0] });
      return;
    }
    const cfg = await loadConfig();
    setBoot({ kind: 'has-active', project: active, config: cfg });
  };

  useEffect(() => {
    void reload();
    const listener = (
      changes: Record<string, chrome.storage.StorageChange>,
      area: string,
    ) => {
      if (area !== 'local') return;
      // 任一相关 key 变化都重 load
      if (
        'doskill_projects' in changes ||
        'doskill_active_project_id' in changes ||
        'doskill_config_v2' in changes
      ) {
        void reload();
      }
    };
    chrome.storage?.onChanged?.addListener?.(listener);
    return () => chrome.storage?.onChanged?.removeListener?.(listener);
  }, []);

  if (boot.kind === 'loading') {
    return <div className="app"><div className="app-body"><p className="help">加载中…</p></div></div>;
  }

  if (boot.kind === 'creating') {
    return (
      <div className="app">
        <CreateProjectPanel
          onDone={() => { void reload(); }}
          onCancel={() => { void reload(); }}
        />
      </div>
    );
  }

  if (boot.kind === 'no-projects' || boot.kind === 'no-active') {
    return (
      <div className="app">
        <ProjectSelectorPanel
          onPicked={() => { void reload(); }}
          onCreateNew={() => setBoot({ kind: 'creating' })}
        />
      </div>
    );
  }

  // has-active：config 可能不全（项目刚迁移过来 / 删了 key）→ 进 CreateProject 修补
  if (!isConfigSufficient(boot.config)) {
    return (
      <div className="app">
        <CreateProjectPanel
          onDone={() => { void reload(); }}
          onCancel={() => { void reload(); }}
        />
      </div>
    );
  }

  return <MainShell project={boot.project} onCreateProject={() => setBoot({ kind: 'creating' })} onSwitch={() => { void reload(); }} />;
}

// ── 主壳：拆出去是为了让 wizard 那条 return 干净，避免 hooks 顺序在 wizard / 主面板之间漂移 ──
interface MainShellProps {
  project: Project;
  onCreateProject: () => void;
  onSwitch: () => void;
}
function MainShell({ project, onCreateProject, onSwitch }: MainShellProps) {
  const state = useRequestState();
  const { mirrors, activeId } = useMirrors();
  const pendingCapture = usePendingCapture();
  const [showSettings, setShowSettings] = useState(false);
  const [theme, toggleTheme] = useThemeToggle();
  // 用户关掉的 PreviewDock id 集合（merged/discarded 后业务员主动 ×）。仅 in-memory：
  // 关闭窗口再开浮卡又会回来 —— 业务员还想看就再切；不想看就持续 ×（轻量决策）。
  const [closedDockIds, setClosedDockIds] = useState<Set<string>>(() => new Set());

  // ── Plan 10 Task 17: cursor-like tabs + chat stream + attachments ──
  const { state: tabsState, open: openTab, close: closeTab, activate: activateTab } = useTabs();
  // 拉历史 conversations 喂 HistoryDropdown；conv 标题 → AgentTabBar 显示。
  const [convCache, setConvCache] = useState<Record<string, Conversation>>({});
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyItems, setHistoryItems] = useState<Conversation[] | null>(null);
  // ChatInputBar attachments tray —— state lifted 到 MainShell
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  // 发送成功后递增，触发 useActiveConversation 立即 refetch，业务员看到自己刚发的消息
  const [submitTick, setSubmitTick] = useState(0);

  // 拉一次完整 conversation list，用来给历史下拉 + tab 标题
  useEffect(() => {
    void listConversations()
      .then((items) => {
        const cache: Record<string, Conversation> = {};
        for (const c of items) cache[c.id] = c;
        setConvCache(cache);
      })
      .catch(() => { /* offline / 未配 orchestrator */ });
  }, []);

  // 切 tab 时给 SW 发 SET_CONVERSATION（已经由 useTabs 自动做了），同时
  // 拉 active conversation messages 给 ChatStream
  const activeConvId = tabsState.activeTabId;
  // refreshKey: mirrors 数 + 当前 active mirror state 变化 → 重新拉 messages
  // （server 在 pipeline 推进 / refine / chat_only 时会写 ai message 到 conv）
  const refreshKey = `${mirrors.length}:${activeId ?? ''}:${
    state ? state.state : ''
  }:${submitTick}`;
  const { messages } = useActiveConversation(activeConvId, refreshKey);

  const handleNewConversation = async () => {
    try {
      const conv = await createConversation('');
      setConvCache((prev) => ({ ...prev, [conv.id]: conv }));
      await openTab(conv.id);
    } catch (err) {
      console.warn('[doskill ui] createConversation failed', err);
    }
  };

  const handleShowHistory = async () => {
    setHistoryOpen(true);
    setHistoryItems(null);
    try {
      const items = await listConversations();
      setHistoryItems(items);
      const cache: Record<string, Conversation> = {};
      for (const c of items) cache[c.id] = c;
      setConvCache(cache);
    } catch {
      setHistoryItems([]);
    }
  };

  const handlePickHistory = async (convId: string) => {
    await openTab(convId);  // open + activate
    setHistoryOpen(false);
  };

  const agentTabs: AgentTab[] = tabsState.openTabIds.map((id) => ({
    id,
    title: convCache[id]?.title ?? '',
  }));
  // mirrors 转成 dict 喂 ChatStream
  const mirrorDict = Object.fromEntries(mirrors.map((m) => [m.id, m]));

  // ── Plan 10 fix A1: tab 维度的 state，不再用 SW 全局 active mirror ──
  // useRequestState() 返的是 SW 上一次 attachSubscription 的 mirror —— 切 tab
  // 时如果 SW 还没收到新 SUBMIT_MESSAGE 它不会变。我们要的是「当前这个 tab
  // 对应的 conversation 的最后一个 CR」的状态。
  // 算法：扫 conversation messages 倒序找第一个带 cr_id 的 user message。
  const tabActiveCrId = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const m = messages[i];
      if (m.type === 'user' && m.cr_id) return m.cr_id;
    }
    return null;
  })();
  const tabState = tabActiveCrId ? (mirrorDict[tabActiveCrId] ?? null) : null;

  if (showSettings) {
    return (
      <div className="app">
        <header className="app-head">
          <span className="app-logo">d</span>
          <div className="app-title">
            <div>DO<em>SKILL</em></div>
            <span className="muted">settings</span>
          </div>
          <div className="app-status"><span className="pulse" />SETTINGS</div>
          <div className="app-head-actions">
            <button
              className="app-gear"
              aria-label={theme === 'light' ? '切换深色' : '切换浅色'}
              title={theme === 'light' ? '切换深色' : '切换浅色'}
              onClick={toggleTheme}
            >{theme === 'light' ? <MoonIcon /> : <SunIcon />}</button>
            <button
              className="app-gear"
              aria-label="关闭设置"
              onClick={() => setShowSettings(false)}
            >×</button>
          </div>
        </header>
        <div className="app-body">
          <SettingsPanel onClose={() => setShowSettings(false)} />
        </div>
      </div>
    );
  }

  // Cursor 风格：body 默认是 ChatStream；只有「澄清 / 变体选择 / Review」
  // 这类需要业务员强决策的面板会临时覆盖。pipeline 进行中（coding/building）
  // 的状态由 InlineCard 在 stream 里显示，body 不再需要专门 StatusPanel。
  // FIX A1：判断 panel 用 tabState（当前 tab 的 CR），不用 SW 全局 state，
  // 避免「切 tab body 还卡在上一个 CR 的 FailedPanel」。
  let body: React.ReactNode = null;
  if (pendingCapture) {
    body = <ReviewCapturePanel pendingCapture={pendingCapture} />;
  } else if (tabState?.pendingVariants) {
    body = <VariantsPanel state={tabState} />;
  } else if (tabState?.pendingQuestion) {
    body = <ClarifyPanel state={tabState} />;
  } else if (tabState && (tabState.state === 'failed' || tabState.state === 'expired')) {
    body = <FailedPanel state={tabState} />;
  } else if (activeConvId) {
    body = <ChatStream messages={messages} mirrors={mirrorDict} />;
  } else {
    body = <IdleHint />;
  }

  // PreviewDock 数据源：**当前 tab 的 conversation** 里最近一条带 preview_url
  // 且没被业务员关掉的 CR。切 tab 时浮卡跟着切，不再串味。
  const dockMirror = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      const cid = messages[i].cr_id;
      if (!cid || closedDockIds.has(cid)) continue;
      const m = mirrorDict[cid];
      if (m?.previewUrl) return m;
    }
    return null;
  })();
  const closeDock = dockMirror
    ? () => setClosedDockIds((prev) => new Set(prev).add(dockMirror.id))
    : undefined;

  // pill 也按 tabState（当前 tab 的 CR），切 tab 时 pill 跟着切
  const pill = statusPillFromState(tabState ? tabState.state : null, !!pendingCapture);

  // ReviewCapturePanel 自有输入框，再放底部 ChatInputBar 是噪音；其他都显示。
  const showInputBar = !pendingCapture;

  return (
    <div className="app">
      <header className="app-head">
        <span className="app-logo">d</span>
        <ProjectSwitcher
          active={project}
          onSwitch={onSwitch}
          onCreateNew={onCreateProject}
        />
        <div className={`app-status ${pill.tone}`}><span className="pulse" />{pill.label}</div>
        <div className="app-head-actions">
          <button
            className="app-gear"
            aria-label={theme === 'light' ? '切换深色' : '切换浅色'}
            title={theme === 'light' ? '切换深色' : '切换浅色'}
            onClick={toggleTheme}
          >{theme === 'light' ? <MoonIcon /> : <SunIcon />}</button>
          <button
            className="app-gear"
            aria-label="设置"
            onClick={() => setShowSettings(true)}
          ><GearIcon /></button>
        </div>
      </header>
      <div className="agent-tab-bar-wrap">
        <AgentTabBar
          tabs={agentTabs}
          activeTabId={activeConvId}
          onActivate={(id) => { void activateTab(id); }}
          onClose={(id) => { void closeTab(id); }}
          onNew={() => { void handleNewConversation(); }}
          onShowHistory={() => { void handleShowHistory(); }}
        />
        {historyOpen && (
          <HistoryDropdown
            items={historyItems}
            onPick={(id) => { void handlePickHistory(id); }}
            onClose={() => setHistoryOpen(false)}
          />
        )}
      </div>
      <div className="app-body">{body}</div>
      <footer className="app-footer">
        <PreviewDock state={dockMirror} onClose={closeDock} />
        {showInputBar && (
          <ChatInputBar
            attachments={attachments}
            onAttachmentsChange={setAttachments}
            conversationId={activeConvId}
            onSubmitted={() => setSubmitTick((t) => t + 1)}
          />
        )}
      </footer>
    </div>
  );
}

// 空状态提示：还没起任何 CR 时 body 显示一行欢迎语；具体输入在底部 ChatInputBar。
function IdleHint() {
  return (
    <section className="idle-hint">
      <h3 className="title">想改这个页面的哪里？</h3>
      <p className="help">在下方输入你想看到的变化 —— 用大白话就行。AI 按 URL 自定位，
        想精准定位就点「框选」拖一个框。</p>
    </section>
  );
}
