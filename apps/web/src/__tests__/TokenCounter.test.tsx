import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { TokenCounter, formatTokens, fillPercent, barColor } from "../components/TokenCounter";

describe("<TokenCounter />", () => {
  it("renders 'N / M' formatted with thousands separator", () => {
    render(<TokenCounter totalTokens={1234} contextWindow={8192} />);
    const text = screen.getByTestId("token-counter-text");
    expect(text).toHaveTextContent("Tokens: 1,234 / 8,192");
  });

  it("progress bar width matches total_tokens / context_window", () => {
    const { container } = render(<TokenCounter totalTokens={4096} contextWindow={8192} />);
    const fill = screen.getByTestId("token-counter-bar-fill") as HTMLElement;
    // 50% width.
    expect(fill.style.width).toBe("50%");
    // data-percent is 50.0
    expect(container.querySelector('[data-testid="token-counter-bar"]')?.getAttribute("data-percent")).toBe(
      "50.0",
    );
  });

  it("color shifts to red when total_tokens / context_window > 0.9", () => {
    render(<TokenCounter totalTokens={7500} contextWindow={8192} />);
    // 7500 / 8192 ≈ 0.9154 → > 0.9 → red.
    const fill = screen.getByTestId("token-counter-bar-fill") as HTMLElement;
    expect(fill.style.background).toContain("c0392b");
  });

  it("clamps to 100% when total_tokens > context_window", () => {
    render(<TokenCounter totalTokens={99999} contextWindow={8192} />);
    const fill = screen.getByTestId("token-counter-bar-fill") as HTMLElement;
    expect(fill.style.width).toBe("100%");
  });

  it("renders 0% when context_window is 0 (defensive)", () => {
    render(<TokenCounter totalTokens={500} contextWindow={0} />);
    const fill = screen.getByTestId("token-counter-bar-fill") as HTMLElement;
    expect(fill.style.width).toBe("0%");
  });
});

describe("TokenCounter helpers", () => {
  it("formatTokens adds thousands separators", () => {
    expect(formatTokens(0)).toBe("0");
    expect(formatTokens(1000)).toBe("1,000");
    expect(formatTokens(1234567)).toBe("1,234,567");
  });
  it("fillPercent is the ratio × 100, clamped", () => {
    expect(fillPercent(50, 100)).toBe(50);
    expect(fillPercent(150, 100)).toBe(100);
    expect(fillPercent(0, 100)).toBe(0);
  });
  it("barColor picks green / amber / red by threshold", () => {
    expect(barColor(30)).toMatch(/green|2e8b57/);
    expect(barColor(75)).toMatch(/amber|d4a017/);
    expect(barColor(95)).toMatch(/red|c0392b/);
  });
});
