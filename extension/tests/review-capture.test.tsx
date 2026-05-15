// Phase G：ReviewCapturePanel —— 框选完弹出的确认页。
// 校验：截图 + 蓝框定位（按 viewport 比例缩放）+ 重新框选 / 确认提交 两按钮。
import { fireEvent, render, screen, act } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ReviewCapturePanel } from '../src/ui/panels';
import type { PendingCapture } from '../src/lib/types';

const mkPending = (overrides: Partial<PendingCapture> = {}): PendingCapture => ({
  screenshotB64: 'AAAA',
  url: 'http://demo.local/orders',
  boxCoords: { x: 100, y: 50, width: 200, height: 100 },
  viewport: { width: 1000, height: 800 },
  requestText: '把这个按钮改红',
  ...overrides,
});

describe('ReviewCapturePanel', () => {
  it('renders 截图 + URL + 视口 + 框选 + 业务需求', () => {
    render(<ReviewCapturePanel pendingCapture={mkPending()} />);
    // 截图：data: URL
    const img = screen.getByAltText('页面截图') as HTMLImageElement;
    expect(img.src.startsWith('data:image/png;base64,')).toBe(true);
    // 元信息
    expect(screen.getByText(/http:\/\/demo\.local\/orders/)).toBeInTheDocument();
    expect(screen.getByText(/1000×800/)).toBeInTheDocument();
    expect(screen.getByText(/200×100 px/)).toBeInTheDocument();
    // 业务需求 textarea 默认填充
    expect((screen.getByLabelText('业务需求') as HTMLTextAreaElement).value).toBe('把这个按钮改红');
    // 蓝框存在
    expect(screen.getByTestId('review-box')).toBeInTheDocument();
  });

  it('蓝框按 viewport 比例缩放', () => {
    // 假定 img 渲染后 boundingRect 宽 = 500。viewport=1000 → scale=0.5。
    // box (100,50,200,100) → left=50, top=25, width=100, height=50。
    render(<ReviewCapturePanel pendingCapture={mkPending()} />);
    const img = screen.getByAltText('页面截图') as HTMLImageElement;
    // 替身 getBoundingClientRect —— jsdom 默认全 0。
    img.getBoundingClientRect = () => ({
      x: 0, y: 0, width: 500, height: 200, top: 0, left: 0, right: 500, bottom: 200, toJSON: () => ({}),
    });
    act(() => { fireEvent.load(img); });
    const box = screen.getByTestId('review-box') as HTMLDivElement;
    expect(box.style.left).toBe('50px');
    expect(box.style.top).toBe('25px');
    expect(box.style.width).toBe('100px');
    expect(box.style.height).toBe('50px');
  });

  it('点「重新框选」发 RETAKE_CAPTURE', () => {
    const sent: unknown[] = [];
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((m) => {
      sent.push(m);
      return Promise.resolve();
    });
    render(<ReviewCapturePanel pendingCapture={mkPending()} />);
    fireEvent.click(screen.getByRole('button', { name: /重新框选/ }));
    expect(sent[0]).toEqual({ type: 'RETAKE_CAPTURE' });
  });

  it('点「确认提交」发 CONFIRM_CAPTURE 带 requestText', () => {
    const sent: unknown[] = [];
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((m) => {
      sent.push(m);
      return Promise.resolve();
    });
    render(<ReviewCapturePanel pendingCapture={mkPending()} />);
    fireEvent.click(screen.getByRole('button', { name: /确认提交/ }));
    expect(sent[0]).toEqual({ type: 'CONFIRM_CAPTURE', requestText: '把这个按钮改红' });
  });

  it('业务员在 review 阶段编辑了 textarea —— 编辑后的文本被回传给 CONFIRM_CAPTURE', () => {
    const sent: unknown[] = [];
    vi.mocked(chrome.runtime.sendMessage).mockImplementation((m) => {
      sent.push(m);
      return Promise.resolve();
    });
    render(<ReviewCapturePanel pendingCapture={mkPending()} />);
    fireEvent.change(screen.getByLabelText('业务需求'), { target: { value: '改成绿色' } });
    fireEvent.click(screen.getByRole('button', { name: /确认提交/ }));
    expect(sent[0]).toMatchObject({ type: 'CONFIRM_CAPTURE', requestText: '改成绿色' });
  });

  it('需求文本为空时「确认提交」按钮禁用', () => {
    render(<ReviewCapturePanel pendingCapture={mkPending({ requestText: '' })} />);
    const btn = screen.getByRole('button', { name: /确认提交/ });
    expect(btn).toBeDisabled();
  });
});
