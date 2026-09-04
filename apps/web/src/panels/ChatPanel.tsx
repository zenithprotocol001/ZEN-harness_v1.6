import React, { useCallback, useEffect, useRef, useState } from "react";
import {
  BranchTip,
  ChatFrame,
  Message,
  Session,
  SessionSummary,
} from "../types/chat";
import { ModelSelect, ModelOption } from "../components/ModelSelect";
import { SettingsModal } from "../components/SettingsModal";
import { ModelConfigMenu } from "../components/ModelConfigMenu";
import { TokenCounter } from "../components/TokenCounter";
import { SessionList } from "./SessionList";
import { SearchOverlay } from "../components/SearchOverlay";
import { LeafSwitcher } from "../components/LeafSwitcher";
import { PathReconstruction } from "../components/PathReconstruction";
import { AttachmentsPanel } from "../components/AttachmentsPanel";
import { MessageBubble } from "../components/MessageBubble";
import { WelcomeView, type WelcomeCard } from "../components/WelcomeView";
import { DeveloperModeMenu } from "../components/DeveloperModeMenu";
import { Icon } from "../components/icons";

type WsState = "connecting" | "open" | "closed" | "unauthorized" | "no-token";

const EMPTY_SESSION_MESSAGES: Message[] = [];

/**
 * <ChatPanel /> (v1.5.1, "Redesigned Product"): the polished,
 * general-user-grade chat shell.
 *
 * Notable v1.5.1 changes over v1.5.0.1:
 *   - WelcomeView renders in the no-session state (no empty rail).
 *   - First card click or first typed send auto-creates a session.
 *     In-flight promise ref prevents double-creation races.
 *   - Stop/cancel while streaming; `chat.cancel` over WS aborts
 *     the server-side turn and finalizes with `cancelled: true`.
 *   - Scroll pinning: "Jump to latest" pill when the user is
 *     scrolled up; auto-pin on every delta when near the bottom.
 *   - developerMode persists in localStorage.
 *   - All emoji replaced with <Icon> (single SVG set).
 *   - SettingsModal/ModelConfigMenu portaled to document.body so
 *     the app chrome (brand/tabs/token) stays visible.
 *   - Mixed 2+2 welcome cards: 2 conversation starters + 2
 *     action cards (Open a session, Manage API keys).
 *   - Copy discipline: "Ready" / "Offline" / "Message Qwen…";
 *     token counter hidden unless developerMode.
 *   - Sidebar: "New chat" pill + hover-revealed row actions;
 *     pinned sessions show the pin icon always, others on hover.
 *   - Empty untitled sessions pruned in the rail (display only).
 *
 * The v1.5.0 server features (branching, attachments, LeafSwitcher,
 * Tree view) still work; they're surfaced only when `developerMode`
 * is on.
 */
export function ChatPanel(props: {
  authToken: string;
  wsState: WsState;
}) {
  const [sessions, setSessions] = useState<SessionSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [active, setActive] = useState<Session | null>(null);
  const [streaming, setStreaming] = useState<string>("");
  const [streamingCancelled, setStreamingCancelled] = useState<boolean>(false);
  const [searchOpen, setSearchOpen] = useState<boolean>(false);
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [llmOk, setLlmOk] = useState<boolean | null>(null);
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [configOpen, setConfigOpen] = useState<boolean>(false);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [branches, setBranches] = useState<BranchTip[]>([]);
  const [branchTreeOpen, setBranchTreeOpen] = useState<boolean>(false);
  const [attachmentsOpen, setAttachmentsOpen] = useState<boolean>(false);
  const [gearOpen, setGearOpen] = useState<boolean>(false);
  // v1.5.1.2 (audit hotfix): surface session-create failures as
  // an inline error in the rail. Previously a 500 on POST /api/sessions
  // was silently swallowed by newSession(), so the user saw the
  // "click does nothing" failure with no recourse. The error is
  // displayed in the rail (where the "New chat" button lives) and
  // cleared on the next successful create or on a manual dismiss.
  const [newSessionError, setNewSessionError] = useState<string | null>(null);
  // v1.5.1: developer mode persists in localStorage.
  const [developerMode, setDeveloperModeState] = useState<boolean>(() => {
    try {
      return localStorage.getItem("dhc.developerMode") === "1";
    } catch {
      return false;
    }
  });
  const setDeveloperMode = (v: boolean) => {
    setDeveloperModeState(v);
    try {
      localStorage.setItem("dhc.developerMode", v ? "1" : "0");
    } catch { /* ignore */ }
  };
  // v1.5.1.4 (audit hotfix #4): the model picker is also
  // rendered when no session is active. The user picks a
  // model here with no session → it updates
  // `dhc.defaultModelId` in localStorage. When the user
  // creates a new session, the server's session create
  // handler pre-selects this model (per ADR-0011).
  const [defaultModelId, setDefaultModelIdState] = useState<string>(() => {
    try {
      const v = localStorage.getItem("dhc.defaultModelId");
      if (v && typeof v === "string") return v;
    } catch { /* ignore */ }
    return "mock-llm/default";
  });
  const setDefaultModelId = (modelId: string) => {
    setDefaultModelIdState(modelId);
    try {
      localStorage.setItem("dhc.defaultModelId", modelId);
    } catch { /* ignore */ }
  };
  // v1.5.1: scroll pinning state.
  const [autoScrollPinned, setAutoScrollPinned] = useState<boolean>(true);
  const messagesRef = useRef<HTMLDivElement | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  // v1.5.1: in-flight new-session promise (race guard).
  const newSessionInFlight = useRef<Promise<Session | null> | null>(null);

  const refreshBranches = useCallback(async (sid: string) => {
    try {
      const r = await fetch(`/api/sessions/${sid}/branches`);
      if (!r.ok) return;
      const body = await r.json();
      setBranches(Array.isArray(body.branches) ? body.branches : []);
    } catch {
      // swallow
    }
  }, []);

  const refreshSessions = useCallback(async (q: string) => {
    try {
      const url = q
        ? `/api/sessions?search=${encodeURIComponent(q)}`
        : "/api/sessions";
      const r = await fetch(url);
      if (!r.ok) return;
      const body = await r.json();
      setSessions(body.sessions || []);
    } catch {
      // swallow
    }
  }, []);

  const loadSession = useCallback(async (id: string) => {
    try {
      const r = await fetch(`/api/sessions/${id}`);
      if (!r.ok) return;
      const body: Session = await r.json();
      setActive(body);
    } catch {
      // swallow
    }
  }, []);

  const onForked = useCallback(
    async (_newBranchRootId: string) => {
      if (activeId) {
        await refreshBranches(activeId);
        await loadSession(activeId);
      }
    },
    [activeId, refreshBranches, loadSession],
  );

  const onTipSwitched = useCallback(
    async (_newActiveId: string) => {
      if (activeId) {
        await loadSession(activeId);
        await refreshBranches(activeId);
      }
    },
    [activeId, loadSession, refreshBranches],
  );

  // Open the WS chat channel.
  useEffect(() => {
    if (!props.authToken) return;
    const proto = window.location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${window.location.host}/ws/chat?token=${encodeURIComponent(props.authToken)}`;
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.onmessage = (ev) => {
      let frame: ChatFrame;
      try {
        frame = JSON.parse(ev.data) as ChatFrame;
      } catch {
        return;
      }
      if (frame.type === "chat.delta") {
        setStreamingCancelled(false);
        setStreaming((cur) => cur + frame.delta);
      } else if (frame.type === "chat.tool_call") {
        setActive((cur) => {
          if (!cur) return cur;
          const last = cur.messages[cur.messages.length - 1];
          if (last && last.role === "assistant" && last.tool_calls) {
            last.tool_calls.push(...frame.tool_calls);
          } else {
            cur.messages.push({
              id: "tc_" + Date.now().toString(36),
              role: "tool",
              content: JSON.stringify(frame.tool_calls),
              ts_ms: Date.now(),
              tool_calls: frame.tool_calls,
            });
          }
          return { ...cur };
        });
      } else if (frame.type === "chat.done") {
        setActive((cur) => {
          if (!cur) return cur;
          cur.messages.push({
            id: "m_" + Date.now().toString(36),
            role: "assistant",
            content: "",
            ts_ms: Date.now(),
            tokens: frame.tokens,
            cancelled: frame.cancelled === true || streamingCancelledRef.current,
          });
          const last = cur.messages[cur.messages.length - 1];
          if (last && last.role === "assistant") {
            last.content = streamingRef.current;
          }
          return { ...cur };
        });
        setStreaming("");
        setStreamingCancelled(false);
        void refreshSessions(searchQuery);
        if (frame.session_id) {
          void loadSession(frame.session_id);
        } else if (activeId) {
          void loadSession(activeId);
        }
      } else if (frame.type === "chat.error") {
        setStreaming("");
        setStreamingCancelled(false);
      } else if (frame.type === "chat.cancelled") {
        // Server acknowledges a cancel; finalize the in-flight
        // turn with whatever was streamed.
        setStreamingCancelled(true);
      }
    };
    return () => {
      try {
        ws.close();
      } catch {
        /* ignore */
      }
      wsRef.current = null;
    };
  }, [props.authToken, refreshSessions, loadSession, searchQuery, activeId]);

  const streamingRef = useRef<string>("");
  useEffect(() => {
    streamingRef.current = streaming;
  }, [streaming]);
  const streamingCancelledRef = useRef<boolean>(false);
  useEffect(() => {
    streamingCancelledRef.current = streamingCancelled;
  }, [streamingCancelled]);

  // Initial load: list + LLM health + model registry.
  useEffect(() => {
    void refreshSessions("");
    fetch("/api/llm/health")
      .then((r) => r.json())
      .then((b) => setLlmOk(Boolean(b.ok)))
      .catch(() => setLlmOk(false));
    fetch("/api/models", { credentials: "same-origin" })
      .then((r) => (r.ok ? r.json() : null))
      .then((b: { models?: ModelOption[] } | null) => {
        if (b && Array.isArray(b.models)) setModels(b.models);
      })
      .catch(() => {
        /* /api/models may 404 in older builds */
      });
  }, [refreshSessions]);

  // Reload the active session when activeId changes.
  useEffect(() => {
    if (activeId) {
      void loadSession(activeId);
      void refreshBranches(activeId);
    } else {
      setActive(null);
      setBranches([]);
    }
  }, [activeId, loadSession, refreshBranches]);

  // Hotkey: Ctrl+K opens the search overlay.
  useEffect(() => {
    function onKey(ev: KeyboardEvent) {
      if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
        ev.preventDefault();
        setSearchOpen((s) => !s);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // v1.5.1: auto-scroll pinning. When the user is near the bottom
  // (within 40px), keep pinned. When they scroll up, unpin and
  // show a "Jump to latest" pill. The pill click re-pins and
  // scrolls to bottom.
  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    function onScroll() {
      if (!el) return;
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
      setAutoScrollPinned(distFromBottom < 40);
    }
    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    const el = messagesRef.current;
    if (!el) return;
    if (!autoScrollPinned) return;
    el.scrollTop = el.scrollHeight;
  }, [streaming, active?.messages?.length, autoScrollPinned]);

  // ---------- handlers ----------

  // v1.5.1: create-session promise, single-flight.
  const newSession = useCallback(async (): Promise<Session | null> => {
    if (newSessionInFlight.current) return newSessionInFlight.current;
    const p = (async () => {
      try {
        // v1.5.1.4: send the user's `dhc.defaultModelId`
        // preference as the session's pre-selected model.
        // The server validates the id against the closed
        // model registry (400 on unknown) so an attacker
        // cannot inject arbitrary model strings.
        const r = await fetch("/api/sessions", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ model: defaultModelId }),
        });
        if (!r.ok) {
          // v1.5.1.2: surface the failure inline. Map common 5xx
          // codes to friendly text; do not echo the raw aiohttp
          // body, which can include file paths from a future
          // PermissionError.
          const friendly =
            r.status >= 500
              ? "Server is busy. Try again or restart the server."
              : r.status === 401 || r.status === 403
                ? "Not authorized to start a new session."
                : `Couldn't start a new session (HTTP ${r.status}).`;
          setNewSessionError(friendly);
          return null;
        }
        const s: Session = await r.json();
        setActiveId(s.id);
        setNewSessionError(null);
        await refreshSessions(searchQueryRef.current);
        return s;
      } catch {
        setNewSessionError("Network error starting a new session.");
        return null;
      } finally {
        newSessionInFlight.current = null;
      }
    })();
    newSessionInFlight.current = p;
    return p;
  }, [refreshSessions]);

  // Keep a ref to the latest searchQuery so newSession() can refresh
  // the right list without re-creating the callback on every keystroke.
  const searchQueryRef = useRef<string>("");
  useEffect(() => {
    searchQueryRef.current = searchQuery;
  }, [searchQuery]);

  async function pinSession(id: string) {
    const s = sessions.find((x) => x.id === id);
    if (!s) return;
    await fetch(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ pinned: !s.pinned }),
    });
    await refreshSessions(searchQuery);
  }

  async function setSessionModel(id: string, modelId: string) {
    await fetch(`/api/sessions/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model: modelId }),
    });
    const r = await fetch(`/api/sessions/${id}`);
    if (r.ok) {
      const updated: Session = await r.json();
      setActive(updated);
    }
  }

  async function archiveSession(id: string) {
    await fetch(`/api/sessions/${id}`, { method: "DELETE" });
    if (activeId === id) {
      setActiveId(null);
    }
    await refreshSessions(searchQuery);
  }

  async function deleteSession(id: string) {
    await fetch(`/api/sessions/${id}?hard=1`, { method: "DELETE" });
    if (activeId === id) {
      setActiveId(null);
    }
    await refreshSessions(searchQuery);
  }

  // v1.5.1: send() now auto-creates a session when there is none.
  function send(text: string) {
    const t = text.trim();
    if (!t) return;
    if (!activeId) {
      // Single-flight auto-create; once activeId is set, the
      // effect above re-runs and we'd want to call send again
      // with the now-active session. We achieve that by chaining
      // off the newSession() promise.
      void newSession().then((s) => {
        if (s) {
          // activeId is now set; dispatch the message.
          dispatchSend(s.id, t);
        }
      });
      return;
    }
    dispatchSend(activeId, t);
  }

  function dispatchSend(sid: string, text: string) {
    setActive((cur) => {
      if (!cur) return cur;
      cur.messages.push({
        id: "m_" + Date.now().toString(36),
        role: "user",
        content: text,
        ts_ms: Date.now(),
      });
      return { ...cur };
    });
    setStreamingCancelled(false);
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(
        JSON.stringify({
          type: "chat.send",
          session_id: sid,
          text,
        }),
      );
    }
  }

  // v1.5.1: Stop while streaming. Sends `chat.cancel` over WS.
  function stopStreaming() {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN && activeId) {
      setStreamingCancelled(true);
      ws.send(
        JSON.stringify({
          type: "chat.cancel",
          session_id: activeId,
        }),
      );
    } else {
      // No active stream; just clear locally.
      setStreaming("");
      setStreamingCancelled(false);
    }
  }

  // v1.5.1: WelcomeView card handlers. Two conversation cards
  // dispatch a prompt; two action cards perform the action.
  const onPickSuggestion = useCallback(
    (text: string) => {
      send(text);
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [activeId, wsRef.current],
  );

  function onWelcomeAction(action: "open-session-list" | "manage-api-keys") {
    if (action === "open-session-list") {
      // Focus the first session card in the sidebar; if there are
      // none, just open the search overlay so the user can find one.
      const card = document.querySelector<HTMLElement>(".session-card");
      if (card) {
        card.focus();
      } else {
        setSearchOpen(true);
      }
    } else if (action === "manage-api-keys") {
      setGearOpen(false);
      setSettingsOpen(true);
    }
  }

  // v1.5.1: empty-untitled-session pruning. Active session is
  // always shown even if empty+untitled, to keep the highlighted
  // row in sync with what's on screen.
  const visibleSessions = React.useMemo(() => {
    const filtered = sessions.filter((s) => {
      if (s.message_count > 0) return true;
      const t = (s.title ?? "").trim();
      if (t && t !== "New session") return true;
      // Active session is always kept visible.
      if (s.id === activeId) return true;
      return false;
    });
    return filtered;
  }, [sessions, activeId]);

  const messages = active?.messages ?? EMPTY_SESSION_MESSAGES;
  const isEmptySession = activeId !== null && messages.length === 0;
  const noSession = activeId === null;
  const isStreaming = streaming.length > 0;

  // v1.5.1: 4 welcome cards. Two conversation, two action.
  const welcomeCards: WelcomeCard[] = [
    {
      slug: "run-an-eval",
      title: "Run an eval",
      description: "Ask the assistant to score a pasted module body against the harness rubric.",
      kind: "conversation",
      icon: "play",
    },
    {
      slug: "open-a-session",
      title: "Open a session",
      description: "Focuses the session list so you can pick a recent chat or start a new one.",
      kind: "action",
      action: "open-session-list",
      icon: "model",
    },
    {
      slug: "configure-a-model",
      title: "Configure a model",
      description: "Per-session temperature, top_p, max tokens, and system prompt — opens the model config menu.",
      kind: "conversation",
      icon: "model",
    },
    {
      slug: "manage-api-keys",
      title: "Manage API keys",
      description: "Opens Settings → API Keys. Default key per provider, per-model overrides.",
      kind: "action",
      action: "manage-api-keys",
      icon: "key",
    },
  ];

  return (
    <div className="chat-app">
      <aside className="chat-sidebar">
        <div className="chat-sidebar-search">
          <button
            type="button"
            className="chat-sidebar-search-button"
            onClick={() => setSearchOpen(true)}
            data-testid="chat-sidebar-search"
            aria-label="Search sessions (Ctrl+K)"
          >
            <Icon name="search" size={14} />
            <span>Search</span>
          </button>
        </div>
        <SessionList
          sessions={visibleSessions}
          activeId={activeId}
          onSelect={(id) => setActiveId(id)}
          onNew={newSession}
          onPin={pinSession}
          onArchive={archiveSession}
          onDelete={deleteSession}
          newSessionError={newSessionError}
          onDismissError={() => setNewSessionError(null)}
        />
      </aside>
      <main className="chat-main">
        <header className="chat-header-clean">
          <div className="chat-header-clean-title">
            {noSession
              ? "DHC Chat"
              : active?.title || "New session"}
          </div>
          <div className="chat-header-clean-right">
            {active ? (
              <div className="chat-model-pill">
                <span className="chat-model-pill-label">Model</span>
                <ModelSelect
                  value={active.model || "mock-llm/default"}
                  onChange={(modelId) => setSessionModel(active.id, modelId)}
                  disabled={props.wsState === "connecting"}
                />
              </div>
            ) : (
              // v1.5.1.4: also render the picker when no
              // session is active. The picker updates
              // `dhc.defaultModelId` in localStorage. New
              // sessions will pre-select this model.
              <div
                className="chat-model-pill"
                title="Sets the default model for new sessions."
                data-testid="chat-model-pill-default"
              >
                <span className="chat-model-pill-label">Default model</span>
                <ModelSelect
                  value={defaultModelId}
                  onChange={(modelId) => setDefaultModelId(modelId)}
                  disabled={false}
                />
              </div>
            )}
            {developerMode && active && branches.length > 0 && (
              <div className="chat-developer-leaf">
                <LeafSwitcher
                  sessionId={active.id}
                  branches={branches.map((b) => ({
                    id: b.id,
                    active: b.active,
                  }))}
                  onSwitched={onTipSwitched}
                />
              </div>
            )}
            {developerMode && active && (
              <button
                type="button"
                className={`chat-developer-tree-button ${branchTreeOpen ? "is-active" : ""}`}
                onClick={() => setBranchTreeOpen((v) => !v)}
                aria-label="Toggle branch tree view"
                aria-pressed={branchTreeOpen}
                data-testid="chat-developer-tree"
              >
                <Icon name="tree" size={14} />
                <span>Tree</span>
              </button>
            )}
            <button
              type="button"
              className="chat-gear-button"
              onClick={() => setGearOpen((v) => !v)}
              aria-label="Open settings menu"
              aria-haspopup="menu"
              aria-expanded={gearOpen}
              data-testid="chat-gear"
            >
              <Icon name="gear" size={16} />
            </button>
            <DeveloperModeMenu
              open={gearOpen}
              onClose={() => setGearOpen(false)}
              developerMode={developerMode}
              onToggleDeveloperMode={() => setDeveloperMode(!developerMode)}
              llmOk={llmOk}
              onOpenKeys={() => setSettingsOpen(true)}
              onOpenConfig={() => setConfigOpen(true)}
            />
          </div>
        </header>
        <div className="chat-messages-clean" ref={messagesRef} data-testid="chat-messages">
          {(noSession || isEmptySession) && !branchTreeOpen && (
            <WelcomeView
              llmOk={llmOk}
              cards={welcomeCards}
              onPickSuggestion={onPickSuggestion}
              onAction={onWelcomeAction}
            />
          )}
          {activeId && !isEmptySession && branchTreeOpen && (
            <PathReconstruction sessionId={activeId} />
          )}
          {activeId && !isEmptySession && !branchTreeOpen && (
            <div className="chat-messages-list">
              {messages.map((m) => (
                <MessageBubble
                  key={m.id}
                  message={m}
                  activeId={activeId}
                  onForked={onForked}
                  developerMode={developerMode}
                />
              ))}
              {isStreaming && (
                <div
                  className="chat-bubble chat-bubble-assistant chat-bubble-streaming"
                  data-testid="chat-bubble-streaming"
                >
                  <div className="chat-bubble-avatar chat-bubble-avatar-assistant">A</div>
                  <div className="chat-bubble-body">
                    <div className="chat-bubble-content" aria-live="polite">
                      {streaming}
                      <span className="chat-bubble-cursor" aria-hidden="true">▌</span>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}
          {!autoScrollPinned && activeId && !isEmptySession && !branchTreeOpen && (
            <button
              type="button"
              className="chat-jump-latest"
              onClick={() => {
                const el = messagesRef.current;
                if (el) {
                  el.scrollTop = el.scrollHeight;
                  setAutoScrollPinned(true);
                }
              }}
              data-testid="chat-jump-latest"
              aria-label="Jump to latest"
            >
              <Icon name="arrowDown" size={12} />
              <span>Jump to latest</span>
            </button>
          )}
        </div>
        {activeId && attachmentsOpen && (
          <div className="chat-attachments-drawer-clean">
            <AttachmentsPanel sessionId={activeId} />
          </div>
        )}
        <InputArea
          onSend={send}
          onStop={stopStreaming}
          isStreaming={isStreaming}
          disabled={!activeId || props.wsState !== "open"}
          attachmentsOpen={attachmentsOpen}
          onToggleAttachments={() => setAttachmentsOpen((v) => !v)}
          totalTokens={
            active?.usage_totals?.total_tokens ??
            (active?.messages ?? []).reduce(
              (acc, m) =>
                acc + (m.tokens?.prompt ?? 0) + (m.tokens?.completion ?? 0),
              0,
            )
          }
          contextWindow={
            (() => {
              const m = models.find(
                (x) => x.id === (active?.model || "mock-llm/default"),
              );
              return m?.context_length ?? 0;
            })()
          }
          showTokenCounter={developerMode}
        />
      </main>
      <SearchOverlay
        open={searchOpen}
        query={searchQuery}
        onChange={(q) => {
          setSearchQuery(q);
          void refreshSessions(q);
        }}
        onClose={() => setSearchOpen(false)}
      />
      <SettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        models={models}
        activeProvider={
          active && typeof active.model === "string" && active.model.includes("/")
            ? active.model.split("/")[0]
            : null
        }
      />
      {active && (
        <ModelConfigMenu
          sessionID={active.id}
          isOpen={configOpen}
          onClose={() => setConfigOpen(false)}
        />
      )}
    </div>
  );
}

function InputArea(props: {
  onSend: (text: string) => void;
  onStop: () => void;
  isStreaming: boolean;
  disabled: boolean;
  totalTokens: number;
  contextWindow: number;
  attachmentsOpen: boolean;
  onToggleAttachments: () => void;
  showTokenCounter: boolean;
}) {
  const [text, setText] = useState<string>("");
  const taRef = React.useRef<HTMLTextAreaElement | null>(null);
  React.useEffect(() => {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    const next = Math.min(200, Math.max(24, ta.scrollHeight));
    ta.style.height = `${next}px`;
  }, [text]);
  const canSend = !props.disabled && text.trim().length > 0;
  return (
    <div className="chat-input-shell">
      <div className="chat-input-card">
        <textarea
          ref={taRef}
          className="chat-input-textarea"
          rows={1}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              const t = text.trim();
              if (t) {
                props.onSend(t);
                setText("");
              }
            }
          }}
          placeholder="Message Qwen…"
          disabled={props.disabled}
          aria-label="Message input"
        />
        <div className="chat-input-toolbar">
          <button
            type="button"
            className="chat-input-attach"
            onClick={props.onToggleAttachments}
            aria-label="Toggle attachments panel"
            aria-pressed={props.attachmentsOpen}
            data-testid="chat-input-attach"
            disabled={props.disabled}
          >
            <Icon name="attach" size={14} />
            <span>Attach</span>
          </button>
          {props.isStreaming ? (
            <button
              type="button"
              className="chat-input-stop"
              onClick={props.onStop}
              aria-label="Stop generating"
              data-testid="chat-input-stop"
            >
              <Icon name="stop" size={12} />
              <span className="chat-input-send-label">Stop</span>
            </button>
          ) : (
            <button
              type="button"
              className={`chat-input-send ${canSend ? "is-active" : ""}`}
              onClick={() => {
                const t = text.trim();
                if (t) {
                  props.onSend(t);
                  setText("");
                }
              }}
              disabled={!canSend}
              aria-disabled={!canSend}
              aria-label="Send message"
              data-testid="chat-input-send"
            >
              <Icon name="paperPlane" size={12} />
              <span className="chat-input-send-label">Send</span>
            </button>
          )}
        </div>
      </div>
      {props.showTokenCounter && (
        <div className="chat-input-meta">
          <TokenCounter
            totalTokens={props.totalTokens}
            contextWindow={props.contextWindow}
          />
        </div>
      )}
    </div>
  );
}
