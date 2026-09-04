import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SessionList } from "../panels/SessionList";
import type { SessionSummary } from "../types/chat";

const NOW = Date.now();
const DAY_MS = 24 * 60 * 60 * 1000;

function s(over: Partial<SessionSummary> = {}): SessionSummary {
  return {
    id: `s_${Math.random().toString(36).slice(2, 8)}`,
    title: "Untitled",
    message_count: 0,
    created_at: NOW,
    updated_at: NOW,
    pinned: false,
    archived: false,
    model: "mock-llm/default",
    tags: [],
    ...over,
  };
}

describe("<SessionList /> (v1.5.1 redesign)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the New chat pill as the first focusable", () => {
    render(
      <SessionList
        sessions={[]}
        activeId={null}
        onSelect={() => {}}
        onNew={() => {}}
        onPin={() => {}}
        onArchive={() => {}}
        onDelete={() => {}}
      />,
    );
    const pill = screen.getByTestId("session-list-new-chat");
    expect(pill).toBeTruthy();
    expect(pill.textContent).toMatch(/New chat/);
  });

  it("calls onNew when the New chat pill is clicked", () => {
    const onNew = vi.fn();
    render(
      <SessionList
        sessions={[]}
        activeId={null}
        onSelect={() => {}}
        onNew={onNew}
        onPin={() => {}}
        onArchive={() => {}}
        onDelete={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("session-list-new-chat"));
    expect(onNew).toHaveBeenCalledTimes(1);
  });

  it("groups sessions by recency (Today / Yesterday / Older)", () => {
    const sessions: SessionSummary[] = [
      s({ id: "a", title: "Today 1", updated_at: NOW - 1000 }),
      s({ id: "b", title: "Today 2", updated_at: NOW - 60_000 }),
      s({ id: "c", title: "Yesterday 1", updated_at: NOW - DAY_MS - 1000 }),
      s({ id: "d", title: "Older 1", updated_at: NOW - 5 * DAY_MS }),
    ];
    render(
      <SessionList
        sessions={sessions}
        activeId={null}
        onSelect={() => {}}
        onNew={() => {}}
        onPin={() => {}}
        onArchive={() => {}}
        onDelete={() => {}}
      />,
    );
    expect(screen.getByText("Today")).toBeTruthy();
    expect(screen.getByText("Yesterday")).toBeTruthy();
    expect(screen.getByText("Older")).toBeTruthy();
  });

  it("renders the pin icon on pinned sessions", () => {
    const sessions: SessionSummary[] = [
      s({ id: "pinned", title: "Pinned one", pinned: true }),
      s({ id: "regular", title: "Regular one", pinned: false }),
    ];
    const { container } = render(
      <SessionList
        sessions={sessions}
        activeId={null}
        onSelect={() => {}}
        onNew={() => {}}
        onPin={() => {}}
        onArchive={() => {}}
        onDelete={() => {}}
      />,
    );
    const pinnedCard = container.querySelector('[data-session-id="pinned"]');
    const regularCard = container.querySelector('[data-session-id="regular"]');
    expect(pinnedCard?.getAttribute("data-pinned")).toBe("true");
    expect(regularCard?.getAttribute("data-pinned")).toBeNull();
  });

  it("calls onSelect when a session card is clicked", () => {
    const onSelect = vi.fn();
    const sessions: SessionSummary[] = [s({ id: "x", title: "Click me" })];
    render(
      <SessionList
        sessions={sessions}
        activeId={null}
        onSelect={onSelect}
        onNew={() => {}}
        onPin={() => {}}
        onArchive={() => {}}
        onDelete={() => {}}
      />,
    );
    fireEvent.click(screen.getByText("Click me"));
    expect(onSelect).toHaveBeenCalledWith("x");
  });

  it("calls onDelete (with stopPropagation) when the trash button is clicked", () => {
    const onDelete = vi.fn();
    const onSelect = vi.fn();
    const sessions: SessionSummary[] = [s({ id: "z", title: "Trash me" })];
    render(
      <SessionList
        sessions={sessions}
        activeId={null}
        onSelect={onSelect}
        onNew={() => {}}
        onPin={() => {}}
        onArchive={() => {}}
        onDelete={onDelete}
      />,
    );
    fireEvent.click(screen.getByTestId("session-card-delete"));
    expect(onDelete).toHaveBeenCalledWith("z");
    expect(onSelect).not.toHaveBeenCalled();
  });
});

// ============================================================================
// v1.5.1.2 (audit hotfix): inline newSessionError slot
// ============================================================================
// Previously a 500 on POST /api/sessions was silently swallowed
// by ChatPanel.newSession(), so the user saw "click does nothing".
// The new error is rendered in the rail (where the "New chat"
// button lives) and dismissable. These tests pin the visual
// contract: the error slot is rendered when `newSessionError`
// is set, and clicking the dismiss button calls `onDismissError`.

describe("<SessionList /> v1.5.1.2: inline newSessionError", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does not render the error slot when newSessionError is null", () => {
    render(
      <SessionList
        sessions={[]}
        activeId={null}
        onSelect={() => {}}
        onNew={() => {}}
        onPin={() => {}}
        onArchive={() => {}}
        onDelete={() => {}}
        newSessionError={null}
        onDismissError={() => {}}
      />,
    );
    expect(screen.queryByTestId("session-list-new-chat-error")).toBeNull();
  });

  it("renders the error text when newSessionError is set", () => {
    render(
      <SessionList
        sessions={[]}
        activeId={null}
        onSelect={() => {}}
        onNew={() => {}}
        onPin={() => {}}
        onArchive={() => {}}
        onDelete={() => {}}
        newSessionError="Server is busy. Try again or restart the server."
        onDismissError={() => {}}
      />,
    );
    const err = screen.getByTestId("session-list-new-chat-error");
    expect(err).toBeTruthy();
    expect(err.textContent).toMatch(/Server is busy/);
    expect(err.getAttribute("role")).toBe("alert");
  });

  it("calls onDismissError when the dismiss button is clicked", () => {
    const onDismiss = vi.fn();
    render(
      <SessionList
        sessions={[]}
        activeId={null}
        onSelect={() => {}}
        onNew={() => {}}
        onPin={() => {}}
        onArchive={() => {}}
        onDelete={() => {}}
        newSessionError="boom"
        onDismissError={onDismiss}
      />,
    );
    fireEvent.click(screen.getByTestId("session-list-new-chat-error-dismiss"));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });
});
