import { useMemo } from "react";

export type TokenCounterProps = {
  /** Total tokens used in this session so far. */
  totalTokens: number;
  /** The model context window. Used to compute the bar fill ratio. */
  contextWindow: number;
};

/** Format a token count with thousands separators. */
function formatTokens(n: number): string {
  return n.toLocaleString("en-US");
}

/** Compute the bar fill percentage, clamped to [0, 100]. */
function fillPercent(totalTokens: number, contextWindow: number): number {
  if (contextWindow <= 0) return 0;
  const ratio = totalTokens / contextWindow;
  if (ratio < 0) return 0;
  if (ratio > 1) return 100;
  return ratio * 100;
}

/** Pick a bar color from green → yellow → red based on the ratio. */
function barColor(percent: number): string {
  if (percent < 60) return "var(--dhc-tokens-low, #2e8b57)"; // green
  if (percent < 90) return "var(--dhc-tokens-mid, #d4a017)"; // amber
  return "var(--dhc-tokens-high, #c0392b)"; // red
}

export function TokenCounter(props: TokenCounterProps) {
  const { totalTokens, contextWindow } = props;
  const pct = useMemo(() => fillPercent(totalTokens, contextWindow), [totalTokens, contextWindow]);
  const color = useMemo(() => barColor(pct), [pct]);
  return (
    <div className="token-counter" data-testid="token-counter">
      <div
        className="token-counter-bar"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Context window usage"
        data-testid="token-counter-bar"
        data-percent={pct.toFixed(1)}
      >
        <div
          className="token-counter-bar-fill"
          style={{ width: `${pct}%`, background: color }}
          data-testid="token-counter-bar-fill"
        />
      </div>
      <div className="token-counter-text" data-testid="token-counter-text">
        Tokens: {formatTokens(totalTokens)} / {formatTokens(contextWindow)}
      </div>
    </div>
  );
}

export { formatTokens, fillPercent, barColor };
