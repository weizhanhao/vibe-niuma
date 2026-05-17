// AnnotateOverlay —— 全屏标注 overlay：业务员看冻结的截图、用 4 种工具标注、
// 「完成」时把所有标注烘焙进一张 PNG 返回。
//
// 工具：红框 / 箭头 / 文字 / 马赛克
// 操作流：start(dataUrl) → resolve(annotatedPngDataUrl | null)
//
// 设计要点：
// - 单 canvas 渲染所有图层 = 截图 + annotations[]，每次操作 redraw
// - 标注存 plain object 数组 → 撤销 = pop + redraw
// - canvas pixel 尺寸 = 截图原始尺寸（高清屏 DPR 信息保留）；CSS 尺寸缩放
//   到窗口大小（不失真显示）。鼠标坐标 → canvas 坐标做一次缩放映射
// - 文字标注：用 contentEditable 浮层定位输入，按 Enter / 失焦提交
// - 马赛克：简单像素化（取 12×12 块平均色填回）
//
// 不依赖任何 React / 框架 —— content script 直接 new 这个 class。

type ToolKind = 'rect' | 'arrow' | 'text' | 'mosaic';

interface AnnotationBase { type: ToolKind; }
interface RectAnn extends AnnotationBase {
  type: 'rect'; x1: number; y1: number; x2: number; y2: number;
}
interface ArrowAnn extends AnnotationBase {
  type: 'arrow'; x1: number; y1: number; x2: number; y2: number;
}
interface TextAnn extends AnnotationBase {
  type: 'text'; x: number; y: number; text: string;
}
interface MosaicAnn extends AnnotationBase {
  type: 'mosaic'; x1: number; y1: number; x2: number; y2: number;
}
type Annotation = RectAnn | ArrowAnn | TextAnn | MosaicAnn;

const COLOR = '#ff3b30';       // iOS 红，截图标注约定俗成的高对比度色
const STROKE_WIDTH = 3;
const TEXT_FONT = 'bold 18px -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif';
const MOSAIC_BLOCK = 12;       // 马赛克块大小（像素）

export class AnnotateOverlay {
  private container!: HTMLDivElement;
  private canvas!: HTMLCanvasElement;
  private ctx!: CanvasRenderingContext2D;
  private img!: HTMLImageElement;
  private toolbar!: HTMLDivElement;
  private tool: ToolKind = 'rect';
  private annotations: Annotation[] = [];
  private dragStart: { x: number; y: number } | null = null;
  private dragCurrent: { x: number; y: number } | null = null;
  private resolve: ((dataUrl: string | null) => void) | null = null;
  private keydownHandler!: (e: KeyboardEvent) => void;

  async start(screenshotDataUrl: string): Promise<string | null> {
    this.img = await loadImage(screenshotDataUrl);
    this.buildDom();
    document.documentElement.appendChild(this.container);
    return new Promise<string | null>((resolve) => {
      this.resolve = resolve;
    });
  }

  dispose(): void {
    this.container?.remove();
    document.removeEventListener('keydown', this.keydownHandler);
  }

  // ── DOM 构建 ─────────────────────────────────────────────────────
  private buildDom(): void {
    this.container = document.createElement('div');
    Object.assign(this.container.style, {
      position: 'fixed', inset: '0', zIndex: '2147483647',
      background: 'rgba(0,0,0,0.55)', userSelect: 'none',
      display: 'flex', flexDirection: 'column', alignItems: 'center',
      justifyContent: 'center', overflow: 'hidden',
      cursor: 'crosshair', fontFamily: 'system-ui, sans-serif',
    } as Partial<CSSStyleDeclaration>);

    // canvas：pixel 尺寸 = 截图原始尺寸，CSS 缩放到 fit window
    this.canvas = document.createElement('canvas');
    this.canvas.width = this.img.naturalWidth;
    this.canvas.height = this.img.naturalHeight;
    const ctx = this.canvas.getContext('2d');
    if (!ctx) throw new Error('canvas 2d ctx 拿不到');
    this.ctx = ctx;
    // CSS 缩放：保持比例不变形，最多占窗口高度 80% / 宽度 90%
    const maxW = window.innerWidth * 0.9;
    const maxH = window.innerHeight * 0.8;
    const scale = Math.min(maxW / this.img.naturalWidth, maxH / this.img.naturalHeight, 1);
    Object.assign(this.canvas.style, {
      width: `${this.img.naturalWidth * scale}px`,
      height: `${this.img.naturalHeight * scale}px`,
      boxShadow: '0 8px 40px rgba(0,0,0,0.4)',
      background: '#fff',
    } as Partial<CSSStyleDeclaration>);
    this.canvas.addEventListener('mousedown', this.onMouseDown);
    this.canvas.addEventListener('mousemove', this.onMouseMove);
    this.canvas.addEventListener('mouseup', this.onMouseUp);
    this.canvas.addEventListener('mouseleave', this.onMouseUp);

    // toolbar：顶部居中
    this.toolbar = document.createElement('div');
    Object.assign(this.toolbar.style, {
      position: 'absolute', top: '20px', left: '50%',
      transform: 'translateX(-50%)',
      display: 'flex', gap: '6px', alignItems: 'center',
      padding: '8px 12px', background: 'rgba(28,28,30,0.92)',
      borderRadius: '12px', boxShadow: '0 4px 16px rgba(0,0,0,0.4)',
      color: 'white', fontSize: '13px',
    } as Partial<CSSStyleDeclaration>);
    this.toolbar.appendChild(this.makeToolBtn('rect', '🟥', '红框'));
    this.toolbar.appendChild(this.makeToolBtn('arrow', '↗', '箭头'));
    this.toolbar.appendChild(this.makeToolBtn('text', 'T', '文字'));
    this.toolbar.appendChild(this.makeToolBtn('mosaic', '▦', '马赛克'));
    this.toolbar.appendChild(this.makeSeparator());
    this.toolbar.appendChild(this.makeActionBtn('↶ 撤销', () => this.undo()));
    this.toolbar.appendChild(this.makeSeparator());
    this.toolbar.appendChild(this.makeActionBtn('取消', () => this.cancel(), 'ghost'));
    this.toolbar.appendChild(this.makeActionBtn('✓ 完成', () => this.done(), 'primary'));

    // 底部提示
    const hint = document.createElement('div');
    Object.assign(hint.style, {
      position: 'absolute', bottom: '20px', left: '50%',
      transform: 'translateX(-50%)',
      padding: '6px 12px', background: 'rgba(28,28,30,0.7)',
      borderRadius: '8px', color: '#aaa', fontSize: '12px',
    } as Partial<CSSStyleDeclaration>);
    hint.textContent = 'Esc 取消 · ⌘Z 撤销 · 完成后图片自动加到输入栏';
    this.container.appendChild(this.canvas);
    this.container.appendChild(this.toolbar);
    this.container.appendChild(hint);

    // 第一次渲染 + 高亮默认工具
    this.redraw();
    this.refreshToolbarSelection();

    // 键盘快捷键
    this.keydownHandler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); this.cancel(); }
      if ((e.metaKey || e.ctrlKey) && (e.key === 'z' || e.key === 'Z')) {
        e.preventDefault(); this.undo();
      }
    };
    document.addEventListener('keydown', this.keydownHandler);
  }

  private makeToolBtn(kind: ToolKind, icon: string, label: string): HTMLButtonElement {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.title = label;
    btn.innerHTML = `<span style="font-size:14px">${icon}</span> <span>${label}</span>`;
    Object.assign(btn.style, {
      display: 'inline-flex', alignItems: 'center', gap: '4px',
      padding: '6px 10px', border: 'none', borderRadius: '8px',
      background: 'transparent', color: 'white', cursor: 'pointer',
      fontSize: '12px', fontFamily: 'inherit',
    } as Partial<CSSStyleDeclaration>);
    btn.addEventListener('click', () => {
      this.tool = kind;
      this.refreshToolbarSelection();
    });
    btn.dataset.tool = kind;
    return btn;
  }

  private makeActionBtn(
    label: string, onClick: () => void,
    variant: 'default' | 'primary' | 'ghost' = 'default',
  ): HTMLButtonElement {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.textContent = label;
    const bg = variant === 'primary' ? '#0a84ff'
             : variant === 'ghost' ? 'transparent'
             : 'rgba(255,255,255,0.1)';
    Object.assign(btn.style, {
      padding: '6px 12px', border: 'none', borderRadius: '8px',
      background: bg, color: 'white', cursor: 'pointer',
      fontSize: '12px', fontFamily: 'inherit', fontWeight: '500',
    } as Partial<CSSStyleDeclaration>);
    btn.addEventListener('click', onClick);
    return btn;
  }

  private makeSeparator(): HTMLDivElement {
    const sep = document.createElement('div');
    Object.assign(sep.style, {
      width: '1px', height: '18px', background: 'rgba(255,255,255,0.2)',
      margin: '0 4px',
    } as Partial<CSSStyleDeclaration>);
    return sep;
  }

  private refreshToolbarSelection(): void {
    const btns = this.toolbar.querySelectorAll<HTMLButtonElement>('[data-tool]');
    btns.forEach((b) => {
      const selected = b.dataset.tool === this.tool;
      b.style.background = selected ? 'rgba(10,132,255,0.5)' : 'transparent';
    });
  }

  // ── 鼠标事件 → canvas 坐标（考虑 CSS 缩放）─────────────────────
  private toCanvas(e: MouseEvent): { x: number; y: number } {
    const rect = this.canvas.getBoundingClientRect();
    const scaleX = this.canvas.width / rect.width;
    const scaleY = this.canvas.height / rect.height;
    return {
      x: (e.clientX - rect.left) * scaleX,
      y: (e.clientY - rect.top) * scaleY,
    };
  }

  private onMouseDown = (e: MouseEvent): void => {
    if (e.button !== 0) return;
    const p = this.toCanvas(e);
    if (this.tool === 'text') {
      // 文字标注：弹一个浮层输入框，定位在点击点
      this.spawnTextInput(p.x, p.y, e.clientX, e.clientY);
      return;
    }
    this.dragStart = p;
    this.dragCurrent = p;
  };

  private onMouseMove = (e: MouseEvent): void => {
    if (!this.dragStart) return;
    this.dragCurrent = this.toCanvas(e);
    this.redrawWithPreview();
  };

  private onMouseUp = (e: MouseEvent): void => {
    if (!this.dragStart || !this.dragCurrent) {
      this.dragStart = null;
      this.dragCurrent = null;
      return;
    }
    const start = this.dragStart;
    const end = this.toCanvas(e);
    this.dragStart = null;
    this.dragCurrent = null;

    // 太小的拖动忽略（避免误点产生空 annotation）
    if (Math.abs(end.x - start.x) < 5 && Math.abs(end.y - start.y) < 5) {
      this.redraw();
      return;
    }
    if (this.tool === 'rect') {
      this.annotations.push({ type: 'rect', x1: start.x, y1: start.y, x2: end.x, y2: end.y });
    } else if (this.tool === 'arrow') {
      this.annotations.push({ type: 'arrow', x1: start.x, y1: start.y, x2: end.x, y2: end.y });
    } else if (this.tool === 'mosaic') {
      this.annotations.push({ type: 'mosaic', x1: start.x, y1: start.y, x2: end.x, y2: end.y });
    }
    this.redraw();
  };

  // ── 文字标注：浮层 input ──────────────────────────────────────
  private spawnTextInput(canvasX: number, canvasY: number, clientX: number, clientY: number): void {
    const input = document.createElement('input');
    input.type = 'text';
    input.placeholder = '输入文字 ↩ 提交';
    Object.assign(input.style, {
      position: 'fixed', left: `${clientX}px`, top: `${clientY}px`,
      zIndex: '2147483647',
      padding: '4px 8px', font: '14px sans-serif', color: COLOR,
      border: `2px solid ${COLOR}`, borderRadius: '4px',
      background: 'rgba(255,255,255,0.95)', outline: 'none',
      minWidth: '120px',
    } as Partial<CSSStyleDeclaration>);
    this.container.appendChild(input);
    input.focus();
    const commit = () => {
      const text = input.value.trim();
      input.remove();
      if (!text) return;
      this.annotations.push({ type: 'text', x: canvasX, y: canvasY, text });
      this.redraw();
    };
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter') { e.preventDefault(); commit(); }
      else if (e.key === 'Escape') { e.preventDefault(); input.remove(); }
    });
    input.addEventListener('blur', commit);
  }

  // ── 渲染 ─────────────────────────────────────────────────────────
  private redraw(): void {
    this.ctx.drawImage(this.img, 0, 0);
    for (const ann of this.annotations) this.drawAnnotation(ann);
  }

  private redrawWithPreview(): void {
    this.redraw();
    if (this.dragStart && this.dragCurrent) {
      const s = this.dragStart;
      const c = this.dragCurrent;
      const preview: Annotation | null =
        this.tool === 'rect' ? { type: 'rect', x1: s.x, y1: s.y, x2: c.x, y2: c.y }
        : this.tool === 'arrow' ? { type: 'arrow', x1: s.x, y1: s.y, x2: c.x, y2: c.y }
        : this.tool === 'mosaic' ? { type: 'mosaic', x1: s.x, y1: s.y, x2: c.x, y2: c.y }
        : null;
      if (preview) this.drawAnnotation(preview);
    }
  }

  private drawAnnotation(ann: Annotation): void {
    const ctx = this.ctx;
    if (ann.type === 'rect') {
      ctx.strokeStyle = COLOR;
      ctx.lineWidth = STROKE_WIDTH;
      const x = Math.min(ann.x1, ann.x2);
      const y = Math.min(ann.y1, ann.y2);
      const w = Math.abs(ann.x2 - ann.x1);
      const h = Math.abs(ann.y2 - ann.y1);
      ctx.strokeRect(x, y, w, h);
    } else if (ann.type === 'arrow') {
      drawArrow(ctx, ann.x1, ann.y1, ann.x2, ann.y2);
    } else if (ann.type === 'text') {
      ctx.font = TEXT_FONT;
      ctx.textBaseline = 'top';
      // 白描边 + 红字：在任意背景上都看得清
      ctx.lineWidth = 4;
      ctx.strokeStyle = 'white';
      ctx.strokeText(ann.text, ann.x, ann.y);
      ctx.fillStyle = COLOR;
      ctx.fillText(ann.text, ann.x, ann.y);
    } else if (ann.type === 'mosaic') {
      pixelate(ctx, ann.x1, ann.y1, ann.x2, ann.y2, MOSAIC_BLOCK);
    }
  }

  // ── 动作 ─────────────────────────────────────────────────────────
  private undo(): void {
    if (this.annotations.length === 0) return;
    this.annotations.pop();
    this.redraw();
  }

  private cancel(): void {
    this.resolve?.(null);
    this.resolve = null;
    this.dispose();
  }

  private done(): void {
    // 烘焙：当前 canvas 内容直接出 PNG dataURL
    this.redraw();
    const dataUrl = this.canvas.toDataURL('image/png');
    this.resolve?.(dataUrl);
    this.resolve = null;
    this.dispose();
  }
}

// ── 工具函数 ─────────────────────────────────────────────────────

function loadImage(src: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.onload = () => resolve(img);
    img.onerror = () => reject(new Error('image load failed'));
    img.src = src;
  });
}

function drawArrow(
  ctx: CanvasRenderingContext2D,
  x1: number, y1: number, x2: number, y2: number,
): void {
  ctx.strokeStyle = COLOR;
  ctx.fillStyle = COLOR;
  ctx.lineWidth = STROKE_WIDTH;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  // 主线
  ctx.beginPath();
  ctx.moveTo(x1, y1);
  ctx.lineTo(x2, y2);
  ctx.stroke();
  // 箭头头部：在终点 (x2,y2) 画一个等腰三角形
  const angle = Math.atan2(y2 - y1, x2 - x1);
  const headLen = 14;
  const headAngle = Math.PI / 6;
  ctx.beginPath();
  ctx.moveTo(x2, y2);
  ctx.lineTo(
    x2 - headLen * Math.cos(angle - headAngle),
    y2 - headLen * Math.sin(angle - headAngle),
  );
  ctx.lineTo(
    x2 - headLen * Math.cos(angle + headAngle),
    y2 - headLen * Math.sin(angle + headAngle),
  );
  ctx.closePath();
  ctx.fill();
}

function pixelate(
  ctx: CanvasRenderingContext2D,
  x1: number, y1: number, x2: number, y2: number,
  block: number,
): void {
  const x = Math.max(0, Math.floor(Math.min(x1, x2)));
  const y = Math.max(0, Math.floor(Math.min(y1, y2)));
  const w = Math.floor(Math.abs(x2 - x1));
  const h = Math.floor(Math.abs(y2 - y1));
  if (w <= 0 || h <= 0) return;
  // 取原区域像素
  const src = ctx.getImageData(x, y, w, h);
  // 每个 block × block 块取平均色填回
  for (let by = 0; by < h; by += block) {
    for (let bx = 0; bx < w; bx += block) {
      let r = 0, g = 0, b = 0, a = 0, count = 0;
      const bw = Math.min(block, w - bx);
      const bh = Math.min(block, h - by);
      for (let dy = 0; dy < bh; dy++) {
        for (let dx = 0; dx < bw; dx++) {
          const idx = ((by + dy) * w + (bx + dx)) * 4;
          r += src.data[idx];
          g += src.data[idx + 1];
          b += src.data[idx + 2];
          a += src.data[idx + 3];
          count++;
        }
      }
      r = Math.round(r / count);
      g = Math.round(g / count);
      b = Math.round(b / count);
      a = Math.round(a / count);
      ctx.fillStyle = `rgba(${r},${g},${b},${a / 255})`;
      ctx.fillRect(x + bx, y + by, bw, bh);
    }
  }
}
