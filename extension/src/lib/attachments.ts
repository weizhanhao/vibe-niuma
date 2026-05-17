// Plan 10 Task 11: 业务员一次输入的附件构造工具。
//
// 业务员视角（用户原话）：「不是每次输入都要截图...用户可以多输入几张图」。
// 一次 message 最多 3 张附件（vision API token 安全区，与后端
// MAX_ATTACHMENTS_PER_MESSAGE 一致）。
import type { Attachment, BoxCoords, PendingCapture, Viewport } from './types';

/** 业务上限 = 3，跟后端 schemas.MAX_ATTACHMENTS_PER_MESSAGE 对齐。 */
export const MAX_ATTACHMENTS = 3;

interface FramedRegionInput {
  b64: string;
  mime: string;
  url: string;
  box: BoxCoords;
  viewport: Viewport;
}

/**
 * 业务员在 demo 页面拖框出的精确区域 → framed_region attachment。
 * 这是「最强信号」附件：vision 能拿到 box 看到业务员真正想改的位置。
 */
export function collectFramedRegion(input: FramedRegionInput): Attachment {
  return {
    kind: 'framed_region',
    mime: input.mime,
    b64: input.b64,
    url: input.url,
    box: input.box,
    viewport: input.viewport,
  };
}

interface PasteImageInput {
  b64: string;
  mime: string;
  name?: string;
}

/**
 * 业务员从剪贴板或文件选择器贴的图 → pasted_image。
 * 没有 box / url（业务员不在 demo 页面）；name 可选。
 */
export function pasteImage(input: PasteImageInput): Attachment {
  return {
    kind: 'pasted_image',
    mime: input.mime,
    b64: input.b64,
    ...(input.name ? { name: input.name } : {}),
  };
}

interface AttachFileInput {
  b64: string;
  mime: string;
  name: string;
}

/**
 * 通用文件附件入口：
 * - image/* → pasted_image（vision 可以看）
 * - 其他 → attached_file（vision 看不见，作上下文标记）
 */
export function attachFile(input: AttachFileInput): Attachment {
  const isImage = input.mime.startsWith('image/');
  return {
    kind: isImage ? 'pasted_image' : 'attached_file',
    mime: input.mime,
    b64: input.b64,
    name: input.name,
  };
}

/**
 * v0.5 兼容：把老的 PendingCapture（单张截图）转成 Attachment。
 * SW 旧的 SUBMIT_TEXT_ONLY / CONFIRM_CAPTURE 路径还会产生 PendingCapture，
 * 在改造完前用这个适配；Task 13 SW 改 /messages 后会换到 Attachment 直接构造。
 */
export function legacyPendingCaptureToAttachment(cap: PendingCapture): Attachment {
  return collectFramedRegion({
    b64: cap.screenshotB64,
    mime: cap.screenshotMime,
    url: cap.url,
    box: cap.boxCoords,
    viewport: cap.viewport,
  });
}
