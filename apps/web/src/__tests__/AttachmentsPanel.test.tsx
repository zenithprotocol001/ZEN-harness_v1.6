import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { AttachmentsPanel, isMimeAllowed, isMimeInline, MIME_ALLOWLIST } from "../components/AttachmentsPanel";

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

describe("<AttachmentsPanel />", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("rejects disallowed MIME client-side", async () => {
    const file = new File([new Uint8Array([1, 2, 3])], "evil.html", { type: "text/html" });
    render(<AttachmentsPanel sessionId="s_1" />);
    const input = screen.getByTestId("attachments-file-input") as HTMLInputElement;
    // Use the underlying input.files to bypass DataTransfer unavailability.
    Object.defineProperty(input, "files", { value: [file] });
    fireEvent.change(input);
    await waitFor(() => {
      const err = screen.getByTestId("attachments-error");
      expect(err.textContent).toMatch(/Disallowed MIME/);
    });
  });

  it("rejects inline-only MIME (text/plain goes inline, not through the panel)", async () => {
    const file = new File(["x"], "note.txt", { type: "text/plain" });
    render(<AttachmentsPanel sessionId="s_1" />);
    const input = screen.getByTestId("attachments-file-input") as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [file] });
    fireEvent.change(input);
    await waitFor(() => {
      const err = screen.getByTestId("attachments-error");
      expect(err.textContent).toMatch(/goes inline/);
    });
  });

  it("uploads an out-of-line image and renders a preview", async () => {
    const fetchMock = makeFetch({
      "POST /api/sessions/s_1/attachments": () =>
        jsonResponse(
          {
            ref: "attachment:s_1:00000000000000000000000000000000",
            mime: "image/png",
            sha256: "abc",
            size: 3,
          },
          201
        ),
    });
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const file = new File([new Uint8Array([1, 2, 3])], "p.png", { type: "image/png" });
    render(<AttachmentsPanel sessionId="s_1" />);
    const input = screen.getByTestId("attachments-file-input") as HTMLInputElement;
    Object.defineProperty(input, "files", { value: [file] });
    fireEvent.change(input);
    await waitFor(() => {
      expect(screen.getByTestId("attachments-preview")).toBeTruthy();
    });
    expect(screen.getByTestId("attachments-preview-img")).toBeTruthy();
    const ref = screen.getByTestId("attachments-preview").textContent ?? "";
    expect(ref).toContain("attachment:s_1:");
  });
});

describe("MIME allowlist helpers", () => {
  it("isMimeAllowed agrees with the allowlist", () => {
    for (const m of MIME_ALLOWLIST) {
      expect(isMimeAllowed(m)).toBe(true);
    }
    expect(isMimeAllowed("text/html")).toBe(false);
    expect(isMimeAllowed("application/zip")).toBe(false);
  });
  it("isMimeInline is true for text-like MIME only", () => {
    expect(isMimeInline("text/plain")).toBe(true);
    expect(isMimeInline("text/markdown")).toBe(true);
    expect(isMimeInline("image/svg+xml")).toBe(true);
    expect(isMimeInline("image/png")).toBe(false);
    expect(isMimeInline("application/pdf")).toBe(false);
  });
});
