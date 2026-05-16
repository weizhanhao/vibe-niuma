// Plan 9 Task 10: conversations client —— /conversations REST 包装。
import { loadConfig } from './config';

export interface Message {
  type: 'user' | 'ai' | 'summary';
  ts: string;
  content: string;
  cr_id?: string;
  replaces_count?: number;
  replaces_token_estimate?: number;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string | null;
  updated_at: string | null;
  archived_at: string | null;
  messages: Message[];
}

async function authHeaders(): Promise<HeadersInit> {
  const cfg = await loadConfig();
  if (!cfg) return {};
  return { 'X-Admin-Token': cfg.adminToken };
}

async function baseUrl(): Promise<string> {
  const cfg = await loadConfig();
  if (!cfg) throw new Error('未配置 orchestrator URL');
  return cfg.orchestratorUrl.replace(/\/$/, '');
}

export async function listConversations(): Promise<Conversation[]> {
  const url = await baseUrl();
  const r = await fetch(`${url}/conversations?archived=false`, {
    headers: await authHeaders(),
  });
  if (!r.ok) throw new Error(`列对话失败: ${r.status}`);
  const body = await r.json() as { items: Conversation[] };
  return body.items;
}

export async function getConversation(id: string): Promise<Conversation> {
  const url = await baseUrl();
  const r = await fetch(`${url}/conversations/${id}`, { headers: await authHeaders() });
  if (!r.ok) throw new Error(`取对话失败: ${r.status}`);
  return await r.json();
}

export async function createConversation(title: string = ''): Promise<Conversation> {
  const url = await baseUrl();
  const r = await fetch(`${url}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(await authHeaders()) },
    body: JSON.stringify({ title }),
  });
  if (!r.ok) throw new Error(`创建对话失败: ${r.status}`);
  return await r.json();
}

export async function archiveConversation(id: string): Promise<void> {
  const url = await baseUrl();
  const r = await fetch(`${url}/conversations/${id}/archive`, {
    method: 'POST',
    headers: await authHeaders(),
  });
  if (!r.ok) throw new Error(`归档对话失败: ${r.status}`);
}
