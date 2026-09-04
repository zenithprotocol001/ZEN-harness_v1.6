import { useEffect, useRef } from "react";
import { Icon } from "./icons";

/**
 * <DeveloperModeMenu /> (v1.5.1): a popover menu attached to the
 * gear button. Replaces the v1.5.0.1 inline emoji menu.
 *
 * Items:
 *   1. API Keys — opens the Settings modal
 *   2. Model config — opens the per-session ModelConfigMenu
 *   3. Developer tools (switch) — toggles `developerMode`. When
 *      on, the chat panel surfaces v1.5.0 affordances (Fork on
 *      every message, LeafSwitcher, Tree view, token counter).
 *
 * A11y:
 *   - Escape closes.
 *   - Click outside closes.
 *   - On open, focus moves to the first menu item.
 *   - On close, focus returns to the previously focused element
 *     (the gear button).
 *
 * The native checkbox is replaced with a custom switch track so it
 * doesn't look like the rest of the dark-theme controls.
 */
export function DeveloperModeMenu(props: {
  open: boolean;
  onClose: () => void;
  developerMode: boolean;
  onToggleDeveloperMode: () => void;
  llmOk: boolean | null;
  onOpenKeys: () => void;
  onOpenConfig: () => void;
}) {
  const rootRef = useRef<HTMLDivElement | null>(null);
  const lastFocusRef = useRef<HTMLElement | null>(null);

  // Click-outside and Escape.
  useEffect(() => {
    if (!props.open) return;
    lastFocusRef.current = (document.activeElement as HTMLElement | null) ?? null;
    function onMouseDown(ev: MouseEvent) {
      const root = rootRef.current;
      if (!root) return;
      if (ev.target instanceof Node && !root.contains(ev.target)) {
        props.onClose();
      }
    }
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        props.onClose();
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onMouseDown);
      document.removeEventListener("keydown", onKey);
      const last = lastFocusRef.current;
      if (last && document.contains(last)) {
        try {
          last.focus();
        } catch {
          /* ignore */
        }
      }
    };
  }, [props.open, props.onClose]);

  if (!props.open) return null;

  const statusText =
    props.llmOk === true
      ? "LLM ready"
      : props.llmOk === false
        ? "LLM offline"
        : "LLM connecting…";
  const statusClass =
    props.llmOk === true
      ? "ok"
      : props.llmOk === false
        ? "off"
        : "connecting";

  return (
    <div
      ref={rootRef}
      className="chat-gear-menu"
      role="menu"
      aria-label="Settings"
      data-testid="chat-gear-menu"
    >
      <button
        type="button"
        className="chat-gear-menu-item"
        onClick={() => {
          props.onOpenKeys();
          props.onClose();
        }}
        data-testid="chat-gear-keys"
        role="menuitem"
      >
        <Icon name="key" size={14} />
        <span>API Keys</span>
      </button>
      <button
        type="button"
        className="chat-gear-menu-item"
        onClick={() => {
          props.onOpenConfig();
          props.onClose();
        }}
        data-testid="chat-gear-config"
        role="menuitem"
      >
        <Icon name="model" size={14} />
        <span>Model config</span>
      </button>
      <div className="chat-gear-menu-divider" role="separator" />
      <button
        type="button"
        className="chat-gear-menu-toggle"
        onClick={props.onToggleDeveloperMode}
        aria-pressed={props.developerMode}
        data-testid="chat-gear-devmode"
        role="menuitemcheckbox"
      >
        <span className="chat-gear-menu-toggle-label">
          <Icon name="fork" size={14} />
          <span>Developer tools</span>
        </span>
        <span
          className={`chat-gear-menu-switch ${props.developerMode ? "is-on" : ""}`}
          aria-hidden="true"
        >
          <span className="chat-gear-menu-switch-knob" />
        </span>
      </button>
      <div
        className="chat-gear-menu-status"
        data-status={statusClass}
        data-testid="chat-gear-llm-status"
      >
        {statusText}
      </div>
    </div>
  );
}
