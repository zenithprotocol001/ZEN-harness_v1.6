import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MessageBubble } from "../components/MessageBubble";
import type { Message } from "../types/chat";

function msg(over: Partial<Message> = {}): Message {
  return {
    id: "m_test",
    role: "user",
    content: "hi",
    ts_ms: 0,
    ...over,
  };
}

describe("<MessageBubble /> (v1.5.1: developerMode gating)", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does NOT show the Branch button when developerMode is false (default)", () => {
    const { container } = render(
      <MessageBubble
        message={msg()}
        activeId="s1"
        onForked={() => {}}
      />,
    );
    // BranchButton renders a button with title "Branch from this message"
    // (or testid — we look for any button inside chat-bubble-developer).
    const devSection = container.querySelector(".chat-bubble-developer");
    expect(devSection).toBeNull();
  });

  it("shows the Branch button when developerMode is true", () => {
    const { container } = render(
      <MessageBubble
        message={msg({ role: "assistant" })}
        activeId="s1"
        onForked={() => {}}
        developerMode={true}
      />,
    );
    const devSection = container.querySelector(".chat-bubble-developer");
    expect(devSection).toBeTruthy();
  });

  it("renders the cancelled chip when message.cancelled is true", () => {
    render(
      <MessageBubble
        message={msg({ role: "assistant", cancelled: true })}
        activeId="s1"
        onForked={() => {}}
      />,
    );
    expect(screen.getByTestId("chat-bubble-cancelled")).toBeTruthy();
  });

  it("does NOT render the cancelled chip by default", () => {
    const { container } = render(
      <MessageBubble
        message={msg({ role: "assistant" })}
        activeId="s1"
        onForked={() => {}}
      />,
    );
    expect(container.querySelector('[data-testid="chat-bubble-cancelled"]')).toBeNull();
  });
});
