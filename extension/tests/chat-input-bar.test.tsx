// Plan 10 Task 16: ChatInputBar 升级 —— AttachmentTray + mode badge + send 走 SUBMIT_MESSAGE.
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatInputBar } from '../src/ui/components/ChatInputBar';
import type { Attachment } from '../src/lib/types';

const sent: unknown[] = [];

beforeEach(() => {
  sent.length = 0;
  vi.mocked(chrome.runtime.sendMessage).mockImplementation((m) => {
    sent.push(m);
    return Promise.resolve({ ok: true });
  });
});

afterEach(() => {
  vi.restoreAllMocks();
});

function mkAtt(b64: string): Attachment {
  return { kind: 'pasted_image', mime: 'image/png', b64 };
}

describe('ChatInputBar attachments + mode', () => {
  it('renders without attachments by default', () => {
    render(<ChatInputBar attachments={[]} onAttachmentsChange={() => {}} />);
    expect(screen.queryByLabelText(/移除附件/)).toBeNull();
  });

  it('renders one chip per attachment', () => {
    render(
      <ChatInputBar
        attachments={[mkAtt('A'), mkAtt('B'), mkAtt('C')]}
        onAttachmentsChange={() => {}}
      />,
    );
    expect(screen.getAllByLabelText(/移除附件/)).toHaveLength(3);
  });

  it('clicking × on a chip fires onAttachmentsChange without that one', () => {
    const onChange = vi.fn();
    render(
      <ChatInputBar
        attachments={[mkAtt('A'), mkAtt('B'), mkAtt('C')]}
        onAttachmentsChange={onChange}
      />,
    );
    const removes = screen.getAllByLabelText(/移除附件/);
    fireEvent.click(removes[1]);
    expect(onChange).toHaveBeenCalledWith([mkAtt('A'), mkAtt('C')]);
  });

  it('shows mode badge based on classifyIntent (heuristic preview)', () => {
    render(
      <ChatInputBar
        attachments={[]}
        onAttachmentsChange={() => {}}
        initialText="加搜索"
      />,
    );
    expect(screen.getByText(/新需求|new_cr/i)).toBeInTheDocument();
  });

  it('sending text dispatches SUBMIT_MESSAGE with attachments + convId', async () => {
    render(
      <ChatInputBar
        attachments={[mkAtt('AAA')]}
        onAttachmentsChange={() => {}}
        initialText="加个搜索"
        conversationId="conv-abc"
      />,
    );
    const sendBtn = screen.getByRole('button', { name: /发送|提交|→/ });
    fireEvent.click(sendBtn);
    // await microtask flush so the async submit handler runs
    await Promise.resolve();
    await Promise.resolve();
    const submitMsg = sent.find(
      (m) => (m as { type?: string }).type === 'SUBMIT_MESSAGE',
    ) as { text: string; attachments?: Attachment[]; conversation_id?: string } | undefined;
    expect(submitMsg).toBeTruthy();
    expect(submitMsg!.text).toBe('加个搜索');
    expect(submitMsg!.conversation_id).toBe('conv-abc');
    expect(submitMsg!.attachments).toHaveLength(1);
    expect(submitMsg!.attachments![0].b64).toBe('AAA');
  });

  it('cmd+enter submits as well', async () => {
    render(
      <ChatInputBar
        attachments={[]}
        onAttachmentsChange={() => {}}
        initialText="hi"
        conversationId="conv-x"
      />,
    );
    const ta = screen.getByLabelText(/业务需求/) as HTMLTextAreaElement;
    fireEvent.keyDown(ta, { key: 'Enter', metaKey: true });
    await Promise.resolve();
    await Promise.resolve();
    const submit = sent.find(
      (m) => (m as { type?: string }).type === 'SUBMIT_MESSAGE',
    );
    expect(submit).toBeTruthy();
  });

  it('submit without conversationId is no-op (warns)', () => {
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
    render(
      <ChatInputBar
        attachments={[]}
        onAttachmentsChange={() => {}}
        initialText="hi"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /发送|提交|→/ }));
    expect(sent.find((m) => (m as { type?: string }).type === 'SUBMIT_MESSAGE'))
      .toBeUndefined();
    warnSpy.mockRestore();
  });

  it('does not exceed MAX_ATTACHMENTS=3 — input file picker disabled', () => {
    render(
      <ChatInputBar
        attachments={[mkAtt('A'), mkAtt('B'), mkAtt('C')]}
        onAttachmentsChange={() => {}}
      />,
    );
    const addBtn = screen.getByLabelText(/添加附件/);
    expect(addBtn).toBeDisabled();
  });
});
