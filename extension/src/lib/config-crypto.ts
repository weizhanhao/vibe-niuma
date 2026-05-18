// Plan 11 · M4.T25：config 加密 / 解密 utility（PBKDF2 + AES-GCM via Web Crypto API）。
//
// 用法：
//   const exported = await encryptConfig(myConfig, passphrase);
//   // 业务员把 exported 文本（JSON）发给程序员，程序员粘到导入框 + 同样 passphrase：
//   const restored = await decryptConfig(exported, passphrase);
//
// 安全：
// - PBKDF2-HMAC-SHA256 200_000 次（OWASP 2023 推荐 ≥ 600k，但 jsdom 慢；
//   200k 够防钓鱼场景，业务员私分享场景的成本/收益均衡）
// - AES-256-GCM（128-bit IV）—— AEAD，自带 integrity；改 ciphertext 解密会抛
// - salt 每次新（16 bytes），iv 每次新（12 bytes），不会重用 nonce
// - 输出 JSON 格式带 v 字段，未来升迭代次数/算法时可优雅迁移

export const CRYPTO_FORMAT_VERSION = 1;

const PBKDF2_ITERATIONS = 200_000;
const SALT_BYTES = 16;
const IV_BYTES = 12;
const KEY_BITS = 256;

export interface EncryptedBlob {
  v: number;
  salt: string;       // base64
  iv: string;         // base64
  ciphertext: string; // base64
}

function b64encode(buf: ArrayBuffer | Uint8Array): string {
  const bytes = buf instanceof Uint8Array ? buf : new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < bytes.length; i++) s += String.fromCharCode(bytes[i]);
  return btoa(s);
}

function b64decode(s: string): Uint8Array {
  const bin = atob(s);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

async function deriveKey(passphrase: string, salt: Uint8Array): Promise<CryptoKey> {
  const baseKey = await crypto.subtle.importKey(
    'raw',
    new TextEncoder().encode(passphrase),
    'PBKDF2',
    false,
    ['deriveKey'],
  );
  return crypto.subtle.deriveKey(
    {
      name: 'PBKDF2',
      salt,
      iterations: PBKDF2_ITERATIONS,
      hash: 'SHA-256',
    },
    baseKey,
    { name: 'AES-GCM', length: KEY_BITS },
    false,
    ['encrypt', 'decrypt'],
  );
}

export async function encryptConfig(
  payload: unknown,
  passphrase: string,
): Promise<string> {
  if (!passphrase) throw new Error('passphrase 不能为空');
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const iv = crypto.getRandomValues(new Uint8Array(IV_BYTES));
  const key = await deriveKey(passphrase, salt);
  const plaintext = new TextEncoder().encode(JSON.stringify(payload));
  const ciphertext = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    plaintext,
  );
  const blob: EncryptedBlob = {
    v: CRYPTO_FORMAT_VERSION,
    salt: b64encode(salt),
    iv: b64encode(iv),
    ciphertext: b64encode(ciphertext),
  };
  return JSON.stringify(blob);
}

export async function decryptConfig<T = unknown>(
  encryptedJson: string,
  passphrase: string,
): Promise<T> {
  if (!passphrase) throw new Error('passphrase 不能为空');
  let blob: EncryptedBlob;
  try {
    blob = JSON.parse(encryptedJson) as EncryptedBlob;
  } catch (err) {
    throw new Error(
      `解密失败：导入文本不是合法 JSON（${err instanceof Error ? err.message : err}）`,
    );
  }
  if (blob.v !== CRYPTO_FORMAT_VERSION) {
    throw new Error(
      `加密格式版本不匹配：导入文件 v=${blob.v}，当前扩展只认 v=${CRYPTO_FORMAT_VERSION}`,
    );
  }
  if (!blob.salt || !blob.iv || !blob.ciphertext) {
    throw new Error('解密失败：缺少 salt / iv / ciphertext 字段');
  }

  const salt = b64decode(blob.salt);
  const iv = b64decode(blob.iv);
  const ciphertext = b64decode(blob.ciphertext);
  const key = await deriveKey(passphrase, salt);
  try {
    const plaintext = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv },
      key,
      ciphertext,
    );
    return JSON.parse(new TextDecoder().decode(plaintext)) as T;
  } catch {
    // AES-GCM 验证失败 → 大概率 passphrase 错或密文被改
    throw new Error('解密失败：passphrase 错误或密文被篡改');
  }
}
