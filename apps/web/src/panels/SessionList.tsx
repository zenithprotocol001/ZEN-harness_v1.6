import { SessionSummary } from "../types/chat";
import { Icon } from "../components/icons";

/**
 * <SessionList /> (v1.5.1 redesign): the left-rail session
 * history. New design:
 *
 *   ┌──────────────────────────────────┐
 *   │ [ + New chat ]    <- pill button │
 *   │ 🔍 Search sessions              │
 *   │ ─────────────────────            │
 *   │ Today                            │
 *   │  Title …        [📌] [🗄] [✕]    │ <- actions reveal on hover
 *   │  3 msgs                           │
 *   │ Yesterday                        │
 *   │  Title …                          │
 *   │  1 msg                            │
 *   └──────────────────────────────────┘
 *
 * Empty untitled sessions are pruned in the parent (ChatPanel) by
 * passing in `sessions` already filtered. This component is purely
 * presentational.
 *
 * Row actions are hidden by default and shown on hover/focus, with
 * the pinned indicator visible only when `session.pinned === true`.
 */

export function SessionList(props: {
  sessions: SessionSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onPin: (id: string) => void;
  onArchive: (id: string) => void;
  onDelete: (id: string) => void;
  // v1.5.1.2 (audit hotfix): inline error from a failed
  // POST /api/sessions. Rendered below the "New chat" pill so
  // the user can see why the click "did nothing" without opening
  // DevTools. The parent (ChatPanel) owns the error state and
  // clears it on the next successful create or a manual dismiss.
  newSessionError?: string | null;
  onDismissError?: () => void;
}) {
  const groups = groupByRecency(props.sessions);
  return (
    <div className="session-list">
      <button
        type="button"
        className="session-list-pill"
        onClick={() => props.onNew()}
        data-testid="session-list-new-chat"
        aria-label="Start a new chat"
      >
        <Icon name="plus" size={14} />
        <span>New chat</span>
      </button>
      {props.newSessionError && (
        <div
          className="session-list-error"
          role="alert"
          data-testid="session-list-new-chat-error"
        >
          <span className="session-list-error-text">{props.newSessionError}</span>
          {props.onDismissError && (
            <button
              type="button"
              className="session-list-error-dismiss"
              onClick={props.onDismissError}
              aria-label="Dismiss new chat error"
              data-testid="session-list-new-chat-error-dismiss"
            >
              <Icon name="close" size={12} />
            </button>
          )}
        </div>
      )}
      {groups.length === 0 && (
        <div className="session-list-empty">No sessions yet.</div>
      )}
      {groups.map((g) => (
        <div key={g.label} className="session-list-group">
          <div className="session-list-group-label">{g.label}</div>
          {g.sessions.map((s) => (
            <SessionCard
              key={s.id}
              session={s}
              active={s.id === props.activeId}
              onSelect={() => props.onSelect(s.id)}
              onPin={() => props.onPin(s.id)}
              onArchive={() => props.onArchive(s.id)}
              onDelete={() => props.onDelete(s.id)}
            />
          ))}
        </div>
      ))}
    </div>
  );
}

function SessionCard(props: {
  session: SessionSummary;
  active: boolean;
  onSelect: () => void;
  onPin: () => void;
  onArchive: () => void;
  onDelete: () => void;
}) {
  const s = props.session;
  return (
    <div
      className={`session-card ${props.active ? "is-active" : ""}`}
      data-session-id={s.id}
      data-pinned={s.pinned ? "true" : undefined}
      onClick={props.onSelect}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          props.onSelect();
        }
      }}
    >
      <div className="session-card-body">
        <div className="session-card-title">
          {s.pinned && (
            <span className="session-card-pin" title="Pinned" aria-label="Pinned">
              <Icon name="pin" size={11} />
            </span>
          )}
          <span className="session-card-title-text">
            {s.title || "New session"}
          </span>
        </div>
        <div className="session-card-meta">
          {s.message_count} msg{s.message_count === 1 ? "" : "s"}
        </div>
      </div>
      <div className="session-card-actions">
        <button
          type="button"
          className="session-card-action"
          title={s.pinned ? "Unpin" : "Pin"}
          aria-label={s.pinned ? "Unpin session" : "Pin session"}
          aria-pressed={s.pinned}
          onClick={(e) => {
            e.stopPropagation();
            props.onPin();
          }}
          data-testid="session-card-pin"
        >
          <Icon name="pin" size={12} />
        </button>
        <button
          type="button"
          className="session-card-action"
          title="Archive"
          aria-label="Archive session"
          onClick={(e) => {
            e.stopPropagation();
            props.onArchive();
          }}
          data-testid="session-card-archive"
        >
          <Icon name="archive" size={12} />
        </button>
        <button
          type="button"
          className="session-card-action"
          title="Delete"
          aria-label="Delete session"
          onClick={(e) => {
            e.stopPropagation();
            props.onDelete();
          }}
          data-testid="session-card-delete"
        >
          <Icon name="trash" size={12} />
        </button>
      </div>
    </div>
  );
}

type Group = { label: string; sessions: SessionSummary[] };

function groupByRecency(sessions: SessionSummary[]): Group[] {
  const now = Date.now();
  const dayMs = 24 * 60 * 60 * 1000;
  const today: SessionSummary[] = [];
  const yesterday: SessionSummary[] = [];
  const older: SessionSummary[] = [];
  for (const s of sessions) {
    const ageDays = (now - s.updated_at) / dayMs;
    if (ageDays < 1) today.push(s);
    else if (ageDays < 2) yesterday.push(s);
    else older.push(s);
  }
  const groups: Group[] = [];
  if (today.length) groups.push({ label: "Today", sessions: today });
  if (yesterday.length) groups.push({ label: "Yesterday", sessions: yesterday });
  if (older.length) groups.push({ label: "Older", sessions: older });
  return groups;
}
