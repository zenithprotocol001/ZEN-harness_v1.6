// Session type definitions shared by ChatPanel, SessionList, and
// SearchOverlay. Mirrors the JSON shape of GET /api/sessions and
// GET /api/sessions/{id}.

export type Role = "user" | "assistant" | "system" | "tool";

// v1.5.0 (ADR-0020): an out-of-line attachment ref. The ref is
// `attachment:{session_id}:{uuid}`. The MIME is on the allowlist.
export type AttachmentRef = {
  ref: string;
  mime: string;
  sha256: string;
  size: number;
};

// v1.5.0 (ADR-0020): an inline attachment (text-like MIME only).
export type InlineAttachment = {
  mime: string;
  body: string;
  sha256: string;
};

export type Message = {
  id: string;
  role: Role;
  content: string;
  ts_ms: number;
  tool_calls?: ToolCall[];
  tokens?: { prompt: number; completion: number };
  truncated?: boolean;
  // v1.5.1: set on an assistant message whose stream was
  // cancelled mid-flight by the user. The bubble meta line shows
  // an amber "cancelled" chip.
  cancelled?: boolean;
  // v1.5.0 (ADR-0019): the parent message id. None for the root
  // of a branch; otherwise the id of the message this one extends.
  parent_id?: string | null;
  // v1.5.0 (ADR-0020): inline attachments (text/SVG only).
  inline_attachments?: InlineAttachment[];
  // v1.5.0 (ADR-0020): out-of-line attachments (binary only).
  attachments?: AttachmentRef[];
};

export type ToolCall = {
  index?: number;
  id?: string;
  type?: string;
  function?: { name?: string; arguments?: string };
};

export type SessionSummary = {
  id: string;
  title: string;
  created_at: number;
  updated_at: number;
  message_count: number;
  model: string;
  tags: string[];
  pinned: boolean;
  archived: boolean;
  // v1.3.2: per-session token usage. Optional because old
  // summary endpoints may not include it.
  usage_totals?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    last_turn_prompt: number;
    last_turn_completion: number;
  };
  // v1.5.0 (ADR-0019): the active_tip_id of the session. None for
  // an empty session.
  active_tip_id?: string | null;
};

export type Session = SessionSummary & {
  messages: Message[];
};

// v1.5.0 (ADR-0019): a single branch tip in the BRANCH_LIST response.
export type BranchTip = {
  id: string;
  active: boolean;
  path: Array<{
    id: string;
    role: Role;
    preview: string;
  }>;
};

export type ChatFrame =
  | { type: "chat.delta"; session_id: string; delta: string }
  | { type: "chat.tool_call"; session_id: string; tool_calls: ToolCall[] }
  | { type: "chat.done"; session_id: string; tokens: { prompt: number; completion: number }; latency_ms: number; cancelled?: boolean }
  | { type: "chat.cancelled"; session_id: string; noop?: boolean }
  | { type: "chat.error"; code: string; message?: string; session_id?: string };
