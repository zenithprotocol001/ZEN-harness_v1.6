import React from "react";

export interface PathReconstructionProps {
  /** The active session id. */
  sessionId: string;
  /** Optional tip id to reconstruct from. Defaults to the active tip. */
  leafId?: string;
}

/**
 * <PathReconstruction /> (v1.5.0, ADR-0019) — renders the
 * reconstructed path from the root to a tip.
 *
 * Fetches `GET /api/sessions/{sid}/branches`, finds the entry
 * whose id matches `leafId` (or the active one), and renders the
 * path as a list of `{role, preview}` rows.
 */
export function PathReconstruction({ sessionId, leafId }: PathReconstructionProps) {
  const [path, setPath] = React.useState<Array<{ id: string; role: string; preview: string }> | null>(null);
  const [err, setErr] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const token = (document.querySelector('meta[name="dhc-token"]') as HTMLMetaElement | null)?.content ?? "";
        const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/branches`, {
          headers: token ? { "X-DHC-Token": token } : {},
        });
        if (!res.ok) {
          setErr(`HTTP ${res.status}`);
          return;
        }
        const data = await res.json();
        const branches = data.branches as Array<{
          id: string;
          active: boolean;
          path: Array<{ id: string; role: string; preview: string }>;
        }>;
        const target = leafId
          ? branches.find((b) => b.id === leafId)
          : branches.find((b) => b.active) ?? branches[0];
        if (cancelled) return;
        setPath(target?.path ?? []);
      } catch (e) {
        if (!cancelled) setErr(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [sessionId, leafId]);

  if (err) return <div role="alert">{err}</div>;
  if (path === null) return <div>Loading…</div>;
  return (
    <ol data-testid="path-reconstruction">
      {path.map((m) => (
        <li key={m.id} data-testid={`path-row-${m.id}`}>
          <strong>{m.role}:</strong> {m.preview}
        </li>
      ))}
    </ol>
  );
}
