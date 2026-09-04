import React from "react";

export interface BranchButtonProps {
  /** The id of the message to fork from. */
  messageId: string;
  /** The active session id. */
  sessionId: string;
  /** Optional callback after a successful fork. */
  onForked?: (newBranchRootId: string) => void;
}

/**
 * <BranchButton /> (v1.5.0, ADR-0019) — a "Fork" button that
 * creates a new branch by POSTing to /api/sessions/{sid}/branches.
 *
 * The button is rendered as part of the ChatPanel message list.
 * Clicking it dispatches a `branch.create` request with
 * `parent_message_id = messageId`; on success, the new branch
 * becomes the active tip and `onForked` is called.
 *
 * The button does not check for an in-flight stream; the server
 * refuses `branch_switch` while a stream is active
 * (`/api/sessions/{sid}/active-tip` returns 409). The
 * frontend-level lockout of the Fork button while a stream is in
 * flight is a v1.5.1 nicety (see CHANGELOG §1.5.0 "Deferred to
 * v1.5.1").
 */
export function BranchButton({ messageId, sessionId, onForked }: BranchButtonProps) {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState<string | null>(null);

  const onClick = React.useCallback(async () => {
    setBusy(true);
    setErr(null);
    try {
      const token = (document.querySelector('meta[name="dhc-token"]') as HTMLMetaElement | null)?.content ?? "";
      const res = await fetch(`/api/sessions/${encodeURIComponent(sessionId)}/branches`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(token ? { "X-DHC-Token": token } : {}),
        },
        body: JSON.stringify({ parent_message_id: messageId, role: "user", content: "" }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setErr(body?.error ?? `HTTP ${res.status}`);
        return;
      }
      const data = await res.json();
      onForked?.(data.branch_root);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }, [messageId, sessionId, onForked]);

  return (
    <button
      type="button"
      aria-label="Fork from this message"
      data-testid={`branch-button-${messageId}`}
      onClick={onClick}
      disabled={busy}
    >
      {busy ? "Forking…" : "Fork"}
      {err ? <span role="alert" data-testid={`branch-error-${messageId}`}>{err}</span> : null}
    </button>
  );
}
