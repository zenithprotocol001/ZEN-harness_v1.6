import React from "react";

export interface LeafSwitcherProps {
  /** The active session id. */
  sessionId: string;
  /** Branches returned by `GET /api/sessions/{sid}/branches`. */
  branches: Array<{ id: string; active: boolean }>;
  /** Optional callback after a successful switch. */
  onSwitched?: (newActiveId: string) => void;
}

/**
 * <LeafSwitcher /> (v1.5.0, ADR-0019) — a small dropdown that
 * lists all tips (one per branch) and PATCHes
 * `/api/sessions/{sid}/active-tip` on change.
 *
 * The dropdown shows each tip's id (the `m_<12 hex>` of the tip
 * message). The currently active tip is marked.
 */
export function LeafSwitcher({ sessionId, branches, onSwitched }: LeafSwitcherProps) {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);
  const active = branches.find((b) => b.active)?.id ?? "";

  const onChange = React.useCallback(
    async (e: React.ChangeEvent<HTMLSelectElement>) => {
      const newId = e.target.value;
      if (!newId || newId === active) return;
      setBusy(true);
      setErr(null);
      try {
        const token = (document.querySelector('meta[name="dhc-token"]') as HTMLMetaElement | null)?.content ?? "";
        const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/active-tip`, {
          method: "PATCH",
          headers: {
            "Content-Type": "application/json",
            ...(token ? { "X-DHC-Token": token } : {}),
          },
          body: JSON.stringify({ leaf_message_id: newId }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          setErr(body?.error ?? `HTTP ${res.status}`);
          return;
        }
        onSwitched?.(newId);
      } catch (e) {
        setErr(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(false);
      }
    },
    [sessionId, active, onSwitched]
  );

  if (branches.length === 0) {
    return <span data-testid="leaf-switcher-empty">No branches</span>;
  }
  return (
    <div>
      <label htmlFor="leaf-switcher-select">Active tip: </label>
      <select
        id="leaf-switcher-select"
        data-testid="leaf-switcher-select"
        value={active}
        onChange={onChange}
        disabled={busy}
      >
        {branches.map((b) => (
          <option key={b.id} value={b.id}>
            {b.id}{b.active ? " (active)" : ""}
          </option>
        ))}
      </select>
      {err ? <span role="alert" data-testid="leaf-switcher-error">{err}</span> : null}
    </div>
  );
}
