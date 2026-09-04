import { Icon, type IconName } from "./icons";
import { SuggestionCard } from "./SuggestionCard";

export type WelcomeCard =
  | {
      slug: string;
      title: string;
      description: string;
      kind: "conversation";
      icon: IconName;
    }
  | {
      slug: string;
      title: string;
      description: string;
      kind: "action";
      action: "open-session-list" | "manage-api-keys";
      icon: IconName;
    };

/**
 * <WelcomeView /> (v1.5.1): the "Zero State" / "Welcome Screen"
 * shown when no session is active OR when the active session is
 * empty.
 *
 * Two flavors of cards (v1.5.1):
 *   - conversation: dispatches a prompt via `onPickSuggestion`.
 *   - action: performs a UI action via `onAction(action)`.
 *
 * Both render the same <SuggestionCard /> surface (so the grid is
 * visually uniform); the difference is in the verb and the
 * microcopy ("will explain…" vs. "opens…").
 *
 * Copy:
 *   - LLM ok → "Ready"
 *   - LLM off → "Offline"
 *   - Unknown → "Ready to help"
 */

const CONVERSATION_PROMPTS: Record<string, string> = {
  "run-an-eval":
    "Run a quick eval — score a pasted module body against the harness rubric and report any failures.",
  "configure-a-model":
    "Configure the model for this session. What's the difference between temperature and top_p, and which should I tune first for code generation?",
};

export function WelcomeView(props: {
  llmOk: boolean | null;
  cards: WelcomeCard[];
  onPickSuggestion: (text: string) => void;
  onAction: (action: "open-session-list" | "manage-api-keys") => void;
}) {
  let subtitle: string;
  if (props.llmOk === true) subtitle = "Ready";
  else if (props.llmOk === false) subtitle = "Offline";
  else subtitle = "Ready to help";

  return (
    <div className="chat-welcome" data-testid="chat-welcome">
      <div className="chat-welcome-mark" aria-hidden="true">
        DHC
      </div>
      <h1 className="chat-welcome-greeting">How can I help?</h1>
      <p className="chat-welcome-subtitle">{subtitle}</p>
      <div className="chat-welcome-grid" role="list">
        {props.cards.map((c) => {
          if (c.kind === "conversation") {
            const prompt = CONVERSATION_PROMPTS[c.slug] ?? c.title;
            return (
              <SuggestionCard
                key={c.slug}
                icon={<Icon name={c.icon} size={18} />}
                title={c.title}
                description={c.description}
                verb="Ask"
                onClick={() => props.onPickSuggestion(prompt)}
                testId={`welcome-card-${c.slug}`}
              />
            );
          }
          return (
            <SuggestionCard
              key={c.slug}
              icon={<Icon name={c.icon} size={18} />}
              title={c.title}
              description={c.description}
              verb="Open"
              onClick={() => props.onAction(c.action)}
              testId={`welcome-card-${c.slug}`}
            />
          );
        })}
      </div>
    </div>
  );
}
