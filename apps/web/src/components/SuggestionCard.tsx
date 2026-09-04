/**
 * <SuggestionCard /> — a single card in the welcome grid.
 * Used for both conversation starters (verb="Ask") and action
 * entries (verb="Open"). The same visual surface, two different
 * microcopy verbs, so the grid stays uniform.
 *
 * v1.5.1: the icon is a ReactNode (typically <Icon />) so it can
 * use the shared SVG set; the `verb` is a short label that names
 * what clicking does.
 */
export function SuggestionCard(props: {
  icon: React.ReactNode;
  title: string;
  description: string;
  verb: "Ask" | "Open";
  onClick: () => void;
  testId?: string;
}) {
  return (
    <button
      type="button"
      className="chat-welcome-card"
      onClick={props.onClick}
      data-testid={props.testId}
      role="listitem"
    >
      <span className="chat-welcome-card-icon">{props.icon}</span>
      <span className="chat-welcome-card-verb">{props.verb}</span>
      <span className="chat-welcome-card-title">{props.title}</span>
      <span className="chat-welcome-card-desc">{props.description}</span>
    </button>
  );
}
