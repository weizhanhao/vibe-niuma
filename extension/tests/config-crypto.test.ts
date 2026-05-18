// Plan 11 · M4.T25：config 加密/解密 utility 单测。
//
// 业务员用 passphrase 加密整个 config JSON 给程序员，
// 程序员同样用 passphrase 解密导回 chrome.storage。
//
// PBKDF2-HMAC-SHA256（200k 次迭代）+ AES-256-GCM。
import { describe, expect, it } from 'vitest';
import {
  CRYPTO_FORMAT_VERSION,
  decryptConfig,
  encryptConfig,
} from '../src/lib/config-crypto';

describe('config-crypto', () => {
  it('round-trip：加密 → 解密 → 原 JSON', async () => {
    const payload = { orchestratorUrl: 'http://x:9000', adminToken: 't'.repeat(20) };
    const enc = await encryptConfig(payload, 'my-passphrase');

    const parsed = JSON.parse(enc);
    expect(parsed.v).toBe(CRYPTO_FORMAT_VERSION);
    expect(typeof parsed.salt).toBe('string');
    expect(typeof parsed.iv).toBe('string');
    expect(typeof parsed.ciphertext).toBe('string');
    expect(parsed.salt.length).toBeGreaterThan(0);

    const decoded = await decryptConfig(enc, 'my-passphrase');
    expect(decoded).toEqual(payload);
  });

  it('错 passphrase → 抛错（不偷偷返垃圾）', async () => {
    const enc = await encryptConfig({ foo: 'bar' }, 'correct');
    await expect(decryptConfig(enc, 'wrong')).rejects.toThrow();
  });

  it('损坏 ciphertext → 抛错', async () => {
    const enc = await encryptConfig({ foo: 'bar' }, 'p');
    const parsed = JSON.parse(enc);
    parsed.ciphertext = parsed.ciphertext.slice(0, -4) + 'XXXX';
    await expect(decryptConfig(JSON.stringify(parsed), 'p')).rejects.toThrow();
  });

  it('format 版本不匹配 → 抛带版本号的错', async () => {
    const enc = await encryptConfig({ foo: 'bar' }, 'p');
    const parsed = JSON.parse(enc);
    parsed.v = 999;
    await expect(
      decryptConfig(JSON.stringify(parsed), 'p'),
    ).rejects.toThrow(/格式版本/);
  });

  it('每次加密 salt + iv 不同（不重用 nonce）', async () => {
    const payload = { x: 1 };
    const a = JSON.parse(await encryptConfig(payload, 'p'));
    const b = JSON.parse(await encryptConfig(payload, 'p'));
    expect(a.salt).not.toBe(b.salt);
    expect(a.iv).not.toBe(b.iv);
    expect(a.ciphertext).not.toBe(b.ciphertext);
  });

  it('passphrase 空 → 抛错', async () => {
    await expect(encryptConfig({ x: 1 }, '')).rejects.toThrow(/passphrase/);
  });

  it('Unicode payload round-trip 正确', async () => {
    const payload = { 业务员: '不要再用 doskill', emoji: '🐂' };
    const enc = await encryptConfig(payload, 'pp');
    const dec = await decryptConfig(enc, 'pp');
    expect(dec).toEqual(payload);
  });
});
