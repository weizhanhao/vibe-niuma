// 注入到目标页面的框选 overlay。负责拖拽选框、采集 boxCoords + viewport。
import type { BoxCoords, Viewport } from '../lib/types';

export interface CaptureResult {
  boxCoords: BoxCoords;
  viewport: Viewport;
}

export class CaptureOverlay {
  private root: HTMLDivElement | null = null;
  private box: HTMLDivElement | null = null;
  private dragStart: { x: number; y: number } | null = null;
  private dragEnd: { x: number; y: number } | null = null;
  private resolveFn: ((r: CaptureResult | null) => void) | null = null;

  start(): Promise<CaptureResult | null> {
    if (this.root) this.dispose();
    this.root = document.createElement('div');
    this.root.setAttribute('data-doskill-overlay', '1');
    Object.assign(this.root.style, {
      position: 'fixed', inset: '0',
      background: 'rgba(20, 22, 30, 0.28)',
      cursor: 'crosshair', zIndex: '2147483647',
    });
    this.box = document.createElement('div');
    Object.assign(this.box.style, {
      position: 'fixed',
      border: '2px solid #4f6bff',
      background: 'rgba(79, 107, 255, 0.10)',
      pointerEvents: 'none',
      display: 'none',
    });
    this.root.appendChild(this.box);
    document.documentElement.appendChild(this.root);

    this.root.addEventListener('mousedown', this.onMouseDown);
    window.addEventListener('mousemove', this.onMouseMove);
    window.addEventListener('mouseup', this.onMouseUp);
    window.addEventListener('keydown', this.onKey);

    return new Promise((resolve) => { this.resolveFn = resolve; });
  }

  private onMouseDown = (e: MouseEvent) => {
    e.preventDefault();
    this.dragStart = { x: e.clientX, y: e.clientY };
    if (this.box) {
      this.box.style.display = 'block';
      this.setBox(e.clientX, e.clientY, 0, 0);
    }
    console.log('[doskill overlay] mousedown', e.clientX, e.clientY);
  };

  private onMouseMove = (e: MouseEvent) => {
    if (!this.dragStart || !this.box) return;
    this.dragEnd = { x: e.clientX, y: e.clientY };
    const x = Math.min(this.dragStart.x, e.clientX);
    const y = Math.min(this.dragStart.y, e.clientY);
    const w = Math.abs(e.clientX - this.dragStart.x);
    const h = Math.abs(e.clientY - this.dragStart.y);
    this.setBox(x, y, w, h);
  };

  private onMouseUp = (e: MouseEvent) => {
    console.log('[doskill overlay] mouseup', e.clientX, e.clientY, 'dragStart=', this.dragStart);
    if (!this.dragStart || !this.box) return;
    const end = this.dragEnd ?? { x: e.clientX, y: e.clientY };
    const x = Math.min(this.dragStart.x, end.x);
    const y = Math.min(this.dragStart.y, end.y);
    const w = Math.abs(end.x - this.dragStart.x);
    const h = Math.abs(end.y - this.dragStart.y);
    this.dragStart = null;
    this.dragEnd = null;
    console.log('[doskill overlay] computed wxh=', w, h);
    if (w < 6 || h < 6) {
      this.box.style.display = 'none';
      console.log('[doskill overlay] BELOW 6px threshold, ignored');
      return;
    }
    this.finish({
      boxCoords: { x, y, width: w, height: h },
      viewport: { width: window.innerWidth, height: window.innerHeight },
    });
  };

  private onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') this.finish(null);
  };

  private setBox(x: number, y: number, w: number, h: number) {
    if (!this.box) return;
    this.box.style.left = `${x}px`;
    this.box.style.top = `${y}px`;
    this.box.style.width = `${w}px`;
    this.box.style.height = `${h}px`;
  }

  private finish(result: CaptureResult | null) {
    const fn = this.resolveFn;
    this.resolveFn = null;
    this.dispose();
    fn?.(result);
  }

  dispose() {
    if (this.root) {
      this.root.removeEventListener('mousedown', this.onMouseDown);
      this.root.remove();
    }
    window.removeEventListener('mousemove', this.onMouseMove);
    window.removeEventListener('mouseup', this.onMouseUp);
    window.removeEventListener('keydown', this.onKey);
    this.root = null;
    this.box = null;
  }
}
