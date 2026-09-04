/**
 * sanitizeErrorMessage (v1.5.1.2 audit hotfix).
 *
 * Maps raw `fetch` failures and HTTP 5xx bodies to friendly text
 * that does NOT echo the server's raw error body. The previous
 * `fetchJson` implementation inlined the raw aiohttp response
 * text into the thrown Error, which:
 *
 *   1. Surfaces internal details to the user (e.g. a future
 *      `PermissionError: [Errno 13] ...` could leak a file
 *      path or mode bits).
 *   2. Wastes the user's attention on a useless "500: 500
 *      Internal Server Error\nServer got itself in trouble"
 *      string when the real cause is in the server log.
 *
 * The shape is intentionally narrow: a handful of friendly
 * messages, no echo of the raw body. The `cause` parameter is
 * an `unknown` for tests/devtools but is NOT rendered.
 */
export function sanitizeErrorMessage(
  status: number,
  cause?: unknown,
): string {
  // We deliberately ignore `cause` so the raw body never makes
  // it into the user-visible string. Tests assert that
  // `cause` is NOT included in the rendered error.
  void cause;
  if (status === 401 || status === 403) {
    return "Not authorized. Check the bearer token or restart the server.";
  }
  if (status === 404) {
    return "Not found.";
  }
  if (status === 409) {
    return "Conflict — try again.";
  }
  if (status === 429) {
    return "Too many requests. Slow down and try again.";
  }
  if (status >= 500) {
    return "Server is busy. Try again or restart the server.";
  }
  if (status >= 400) {
    return `Request failed (HTTP ${status}).`;
  }
  return "Network error.";
}

/**
 * fetchJson (v1.5.1.2 audit hotfix).
 *
 * Drop-in replacement for the previous `fetchJson` helper. The
 * thrown Error message is the sanitized form, never the raw
 * server body. The raw body is still returned to the caller
 * (via the `cause` argument) for tests and devtools, but it
 * is not interpolated into the user-visible string.
 */
export async function fetchJson(
  path: string,
  init?: RequestInit,
): Promise<unknown> {
  const resp = await fetch(path, { credentials: "same-origin", ...init });
  if (!resp.ok) {
    throw new Error(sanitizeErrorMessage(resp.status));
  }
  if (resp.status === 204) return null;
  return resp.json();
}
