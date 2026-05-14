import { useEffect, useState } from 'react';
import { getSettings, updateSetting, type AppSetting } from '../api/client';

export function Settings() {
  const [settings, setSettings] = useState<AppSetting[] | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  useEffect(() => {
    getSettings()
      .then(setSettings)
      .catch(() => setSettings([]));
  }, []);

  function handleChange(key: string, value: string) {
    setSettings((prev) =>
      prev ? prev.map((s) => (s.key === key ? { ...s, value } : s)) : prev,
    );
  }

  async function handleSave(key: string) {
    const target = settings?.find((s) => s.key === key);
    if (!target) return;
    await updateSetting(key, target.value);
    setSaved(key);
    setTimeout(() => setSaved(null), 2000);
  }

  if (settings === null) {
    return <div>加载中…</div>;
  }

  return (
    <section aria-labelledby="settings-heading">
      <h1 id="settings-heading" style={{ marginBottom: 'var(--space-4)' }}>
        设置
      </h1>
      <div
        style={{
          background: 'var(--color-surface)',
          border: '1px solid var(--color-border)',
          borderRadius: 'var(--radius)',
          padding: 'var(--space-4)',
          maxWidth: 480,
        }}
      >
        {settings.map((setting) => (
          <div
            key={setting.key}
            style={{
              display: 'flex',
              gap: 'var(--space-2)',
              alignItems: 'center',
              marginBottom: 'var(--space-3)',
            }}
          >
            <label style={{ width: 140, color: 'var(--color-text-muted)' }}>
              {setting.key}
            </label>
            <input
              value={setting.value}
              onChange={(e) => handleChange(setting.key, e.target.value)}
              style={{
                flex: 1,
                padding: 'var(--space-2)',
                border: '1px solid var(--color-border)',
                borderRadius: 'var(--radius)',
              }}
            />
            <button
              type="button"
              onClick={() => handleSave(setting.key)}
              style={{
                padding: 'var(--space-2) var(--space-3)',
                background: 'var(--color-accent)',
                color: 'var(--color-accent-text)',
                border: 'none',
                borderRadius: 'var(--radius)',
                cursor: 'pointer',
              }}
            >
              保存
            </button>
            {saved === setting.key && (
              <span style={{ color: 'var(--status-paid)' }}>已保存</span>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
