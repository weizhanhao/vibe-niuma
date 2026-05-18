// Plan 11 · M3.T23：AlertWebhookStep 单测。
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { AlertWebhookStep } from '../src/ui/components/AlertWebhookStep';

describe('AlertWebhookStep', () => {
  it('保存按钮初始禁用，识别钉钉 URL 后启用', () => {
    render(<AlertWebhookStep onComplete={vi.fn()} />);
    const save = screen.getByRole('button', { name: /保存/ });
    expect(save).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/Webhook URL/), {
      target: { value: 'https://oapi.dingtalk.com/robot/send?access_token=abc' },
    });
    expect(screen.getByText(/已识别：钉钉/)).toBeInTheDocument();
    expect(save).not.toBeDisabled();
  });

  it('识别飞书 + Discord', () => {
    const { rerender } = render(<AlertWebhookStep onComplete={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/Webhook URL/), {
      target: { value: 'https://open.feishu.cn/open-apis/bot/v2/hook/x' },
    });
    expect(screen.getByText(/已识别：飞书/)).toBeInTheDocument();
    rerender(<AlertWebhookStep onComplete={vi.fn()} initialUrl="" />);
    fireEvent.change(screen.getByLabelText(/Webhook URL/), {
      target: { value: 'https://discord.com/api/webhooks/1/abc' },
    });
    expect(screen.getByText(/已识别：Discord/)).toBeInTheDocument();
  });

  it('非识别 URL 显示错误且禁用保存', () => {
    render(<AlertWebhookStep onComplete={vi.fn()} />);
    fireEvent.change(screen.getByLabelText(/Webhook URL/), {
      target: { value: 'https://example.com/random' },
    });
    expect(screen.getByText(/不识别/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /保存/ })).toBeDisabled();
  });

  it('点跳过 → onComplete(空串)', () => {
    const onComplete = vi.fn();
    render(<AlertWebhookStep onComplete={onComplete} />);
    fireEvent.click(screen.getByRole('button', { name: /跳过/ }));
    expect(onComplete).toHaveBeenCalledWith('');
  });

  it('点保存 → onComplete(去空格的 URL)', () => {
    const onComplete = vi.fn();
    render(<AlertWebhookStep onComplete={onComplete} />);
    fireEvent.change(screen.getByLabelText(/Webhook URL/), {
      target: { value: '  https://oapi.dingtalk.com/robot/send?access_token=x  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));
    expect(onComplete).toHaveBeenCalledWith('https://oapi.dingtalk.com/robot/send?access_token=x');
  });

  it('allowSkip=false 时不显示跳过按钮', () => {
    render(<AlertWebhookStep onComplete={vi.fn()} allowSkip={false} />);
    expect(screen.queryByRole('button', { name: /跳过/ })).toBeNull();
  });
});
