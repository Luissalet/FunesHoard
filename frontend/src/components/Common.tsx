import { Inbox } from "lucide-react";
import { type ReactNode, useState } from "react";
import { categoryColor } from "../categoryColors";

export function StatCard({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="card">
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </div>
  );
}

export function BarList({
  entries,
  formatValue,
  colorFor,
}: {
  entries: [string, number][];
  formatValue: (v: number) => string;
  colorFor?: (key: string) => string;
}) {
  const max = Math.max(1, ...entries.map(([, v]) => v));
  if (entries.length === 0) return <p style={{ color: "var(--text-muted)", fontSize: 13 }}>—</p>;
  return (
    <div>
      {entries.map(([key, value]) => (
        <div className="bar-row" key={key}>
          <div className="label" title={key}>
            {key}
          </div>
          <div className="bar-track">
            <div
              className="bar-fill"
              style={{
                width: `${(value / max) * 100}%`,
                background: colorFor ? colorFor(key) : "var(--accent)",
              }}
            />
          </div>
          <div className="value">{formatValue(value)}</div>
        </div>
      ))}
    </div>
  );
}

export function CategoryBarList({ entries, formatValue }: { entries: [string, number][]; formatValue: (v: number) => string }) {
  return <BarList entries={entries} formatValue={formatValue} colorFor={categoryColor} />;
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="empty-state">
      <Inbox size={32} strokeWidth={1.5} />
      <h3>{title}</h3>
      <p style={{ maxWidth: 340, margin: 0 }}>{body}</p>
    </div>
  );
}

export function InlineConfirm({
  label,
  confirmLabel,
  cancelLabel,
  onConfirm,
  danger = true,
  disabled = false,
}: {
  label: string;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  danger?: boolean;
  disabled?: boolean;
}) {
  const [confirming, setConfirming] = useState(false);
  if (!confirming) {
    return (
      <button className={`btn ${danger ? "btn-danger" : "btn-primary"}`} disabled={disabled} onClick={() => setConfirming(true)}>
        {label}
      </button>
    );
  }
  return (
    <div className="confirm-inline">
      <span>{label}?</span>
      <button
        className="btn btn-danger"
        onClick={() => {
          setConfirming(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </button>
      <button className="btn btn-ghost" onClick={() => setConfirming(false)}>
        {cancelLabel}
      </button>
    </div>
  );
}

export function Badge({ text, color }: { text: string; color: string }) {
  return (
    <span className="badge" style={{ background: color }}>
      {text}
    </span>
  );
}
