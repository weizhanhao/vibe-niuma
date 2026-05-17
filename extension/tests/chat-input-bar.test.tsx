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

  it('commander footer 只显示项目 chip，不显示 mode 分类（业务员视角无意义）', () => {
    render(
      <ChatInputBar
        attachments={[]}
        onAttachmentsChange={() => {}}
        initialText="加搜索"
        projectName="默认项目"
      />,
    );
    expect(screen.getByText('默认项目')).toBeInTheDocument();
    // mode chip（新需求/续改/聊天）业务员看不懂、易误解，已移除
    expect(screen.queryByText(/新需求|续改|聊天/)).toBeNull();
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

  it('Enter 直接发送（业务员实测反馈：不要 ⌘+回车）', async () => {
    render(
      <ChatInputBar
        attachments={[]}
        onAttachmentsChange={() => {}}
        initialText="hi"
        conversationId="conv-x"
      />,
    );
    const ta = screen.getByLabelText(/输入需求/) as HTMLTextAreaElement;
    fireEvent.keyDown(ta, { key: 'Enter' });
    await Promise.resolve();
    await Promise.resolve();
    const submit = sent.find(
      (m) => (m as { type?: string }).type === 'SUBMIT_MESSAGE',
    );
    expect(submit).toBeTruthy();
  });

  it('⇧+回车 不发送，保留换行能力', async () => {
    render(
      <ChatInputBar
        attachments={[]}
        onAttachmentsChange={() => {}}
        initialText="line1"
        conversationId="conv-x"
      />,
    );
    const ta = screen.getByLabelText(/输入需求/) as HTMLTextAreaElement;
    fireEvent.keyDown(ta, { key: 'Enter', shiftKey: true });
    await Promise.resolve();
    const submit = sent.find(
      (m) => (m as { type?: string }).type === 'SUBMIT_MESSAGE',
    );
    expect(submit).toBeUndefined();
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

  it('「+」 button removed — replaced by screenshot tool (TODO)', () => {
    // 旧版「+」按钮是个死 stub（label「添加附件」）。已删除等截图+标注工具
    // 一并重做。这条用 queryBy 断言它不存在，未来加回时改这条而不是新增。
    render(
      <ChatInputBar
        attachments={[mkAtt('A'), mkAtt('B'), mkAtt('C')]}
        onAttachmentsChange={() => {}}
      />,
    );
    expect(screen.queryByLabelText(/添加附件/)).toBeNull();
  });
});
