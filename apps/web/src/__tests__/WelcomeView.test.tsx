import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { WelcomeView, type WelcomeCard } from "../components/WelcomeView";

const TEST_CARDS: WelcomeCard[] = [
  {
    slug: "run-an-eval",
    title: "Run an eval",
    description: "Score a pasted module body against the harness rubric.",
    kind: "conversation",
    icon: "play",
  },
  {
    slug: "open-a-session",
    title: "Open a session",
    description: "Focuses the session list so you can pick a recent chat.",
    kind: "action",
    action: "open-session-list",
    icon: "model",
  },
  {
    slug: "configure-a-model",
    title: "Configure a model",
    description: "Per-session temperature, top_p, max tokens.",
    kind: "conversation",
    icon: "model",
  },
  {
    slug: "manage-api-keys",
    title: "Manage API keys",
    description: "Opens Settings → API Keys.",
    kind: "action",
    action: "manage-api-keys",
    icon: "key",
  },
];

describe("<WelcomeView /> (v1.5.1 redesign)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the greeting and 4 suggestion cards", () => {
    render(
      <WelcomeView
        llmOk={true}
        cards={TEST_CARDS}
        onPickSuggestion={() => {}}
        onAction={() => {}}
      />,
    );
    expect(screen.getByTestId("chat-welcome")).toBeTruthy();
    expect(screen.getByText(/how can i help\?/i)).toBeTruthy();
    expect(screen.getByTestId("welcome-card-run-an-eval")).toBeTruthy();
    expect(screen.getByTestId("welcome-card-open-a-session")).toBeTruthy();
    expect(screen.getByTestId("welcome-card-configure-a-model")).toBeTruthy();
    expect(screen.getByTestId("welcome-card-manage-api-keys")).toBeTruthy();
  });

  it("shows the 'Ready' subtitle when the LLM is connected", () => {
    render(
      <WelcomeView
        llmOk={true}
        cards={TEST_CARDS}
        onPickSuggestion={() => {}}
        onAction={() => {}}
      />,
    );
    expect(screen.getByText(/^Ready$/)).toBeTruthy();
  });

  it("shows the 'Offline' subtitle when the LLM is offline", () => {
    render(
      <WelcomeView
        llmOk={false}
        cards={TEST_CARDS}
        onPickSuggestion={() => {}}
        onAction={() => {}}
      />,
    );
    expect(screen.getByText(/^Offline$/)).toBeTruthy();
  });

  it("falls back to 'Ready to help' when the LLM health is unknown", () => {
    render(
      <WelcomeView
        llmOk={null}
        cards={TEST_CARDS}
        onPickSuggestion={() => {}}
        onAction={() => {}}
      />,
    );
    expect(screen.getByText(/Ready to help/)).toBeTruthy();
  });

  it("calls onPickSuggestion with non-empty text when a conversation card is clicked", () => {
    const onPick = vi.fn();
    render(
      <WelcomeView
        llmOk={true}
        cards={TEST_CARDS}
        onPickSuggestion={onPick}
        onAction={() => {}}
      />,
    );
    fireEvent.click(screen.getByTestId("welcome-card-run-an-eval"));
    expect(onPick).toHaveBeenCalledTimes(1);
    const text = onPick.mock.calls[0][0];
    expect(typeof text).toBe("string");
    expect(text.length).toBeGreaterThan(10);
  });

  it("calls onAction (not onPickSuggestion) when an action card is clicked", () => {
    const onPick = vi.fn();
    const onAction = vi.fn();
    render(
      <WelcomeView
        llmOk={true}
        cards={TEST_CARDS}
        onPickSuggestion={onPick}
        onAction={onAction}
      />,
    );
    fireEvent.click(screen.getByTestId("welcome-card-manage-api-keys"));
    expect(onPick).not.toHaveBeenCalled();
    expect(onAction).toHaveBeenCalledWith("manage-api-keys");
  });
});

describe("<SuggestionCard />", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders icon, title, description, verb, and calls onClick on click", () => {
    const onClick = vi.fn();
    render(
      <button
        type="button"
        className="chat-welcome-card"
        onClick={onClick}
        data-testid="test-card"
      >
        <span className="chat-welcome-card-icon">★</span>
        <span className="chat-welcome-card-verb">Ask</span>
        <span className="chat-welcome-card-title">Test card</span>
        <span className="chat-welcome-card-desc">A short description.</span>
      </button>,
    );
    expect(screen.getByText("Test card")).toBeTruthy();
    expect(screen.getByText("A short description.")).toBeTruthy();
    expect(screen.getByText("Ask")).toBeTruthy();
    fireEvent.click(screen.getByText("Test card"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });
});
