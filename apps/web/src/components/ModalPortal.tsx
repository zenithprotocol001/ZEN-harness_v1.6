import React, { useEffect, useRef } from "react";
import { createPortal } from "react-dom";

/**
 * <ModalPortal /> (v1.5.1, P0 fix): portals its children to
 * document.body, with Escape-to-close, simple focus trap, and
 * focus return.
 *
 * Why this exists: SettingsModal/ModelConfigMenu used to render
 * inside ChatPanel's JSX tree. The chat's own footer/input card
 * sat visually behind/below the modal, and the modal looked like
 * it was eating the app chrome. By portaling to body and pinning
 * position:fixed; inset:0, the modal escapes the chat column and
 * the top brand/tabs in App.tsx stay visible underneath.
 *
 * Behavior:
 *   - Renders nothing if `open` is false (so callers can keep
 *     mounting it unconditionally).
 *   - On open: snapshots `document.activeElement` and restores
 *     focus to it on close.
 *   - On Escape: calls `onClose()`.
 *   - On click outside the inner panel: calls `onClose()`.
 *   - Simple focus trap: Tab cycles within the panel; the first
 *     focusable child is focused on mount. (We use the native
 *     tab order; the trap is achieved by limiting focusable
 *     descendants to elements inside the panel.)
 *   - Locks body scroll while open.
 *
 * Note: callers must render the actual overlay (backdrop + panel)
 * inside <ModalPortal>. The portal only handles the body mount,
 * focus, escape, and outside-click.
 */
export function ModalPortal(props: {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  /** data-testid on the inner panel (the click-outside target). */
  panelTestId?: string;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const lastFocusRef = useRef<HTMLElement | null>(null);

  // Mount: snapshot focus, focus the first focusable inside the panel.
  useEffect(() => {
    if (!props.open) return;
    lastFocusRef.current = (document.activeElement as HTMLElement | null) ?? null;

    // Lock body scroll.
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // Focus first focusable inside the panel after the portal mounts.
    const t = window.setTimeout(() => {
      const panel = panelRef.current;
      if (!panel) return;
      const focusables = panel.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      );
      const first = focusables[0];
      if (first) first.focus();
    }, 0);

    return () => {
      document.body.style.overflow = prevOverflow;
      window.clearTimeout(t);
      // Restore focus to the element that opened the modal.
      const last = lastFocusRef.current;
      if (last && document.contains(last)) {
        try {
          last.focus();
        } catch {
          /* ignore */
        }
      }
    };
  }, [props.open]);

  // Escape-to-close + simple focus trap.
  useEffect(() => {
    if (!props.open) return;
    function onKey(ev: KeyboardEvent) {
      if (ev.key === "Escape") {
        ev.stopPropagation();
        props.onClose();
        return;
      }
      if (ev.key === "Tab") {
        const panel = panelRef.current;
        if (!panel) return;
        const focusables = Array.from(
          panel.querySelectorAll<HTMLElement>(
            'button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
          ),
        );
        if (focusables.length === 0) {
          ev.preventDefault();
          return;
        }
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        const active = document.activeElement as HTMLElement | null;
        if (ev.shiftKey && active === first) {
          ev.preventDefault();
          last.focus();
        } else if (!ev.shiftKey && active === last) {
          ev.preventDefault();
          first.focus();
        }
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [props.open, props.onClose]);

  if (!props.open) return null;
  if (typeof document === "undefined") return null;

  return createPortal(
    <div
      className="modal-portal-overlay"
      onMouseDown={(ev) => {
        // Click on the overlay (not the panel) closes the modal.
        // Use mousedown to beat any input blur/cancel that would
        // otherwise lose focus state.
        if (ev.target === ev.currentTarget) {
          props.onClose();
        }
      }}
    >
      <div
        ref={panelRef}
        className="modal-portal-panel"
        data-testid={props.panelTestId}
        role="dialog"
        aria-modal="true"
        onMouseDown={(ev) => ev.stopPropagation()}
      >
        {props.children}
      </div>
    </div>,
    document.body,
  );
}
