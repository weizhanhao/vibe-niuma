interface StatCardProps {
  label: string;
  value: string;
  hint?: string;
}

export function StatCard({ label, value, hint }: StatCardProps) {
  return (
    <div
      style={{
        background: 'var(--color-surface)',
        border: '1px solid var(--color-border)',
        borderRadius: 'var(--radius)',
        padding: 'var(--space-4)',
        minWidth: 180,
      }}
    >
      <div style={{ color: 'var(--color-text-muted)', fontSize: 'var(--text-sm)' }}>
        {label}
      </div>
      <div
        style={{
          fontSize: 'var(--text-xl)',
          fontWeight: 600,
          marginTop: 'var(--space-2)',
        }}
      >
        {value}
      </div>
      {hint && (
        <div
          style={{
            color: 'var(--color-text-muted)',
            fontSize: 'var(--text-sm)',
            marginTop: 'var(--space-1)',
          }}
        >
          {hint}
        </div>
      )}
    </div>
  );
}
