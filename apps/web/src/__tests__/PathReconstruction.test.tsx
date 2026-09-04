import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { PathReconstruction } from "../components/PathReconstruction";

function makeFetch(handlers: Record<string, (init?: RequestInit) => Response>) {
  return vi.fn(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const h = handlers[key];
    if (!h) throw new Error(`unexpected fetch: ${key}`);
    return h(init);
  });
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("<PathReconstruction />", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the active branch's path from the API response", async () => {
    globalThis.fetch = makeFetch({
      "GET /api/sessions/s_1/branches": () =>
        jsonResponse({
          branches: [
            {
              id: "m_tip1",
              active: true,
              path: [
                { id: "m_a", role: "user", preview: "hello" },
                { id: "m_b", role: "assistant", preview: "world" },
                { id: "m_tip1", role: "user", preview: "followup" },
              ],
            },
            {
              id: "m_tip2",
              active: false,
              path: [
                { id: "m_a", role: "user", preview: "hello" },
                { id: "m_tip2", role: "user", preview: "alt" },
              ],
            },
          ],
        }),
    }) as unknown as typeof fetch;
    render(<PathReconstruction sessionId="s_1" />);
    await waitFor(() => {
      expect(screen.getByTestId("path-reconstruction")).toBeTruthy();
    });
    expect(screen.getByTestId("path-row-m_a")).toBeTruthy();
    expect(screen.getByTestId("path-row-m_b")).toBeTruthy();
    expect(screen.getByTestId("path-row-m_tip1")).toBeTruthy();
  });
});
