import React from "react";

/**
 * <Icon /> — single inline-SVG icon set used across the whole app.
 * Replaces emoji glyphs (which render inconsistently per OS) with
 * theme-aware, currentColor-driven SVGs.
 *
 * All icons are 24x24 viewBox, 1.5px stroke, line-cap "round",
 * line-join "round" (Lucide-style). Color inherits from CSS
 * `color` (currentColor) so themes and hover states Just Work.
 *
 * Names are stable identifiers; size is in pixels and defaults to 16.
 */

export type IconName =
  | "plus"
  | "search"
  | "pin"
  | "archive"
  | "trash"
  | "gear"
  | "key"
  | "model"
  | "tree"
  | "fork"
  | "paperPlane"
  | "attach"
  | "chevronDown"
  | "chevronRight"
  | "eye"
  | "eyeOff"
  | "close"
  | "stop"
  | "arrowDown"
  | "play"
  | "logo"
  | "cancelled"
  | "running";

const PATHS: Record<IconName, React.ReactNode> = {
  plus: (
    <>
      <line x1="12" y1="5" x2="12" y2="19" />
      <line x1="5" y1="12" x2="19" y2="12" />
    </>
  ),
  search: (
    <>
      <circle cx="11" cy="11" r="7" />
      <line x1="21" y1="21" x2="16.5" y2="16.5" />
    </>
  ),
  pin: (
    <>
      <path d="M12 2L9 9H4l4 4-2 9 6-4 6 4-2-9 4-4h-5l-3-7z" />
    </>
  ),
  archive: (
    <>
      <rect x="3" y="4" width="18" height="4" rx="1" />
      <path d="M5 8v11a1 1 0 001 1h12a1 1 0 001-1V8" />
      <line x1="10" y1="12" x2="14" y2="12" />
    </>
  ),
  trash: (
    <>
      <polyline points="4 7 20 7" />
      <path d="M9 7V4a1 1 0 011-1h4a1 1 0 011 1v3" />
      <path d="M6 7l1 13a1 1 0 001 1h8a1 1 0 001-1l1-13" />
    </>
  ),
  gear: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.7 1.7 0 00.4 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.4 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.4l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.4-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.4-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.4H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.4l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.4 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="15" r="4" />
      <line x1="10.5" y1="12.5" x2="20" y2="3" />
      <line x1="17" y1="6" x2="20" y2="9" />
      <line x1="14" y1="9" x2="17" y2="12" />
    </>
  ),
  model: (
    <>
      <path d="M12 2L2 7l10 5 10-5-10-5z" />
      <path d="M2 17l10 5 10-5" />
      <path d="M2 12l10 5 10-5" />
    </>
  ),
  tree: (
    <>
      <circle cx="12" cy="5" r="2" />
      <circle cx="5" cy="19" r="2" />
      <circle cx="19" cy="19" r="2" />
      <path d="M12 7v4M12 11l-7 6M12 11l7 6" />
    </>
  ),
  fork: (
    <>
      <circle cx="6" cy="5" r="2" />
      <circle cx="6" cy="19" r="2" />
      <circle cx="18" cy="12" r="2" />
      <path d="M6 7v10" />
      <path d="M6 12h6a6 6 0 016 6" />
    </>
  ),
  paperPlane: (
    <>
      <path d="M22 2L2 11l8 3 3 8 9-20z" />
    </>
  ),
  attach: (
    <>
      <path d="M21.4 11l-9.2 9.2a5 5 0 11-7-7l9.2-9.2a3.5 3.5 0 014.9 4.9L9.4 18.8a2 2 0 11-2.8-2.8l8.5-8.5" />
    </>
  ),
  chevronDown: <polyline points="6 9 12 15 18 9" />,
  chevronRight: <polyline points="9 6 15 12 9 18" />,
  eye: (
    <>
      <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  eyeOff: (
    <>
      <path d="M3 3l18 18" />
      <path d="M10.6 6.1A10.7 10.7 0 0112 6c6.5 0 10 6 10 6a17 17 0 01-3.4 4.1" />
      <path d="M6.6 6.6A17 17 0 002 12s3.5 6 10 6a10.7 10.7 0 005.4-1.4" />
    </>
  ),
  close: (
    <>
      <line x1="6" y1="6" x2="18" y2="18" />
      <line x1="18" y1="6" x2="6" y2="18" />
    </>
  ),
  stop: <rect x="6" y="6" width="12" height="12" rx="1.5" />,
  arrowDown: (
    <>
      <line x1="12" y1="5" x2="12" y2="19" />
      <polyline points="5 12 12 19 19 12" />
    </>
  ),
  play: <polygon points="6 4 20 12 6 20 6 4" />,
  logo: (
    <>
      <rect x="3" y="3" width="18" height="18" rx="4" />
      <path d="M8 12h8M12 8v8" />
    </>
  ),
  cancelled: (
    <>
      <circle cx="12" cy="12" r="9" />
      <line x1="9" y1="9" x2="15" y2="15" />
      <line x1="15" y1="9" x2="9" y2="15" />
    </>
  ),
  running: (
    <>
      <circle cx="12" cy="12" r="9" />
      <polyline points="12 7 12 12 15 14" />
    </>
  ),
};

export function Icon(props: { name: IconName; size?: number; title?: string }) {
  const size = props.size ?? 16;
  return (
    <svg
      xmlns="http://www.w3.org/2000/svg"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden={props.title ? undefined : true}
      role={props.title ? "img" : undefined}
      aria-label={props.title}
      data-icon={props.name}
    >
      {PATHS[props.name]}
    </svg>
  );
}
