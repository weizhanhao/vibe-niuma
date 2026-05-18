import { describe, expect, it } from 'vitest';
import { CaptureOverlay } from '../src/content/capture-overlay';

function fire(target: EventTarget, type: string, init: Partial<MouseEvent> = {}) {
  target.dispatchEvent(new MouseEvent(type, { bubbles: true, cancelable: true, ...init }));
}

describe('CaptureOverlay', () => {
  it('drag produces normalized box coords + viewport', async () => {
    Object.defineProperty(window, 'innerWidth', { value: 1280, configurable: true });
    Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true });
    const o = new CaptureOverlay();
    const promise = o.start();
    const root = document.querySelector('[data-vibe-niuma-overlay]') as HTMLElement;
    expect(root).toBeTruthy();

    fire(root, 'mousedown', { clientX: 100, clientY: 80 });
    fire(window, 'mousemove', { clientX: 200, clientY: 200 });
    fire(window, 'mouseup', { clientX: 200, clientY: 200 });

    const result = await promise;
    expect(result).not.toBeNull();
    expect(result!.viewport).toEqual({ width: 1280, height: 800 });
  });

  it('escape cancels capture and resolves null', async () => {
    const o = new CaptureOverlay();
    const promise = o.start();
    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    expect(await promise).toBeNull();
    expect(document.querySelector('[data-vibe-niuma-overlay]')).toBeNull();
  });
});
