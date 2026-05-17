// Plan 10 Task 11: attachments.ts —— 业务员一次输入的 0-3 个附件构造工具。
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MAX_ATTACHMENTS,
  attachFile,
  collectFramedRegion,
  legacyPendingCaptureToAttachment,
  pasteImage,
} from '../src/lib/attachments';
import type { PendingCapture } from '../src/lib/types';

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date('2026-05-17T10:00:00Z'));
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe('attachments builders', () => {
  it('collectFramedRegion builds framed_region attachment with box + url', () => {
    const att = collectFramedRegion({
      b64: 'AAA',
      mime: 'image/jpeg',
      url: 'http://x/orders',
      box: { x: 1, y: 2, width: 3, height: 4 },
      viewport: { width: 1280, height: 720 },
    });
    expect(att.kind).toBe('framed_region');
    expect(att.mime).toBe('image/jpeg');
    expect(att.b64).toBe('AAA');
    expect(att.url).toBe('http://x/orders');
    expect(att.box).toEqual({ x: 1, y: 2, width: 3, height: 4 });
    expect(att.viewport).toEqual({ width: 1280, height: 720 });
  });

  it('pasteImage builds pasted_image with file name', () => {
    const att = pasteImage({ b64: 'PPP', mime: 'image/png', name: 'logo.png' });
    expect(att.kind).toBe('pasted_image');
    expect(att.b64).toBe('PPP');
    expect(att.mime).toBe('image/png');
    expect(att.name).toBe('logo.png');
    expect(att.box).toBeUndefined();
    expect(att.url).toBeUndefined();
  });

  it('attachFile defaults to attached_file kind for non-image MIME', () => {
    const att = attachFile({ b64: 'PDF', mime: 'application/pdf', name: 'spec.pdf' });
    expect(att.kind).toBe('attached_file');
    expect(att.name).toBe('spec.pdf');
  });

  it('attachFile for image/* falls back to pasted_image kind', () => {
    const att = attachFile({ b64: 'IMG', mime: 'image/png', name: 'shot.png' });
    expect(att.kind).toBe('pasted_image');
  });

  it('MAX_ATTACHMENTS is 3', () => {
    expect(MAX_ATTACHMENTS).toBe(3);
  });
});

describe('legacy compatibility', () => {
  it('legacyPendingCaptureToAttachment maps v0.5 PendingCapture → Attachment', () => {
    const cap: PendingCapture = {
      screenshotB64: 'OLD',
      screenshotMime: 'image/jpeg',
      url: 'http://demo/orders',
      boxCoords: { x: 0, y: 0, width: 100, height: 50 },
      viewport: { width: 1280, height: 720 },
      requestText: '(text not used here)',
    };
    const att = legacyPendingCaptureToAttachment(cap);
    expect(att.kind).toBe('framed_region');
    expect(att.b64).toBe('OLD');
    expect(att.mime).toBe('image/jpeg');
    expect(att.url).toBe('http://demo/orders');
    expect(att.box).toEqual({ x: 0, y: 0, width: 100, height: 50 });
  });
});
