import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import { ChatPanel } from "../panels/ChatPanel";

/**
 * The ChatPanel owns a WebSocket. In tests we need a stub
 * WebSocket that the ChatPanel can connect to without actually
 * opening a network connection.
 */
class StubWebSocket {
  static instances: StubWebSocket[] = [];
  url: string;
  readyState = 0; // CONNECTING
  onopen: ((ev: Event) => void) | null = null;
  onclose: ((ev: CloseEvent) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  sent: string[] = [];
  constructor(url: string) {
    this.url = url;
    StubWebSocket.instances.push(this);
  }
  send(data: string) {
    this.sent.push(data);
  }
  close() {
    this.readyState = 3;
    if (this.onclose) {
      this.onclose(new CloseEvent("close"));
    }
  }
  // helpers
  open() {
    this.readyState = 1;
    if (this.onopen) this.onopen(new Event("open"));
  }
  message(data: unknown) {
    if (this.onmessage) {
      this.onmessage(new MessageEvent("message", { data: JSON.stringify(data) }));
    }
  }
}

beforeEach(() => {
  StubWebSocket.instances = [];
  // @ts-expect-error: stub for tests
  globalThis.WebSocket = StubWebSocket;
  // Mock fetch for /api/sessions, /api/llm/health, /api/models
  globalThis.fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const m = init?.method ?? "GET";
    if (m === "POST" && url === "/api/sessions") {
      const id = `s_${Math.random().toString(36).slice(2, 8)}`;
      return new Response(
        JSON.stringify({
          id,
          title: "New session",
          created_at: Date.now(),
          updated_at: Date.now(),
          pinned: false,
          model: "mock-llm/default",
          messages: [],
        }),
        { status: 201, headers: { "Content-Type": "application/json" } },
      );
    }
    if (m === "GET" && url === "/api/llm/health") {
      return new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (m === "GET" && url === "/api/models") {
      return new Response(
        JSON.stringify({
          models: [
            {
              id: "mock-llm/default",
              name: "Mock LLM",
              provider: "mock-llm",
              context_length: 8192,
              pricing_input: 0,
              pricing_output: 0,
              capabilities: ["chat"],
            },
          ],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }
    if (m === "GET" && url === "/api/sessions") {
      return new Response(JSON.stringify({ sessions: [] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    return new Response("not found in stub", { status: 404 });
  }) as unknown as typeof fetch;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("<ChatPanel /> (v1.5.1 redesign)", () => {
  it("renders the WelcomeView in the no-session state", () => {
    render(<ChatPanel authToken="t" wsState="open" />);
    expect(screen.getByTestId("chat-welcome")).toBeTruthy();
  });

  it("hides the token counter in the general (non-developer) view", () => {
    const { container } = render(<ChatPanel authToken="t" wsState="open" />);
    expect(container.querySelector(".chat-input-meta")).toBeNull();
  });

  it("shows the gear button", () => {
    render(<ChatPanel authToken="t" wsState="open" />);
    expect(screen.getByTestId("chat-gear")).toBeTruthy();
  });

  it("opens the gear menu on click; Escape closes it", () => {
    render(<ChatPanel authToken="t" wsState="open" />);
    fireEvent.click(screen.getByTestId("chat-gear"));
    expect(screen.getByTestId("chat-gear-menu")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByTestId("chat-gear-menu")).toBeNull();
  });

  it("toggling developer mode persists to localStorage", () => {
    localStorage.clear();
    render(<ChatPanel authToken="t" wsState="open" />);
    fireEvent.click(screen.getByTestId("chat-gear"));
    fireEvent.click(screen.getByTestId("chat-gear-devmode"));
    expect(localStorage.getItem("dhc.developerMode")).toBe("1");
  });

  it("auto-creates a session when a welcome card is clicked", async () => {
    render(<ChatPanel authToken="t" wsState="open" />);
    // Open the WS so the send() can dispatch.
    const ws = StubWebSocket.instances[0];
    act(() => ws.open());
    // Click a conversation card.
    fireEvent.click(screen.getByTestId("welcome-card-run-an-eval"));
    // A session should be created.
    await waitFor(() => {
      const postCalls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
        (c: unknown[]) => (c[1] as RequestInit | undefined)?.method === "POST" && c[0] === "/api/sessions",
      );
      expect(postCalls.length).toBeGreaterThan(0);
    });
  });

  it("clicking a card twice in quick succession does not create two sessions (single-flight)", async () => {
    render(<ChatPanel authToken="t" wsState="open" />);
    const ws = StubWebSocket.instances[0];
    act(() => ws.open());
    // Click two conversation cards before the first POST resolves.
    fireEvent.click(screen.getByTestId("welcome-card-run-an-eval"));
    fireEvent.click(screen.getByTestId("welcome-card-configure-a-model"));
    await waitFor(() => {
      const postCalls = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls.filter(
        (c: unknown[]) => (c[0] === "/api/sessions" && (c[1] as RequestInit | undefined)?.method === "POST"),
      );
      // Exactly one POST /api/sessions total — the second click
      // reuses the in-flight promise.
      expect(postCalls.length).toBe(1);
    });
  });
});

// v1.5.1.4 (audit hotfix #4): the chat header renders an
// inline model picker even when there is no active session.
// The picker is wired to `dhc.defaultModelId` in
// localStorage so new sessions pre-select the user's pick.
describe("<ChatPanel /> v1.5.1.4: inline default model picker", () => {
  beforeEach(() => {
    // v1.5.1.4: clear the localStorage key so each test
    // starts from a known default ("mock-llm/default").
    try {
      localStorage.removeItem("dhc.defaultModelId");
    } catch { /* ignore */ }
  });

  it("renders the default-model pill in the no-session state", () => {
    render(<ChatPanel authToken="t" wsState="open" />);
    // The pill is the data-testid added in v1.5.1.4.
    const pill = screen.getByTestId("chat-model-pill-default");
    expect(pill).toBeTruthy();
    // The picker shows the default model label.
    expect(pill.textContent).toMatch(/Default model/);
    // The model select component is a child of the pill.
    expect(pill.querySelector("select")).toBeTruthy();
  });
});
