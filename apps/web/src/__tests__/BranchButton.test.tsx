import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BranchButton } from "../components/BranchButton";

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

describe("<BranchButton />", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    document.head.innerHTML = "";
  });

  it("renders a Fork button on a message", () => {
    render(<BranchButton messageId="m_abc" sessionId="s_1" />);
    const btn = screen.getByTestId("branch-button-m_abc");
    expect(btn).toBeTruthy();
    expect(btn.textContent).toContain("Fork");
  });

  it("dispatches a POST to /branches on click", async () => {
    const fetchMock = makeFetch({
      "POST /api/sessions/s_1/branches": () =>
        jsonResponse({ branch_root: "m_newbranch", active_tip_id: "m_newbranch" }, 201),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    render(<BranchButton messageId="m_abc" sessionId="s_1" />);
    fireEvent.click(screen.getByTestId("branch-button-m_abc"));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/sessions/s_1/branches");
    expect(init?.method).toBe("POST");
    const body = JSON.parse(String(init?.body));
    expect(body.parent_message_id).toBe("m_abc");
    expect(body.role).toBe("user");
  });

  it("invokes onForked with the new branch root", async () => {
    globalThis.fetch = makeFetch({
      "POST /api/sessions/s_1/branches": () =>
        jsonResponse({ branch_root: "m_newbranchroot", active_tip_id: "m_newbranchroot" }, 201),
    }) as unknown as typeof fetch;
    const onForked = vi.fn();
    render(<BranchButton messageId="m_abc" sessionId="s_1" onForked={onForked} />);
    fireEvent.click(screen.getByTestId("branch-button-m_abc"));
    await waitFor(() => {
      expect(onForked).toHaveBeenCalledWith("m_newbranchroot");
    });
  });
});
