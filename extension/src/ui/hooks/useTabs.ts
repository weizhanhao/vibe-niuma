// Plan 10 Task 17: useTabs —— UI 侧的 tabs 状态 hook。
//
// 读 lib/tabs.ts 的存储；提供 open/close/setActive 包装；切换时同步
// SET_CONVERSATION 到 SW，让 SUBMIT_MESSAGE 知道 convId。
import { useCallback, useEffect, useState } from 'react';
import {
  closeTab as closeTabStore,
  loadTabs, openTab as openTabStore, saveTabs,
  setActiveTab as setActiveTabStore,
} from '../../lib/tabs';
import type { TabsState } from '../../lib/types';

const STORAGE_KEY = 'doskill.tabsState.v1';

function notifySW(id: string | null): void {
  try {
    chrome.runtime?.sendMessage?.({ type: 'SET_CONVERSATION', id });
  } catch {
    /* ignore */
  }
}

export function useTabs() {
  const [state, setState] = useState<TabsState>({
    openTabIds: [], activeTabId: null, lastUsedAt: {},
  });

  useEffect(() => {
    let mounted = true;
    void loadTabs().then((t) => {
      if (mounted) {
        setState(t);
        notifySW(t.activeTabId);
      }
    });
    const onChanged = (
      changes: Record<string, chrome.storage.StorageChange>, area: string,
    ) => {
      if (area === 'local' && STORAGE_KEY in changes) {
        const v = changes[STORAGE_KEY].newValue as TabsState | undefined;
        if (v) setState(v);
      }
    };
    chrome.storage?.onChanged?.addListener?.(onChanged);
    return () => {
      mounted = false;
      chrome.storage?.onChanged?.removeListener?.(onChanged);
    };
  }, []);

  const open = useCallback(async (id: string) => {
    const next = await openTabStore(id);
    setState(next);
    notifySW(next.activeTabId);
  }, []);

  const close = useCallback(async (id: string) => {
    const next = await closeTabStore(id);
    setState(next);
    notifySW(next.activeTabId);
  }, []);

  const activate = useCallback(async (id: string | null) => {
    const next = await setActiveTabStore(id);
    setState(next);
    notifySW(next.activeTabId);
  }, []);

  const clearAll = useCallback(async () => {
    const empty: TabsState = { openTabIds: [], activeTabId: null, lastUsedAt: {} };
    await saveTabs(empty);
    setState(empty);
    notifySW(null);
  }, []);

  return { state, open, close, activate, clearAll };
}
