import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { LeafSwitcher } from "../components/LeafSwitcher";

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

describe("<LeafSwitcher />", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders one option per branch with the active one marked", () => {
    const branches = [
      { id: "m_tip1", active: false },
      { id: "m_tip2", active: true },
    ];
    render(<LeafSwitcher sessionId="s_1" branches={branches} />);
    const select = screen.getByTestId("leaf-switcher-select") as HTMLSelectElement;
    expect(select.value).toBe("m_tip2");
    const options = Array.from(select.querySelectorAll("option"));
    expect(options.map((o) => o.textContent)).toEqual([
      "m_tip1",
      "m_tip2 (active)",
    ]);
  });

  it("dispatches a PATCH to /active-tip on change", async () => {
    const fetchMock = makeFetch({
      "PATCH /api/sessions/s_1/active-tip": () => jsonResponse({ active_tip_id: "m_tip1" }),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const branches = [
      { id: "m_tip1", active: false },
      { id: "m_tip2", active: true },
    ];
    const onSwitched = vi.fn();
    render(
      <LeafSwitcher sessionId="s_1" branches={branches} onSwitched={onSwitched} />
    );
    const select = screen.getByTestId("leaf-switcher-select") as HTMLSelectElement;
    fireEvent.change(select, { target: { value: "m_tip1" } });
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/sessions/s_1/active-tip");
    expect(init?.method).toBe("PATCH");
    const body = JSON.parse(String(init?.body));
    expect(body.leaf_message_id).toBe("m_tip1");
    expect(onSwitched).toHaveBeenCalledWith("m_tip1");
  });
});
