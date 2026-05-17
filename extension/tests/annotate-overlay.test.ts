// AnnotateOverlay smoke 测试 —— jsdom 限制：canvas 拿不到真实像素，所以这里
// 只验：构造 / DOM 注入 / 工具切换 / 撤销 / 取消 / done 烘焙调用，不验真实绘画结果。
import { describe, it, expect, beforeEach, afterEach } from 'vitest';

import { AnnotateOverlay } from '../src/content/annotate-overlay';

// jsdom 不带 canvas API；fake 一个 ctx 让 overlay 跑起来不挂
function stubCanvas() {
  const noop = () => {};
  const stub: any = {
    drawImage: noop, strokeRect: noop, fillRect: noop, beginPath: noop,
    moveTo: noop, lineTo: noop, closePath: noop, fill: noop, stroke: noop,
    fillText: noop, strokeText: noop,
    getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    putImageData: noop,
    lineCap: '', lineJoin: '', lineWidth: 0, strokeStyle: '', fillStyle: '',
    font: '', textBaseline: '',
  };
  (HTMLCanvasElement.prototype as any).getContext = () => stub;
  (HTMLCanvasElement.prototype as any).toDataURL = () =>
    'data:image/png;base64,iVBORw0KGgo';
  return stub;
}

// 真实 Image 在 jsdom 里不会触发 onload；mock 它瞬间 load
function stubImage() {
  Object.defineProperty(window, 'Image', {
    writable: true,
    value: class {
      naturalWidth = 800;
      naturalHeight = 600;
      onload: (() => void) | null = null;
      onerror: (() => void) | null = null;
      set src(_v: string) {
        Promise.resolve().then(() => this.onload?.());
      }
    },
  });
}

describe('AnnotateOverlay', () => {
  beforeEach(() => {
    stubCanvas();
    stubImage();
  });
  afterEach(() => {
    document.documentElement.querySelectorAll('div').forEach((d) => d.remove());
  });

  it('start() 注入 overlay DOM 并显示 4 个工具按钮', async () => {
    const overlay = new AnnotateOverlay();
    void overlay.start('data:image/png;base64,xxx');
    await new Promise((r) => setTimeout(r, 10));

    const tools = document.querySelectorAll('[data-tool]');
    expect(tools.length).toBe(4);
    const kinds = Array.from(tools).map((b) => (b as HTMLButtonElement).dataset.tool);
    expect(kinds).toEqual(['rect', 'arrow', 'text', 'mosaic']);
  });

  it('cancel 按钮 resolve null', async () => {
    const overlay = new AnnotateOverlay();
    const p = overlay.start('data:image/png;base64,xxx');
    await new Promise((r) => setTimeout(r, 10));

    const cancelBtn = Array.from(document.querySelectorAll('button'))
      .find((b) => b.textContent === '取消') as HTMLButtonElement;
    expect(cancelBtn).toBeTruthy();
    cancelBtn.click();
    const result = await p;
    expect(result).toBeNull();
  });

  it('完成按钮 resolve canvas toDataURL', async () => {
    const overlay = new AnnotateOverlay();
    const p = overlay.start('data:image/png;base64,xxx');
    await new Promise((r) => setTimeout(r, 10));

    const doneBtn = Array.from(document.querySelectorAll('button'))
      .find((b) => b.textContent === '✓ 完成') as HTMLButtonElement;
    doneBtn.click();
    const result = await p;
    expect(result).toBe('data:image/png;base64,iVBORw0KGgo');
  });

  it('Esc 触发 cancel', async () => {
    const overlay = new AnnotateOverlay();
    const p = overlay.start('data:image/png;base64,xxx');
    await new Promise((r) => setTimeout(r, 10));

    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    const result = await p;
    expect(result).toBeNull();
  });

  it('点击工具按钮切换 selected 高亮', async () => {
    const overlay = new AnnotateOverlay();
    void overlay.start('data:image/png;base64,xxx');
    await new Promise((r) => setTimeout(r, 10));

    const arrowBtn = document.querySelector('[data-tool="arrow"]') as HTMLButtonElement;
    arrowBtn.click();
    // selected 状态用 background 标识（rgba(10,132,255,0.5)）
    expect(arrowBtn.style.background).toContain('rgba(10, 132, 255');
    // 默认 rect 按钮应该不再高亮
    const rectBtn = document.querySelector('[data-tool="rect"]') as HTMLButtonElement;
    expect(rectBtn.style.background).not.toContain('rgba(10, 132, 255');
  });
});
