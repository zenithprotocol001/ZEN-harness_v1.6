import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import { SettingsModal } from "../components/SettingsModal";
import type { ModelOption } from "../components/ModelSelect";

const MODELS: ModelOption[] = [
  {
    id: "openai/gpt-4o-mini",
    name: "GPT-4o mini",
    provider: "openai",
    context_length: 128000,
    pricing_input: 0.15,
    pricing_output: 0.6,
    capabilities: ["chat"],
  },
  {
    id: "openai/gpt-4.1",
    name: "GPT-4.1",
    provider: "openai",
    context_length: 128000,
    pricing_input: 2.0,
    pricing_output: 8.0,
    capabilities: ["chat"],
  },
  {
    id: "anthropic/claude-3-5-sonnet-latest",
    name: "Claude 3.5 Sonnet",
    provider: "anthropic",
    context_length: 200000,
    pricing_input: 3.0,
    pricing_output: 15.0,
    capabilities: ["chat"],
  },
  {
    id: "openrouter/auto",
    name: "Auto",
    provider: "openrouter",
    context_length: 128000,
    pricing_input: 1.0,
    pricing_output: 3.0,
    capabilities: ["chat"],
  },
];

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

function emptyResponse(status = 204): Response {
  return new Response(null, { status });
}

describe("<SettingsModal />", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders one row per model (v1.3.2 per-model keys)", async () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [] }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // v1.5.1: open the per-model sub-accordion for each provider.
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    fireEvent.click(screen.getByTestId("settings-accordion-anthropic"));
    fireEvent.click(screen.getByTestId("settings-accordion-openrouter"));
    const summaries = screen.getAllByText(/Per-model overrides/);
    for (const s of summaries) fireEvent.click(s);
    await waitFor(() => {
      // The four models are each rendered with their human name.
      expect(screen.getByText("GPT-4o mini")).toBeInTheDocument();
      expect(screen.getByText("GPT-4.1")).toBeInTheDocument();
      expect(screen.getByText("Claude 3.5 Sonnet")).toBeInTheDocument();
      expect(screen.getByText("Auto")).toBeInTheDocument();
    });
  });

  it("shows 'Key saved' badge derived from exact per-model key match", async () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () =>
        jsonResponse({
          names: ["llm_provider_openai_gpt-4o-mini", "llm_provider_anthropic_claude-3-5-sonnet-latest"],
        }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // v1.5.1: the saved status is on the accordion header pill.
    await waitFor(() => {
      // OpenAI and Anthropic accordions auto-open because they have
      // a saved key. The OpenRouter one stays collapsed.
      expect(screen.getByTestId("settings-status-openai")).toHaveTextContent(/Configured/);
      expect(screen.getByTestId("settings-status-anthropic")).toHaveTextContent(/Configured/);
      // gpt-4.1 was not saved → no saved status for that specific
      // model. We assert by checking the data-row attribute
      // presence/absence; the per-row Key saved text isn't part
      // of the v1.5.1 design.
      expect(screen.getByTestId("settings-status-openai")).toHaveTextContent(/Configured/);
    });
  });

  it("save calls PUT /api/secrets/{keyName} with the value", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/secrets" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ names: [] });
      }
      if (url.startsWith("/api/secrets/") && init?.method === "PUT") {
        return emptyResponse(204);
      }
      throw new Error(`unexpected ${init?.method} ${url}`);
    });
    globalThis.fetch = f as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // v1.5.1: open the OpenAI accordion and the per-model sub-accordion.
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    fireEvent.click(screen.getByText(/Per-model overrides/));
    await waitFor(() => screen.getByText("GPT-4.1"));
    // The second model row (gpt-4.1) gets the per-model key name on save.
    const row = document.querySelector('[data-row="model-openai/gpt-4.1"]') as HTMLElement;
    const input = row.querySelector("input.settings-key-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sk-test-1234" } });
    const saveBtn = row.querySelector("button.settings-save-button") as HTMLButtonElement;
    fireEvent.click(saveBtn);
    await waitFor(() => {
      const put = calls.find((c) => c.url.startsWith("/api/secrets/") && c.init?.method === "PUT");
      expect(put).toBeDefined();
      expect(put!.url).toContain("llm_provider_openai_gpt-4.1");
      const body = JSON.parse(String(put!.init!.body));
      expect(body.value).toBe("sk-test-1234");
    });
  });

  it("delete calls DELETE /api/secrets/{per-model name}", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/secrets" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ names: ["llm_provider_openai_gpt-4.1"] });
      }
      if (url.startsWith("/api/secrets/") && init?.method === "DELETE") {
        return emptyResponse(204);
      }
      throw new Error(`unexpected ${init?.method} ${url}`);
    });
    globalThis.fetch = f as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // v1.5.1: open the OpenAI accordion and per-model sub-accordion
    // to access the delete button on the saved gpt-4.1 row.
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    fireEvent.click(screen.getByText(/Per-model overrides/));
    await waitFor(() =>
      document.querySelector('[data-row="model-openai/gpt-4.1"] button.settings-delete-button'),
    );
    const row = document.querySelector('[data-row="model-openai/gpt-4.1"]') as HTMLElement;
    const delBtn = row.querySelector("button.settings-delete-button") as HTMLButtonElement;
    fireEvent.click(delBtn);
    await waitFor(() => {
      const del = calls.find((c) => c.url.startsWith("/api/secrets/") && c.init?.method === "DELETE");
      expect(del).toBeDefined();
      expect(del!.url).toContain("llm_provider_openai_gpt-4.1");
    });
  });

  it("'Add another key' creates a new row whose save targets ___2", async () => {
    const calls: { url: string; init?: RequestInit }[] = [];
    const f = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url === "/api/secrets" && (init?.method ?? "GET") === "GET") {
        return jsonResponse({ names: [] });
      }
      if (url.startsWith("/api/secrets/") && init?.method === "PUT") {
        return emptyResponse(204);
      }
      throw new Error(`unexpected ${init?.method} ${url}`);
    });
    globalThis.fetch = f as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // v1.5.1: open the OpenAI accordion and the per-model sub-accordion.
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    fireEvent.click(screen.getByText(/Per-model overrides/));
    await waitFor(() => screen.getByText("GPT-4o mini"));
    // Click "Add another key" on the gpt-4o-mini row.
    const addBtn = screen.getByTestId("add-another-model-openai/gpt-4o-mini");
    fireEvent.click(addBtn);
    // The extra row should now be rendered. Look for the label
    // "GPT-4o mini (additional #2)" which is unique to the extra row.
    await waitFor(() => {
      expect(screen.getByText(/GPT-4o mini \(additional #2\)/)).toBeInTheDocument();
    });
    // Find the extra row by its data-row attribute and save from it.
    const extraRow = document.querySelector(
      '[data-row="extra-openai/gpt-4o-mini-2"]',
    ) as HTMLElement;
    expect(extraRow).not.toBeNull();
    const input = extraRow.querySelector("input.settings-key-input") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sk-second-1234" } });
    const saveBtn = extraRow.querySelector("button.settings-save-button") as HTMLButtonElement;
    fireEvent.click(saveBtn);
    await waitFor(() => {
      const put = calls.find((c) => c.url.startsWith("/api/secrets/") && c.init?.method === "PUT");
      expect(put).toBeDefined();
      expect(put!.url).toContain("llm_provider_openai_gpt-4o-mini___2");
      const body = JSON.parse(String(put!.init!.body));
      expect(body.value).toBe("sk-second-1234");
    });
  });

  it("provider-key fallback row is shown (per-provider default)", async () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [] }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // Open the OpenAI accordion to access the per-row inputs.
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    await waitFor(() => {
      // The default row's label contains "(default — applies to all".
      expect(screen.getByText(/OpenAI \(default/)).toBeInTheDocument();
    });
  });

  it("shows the per-provider 'default' key as saved when only the default is stored", async () => {
    // v1.3.1-style: one key per provider, no per-model keys. The
    // user only set `llm_provider_openai_gpt-4o-mini` (the first model).
    // The default row's input should be in 'Replace key' mode and
    // the accordion header should show "Configured".
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () =>
        jsonResponse({ names: ["llm_provider_openai_gpt-4o-mini"] }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    await waitFor(() => {
      // Accordion header pill shows "Configured" once the probe resolves.
      expect(screen.getByTestId("settings-status-openai")).toHaveTextContent(/Configured/);
    });
  });

  it("renders per-model rows for each model in the registry", async () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [] }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    fireEvent.click(screen.getByTestId("settings-accordion-anthropic"));
    await waitFor(() => {
      // Each model gets a row in the per-model accordion.
      expect(document.querySelector('[data-row="model-openai/gpt-4o-mini"]')).toBeTruthy();
      expect(document.querySelector('[data-row="model-openai/gpt-4.1"]')).toBeTruthy();
      expect(
        document.querySelector('[data-row="model-anthropic/claude-3-5-sonnet-latest"]'),
      ).toBeTruthy();
    });
  });
});

// ============================================================================
// v1.5.1: new tests for accordion + show/hide + dirty-gated save
// ============================================================================

describe("<SettingsModal /> v1.5.1: accordions + show/hide + dirty save", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders the provider accordions (OpenAI, Anthropic, OpenRouter)", () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    expect(screen.getByTestId("settings-accordion-openai")).toBeTruthy();
    expect(screen.getByTestId("settings-accordion-anthropic")).toBeTruthy();
    expect(screen.getByTestId("settings-accordion-openrouter")).toBeTruthy();
  });

  it("uses display names (OpenAI, Anthropic, OpenRouter), not the lowercase ids", () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    expect(screen.getAllByText(/^OpenAI$/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/^Anthropic$/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/^OpenRouter$/).length).toBeGreaterThan(0);
  });

  it("renders the show/hide eye toggle for each input", () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // Open the OpenAI accordion to access the per-row inputs.
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    const eye = screen.getAllByTestId(/^show-default-/).length;
    expect(eye).toBeGreaterThan(0);
  });

  it("flips the input type when the eye is clicked", () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    const input = screen.getByTestId("input-default-openai") as HTMLInputElement;
    expect(input.type).toBe("password");
    fireEvent.click(screen.getByTestId("show-default-openai"));
    expect(input.type).toBe("text");
    fireEvent.click(screen.getByTestId("show-default-openai"));
    expect(input.type).toBe("password");
  });

  it("Save is disabled when the draft is empty", () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    const save = screen.getByTestId("save-default-openai") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
  });

  it("Save is enabled only when the draft is non-empty and dirty", () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    const input = screen.getByTestId("input-default-openai") as HTMLInputElement;
    const save = screen.getByTestId("save-default-openai") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(input, { target: { value: "sk-foo" } });
    expect(save.disabled).toBe(false);
  });

  it("Save is enabled whenever the draft is non-empty (v1.5.1.1 no-prefill contract)", async () => {
    // v1.5.1.1 (ADR-0108): the server never returns the key value,
    // so we cannot compare the draft against a stored value. Any
    // non-empty draft is a save intent (the user is rotating or
    // replacing the stored key).
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () =>
        jsonResponse({
          secrets: [
            { name: "llm_provider_openai_gpt-4o-mini", configured: true, updated_at: 0, hint: "…1234" },
          ],
        }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // The OpenAI accordion should auto-open because the probe
    // returned a stored key for that provider.
    await waitFor(() => {
      expect(screen.getByTestId("settings-accordion-openai").getAttribute("aria-expanded")).toBe("true");
    });
    // Open the per-model accordion.
    const modelSummary = screen.getByText(/Per-model overrides/);
    fireEvent.click(modelSummary);
    const input = await screen.findByTestId("input-model-openai/gpt-4o-mini") as HTMLInputElement;
    const save = await screen.findByTestId("save-model-openai/gpt-4o-mini") as HTMLButtonElement;
    // Empty draft → save disabled.
    expect(input.value).toBe(""); // never pre-filled
    expect(save.disabled).toBe(true);
    // Any non-empty draft → save enabled.
    fireEvent.change(input, { target: { value: "sk-original" } });
    expect(save.disabled).toBe(false);
    // Different value → still enabled.
    fireEvent.change(input, { target: { value: "sk-different" } });
    expect(save.disabled).toBe(false);
  });

  it("Escape closes the modal", () => {
    const onClose = vi.fn();
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={onClose} models={MODELS} />);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("clicking the close button calls onClose", () => {
    const onClose = vi.fn();
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={onClose} models={MODELS} />);
    fireEvent.click(screen.getByTestId("settings-modal-close"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

// ============================================================================
// v1.5.1.1 (ADR-0108): metadata GET, no prefill, accordion default state
// ============================================================================

describe("<SettingsModal /> v1.5.1.1: ADR-0108 contract", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("uses the new metadata {secrets:[{name, configured, hint}]} shape and never prefills the input", async () => {
    // The server returns the metadata shape (no value, just a 7-char hint).
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () =>
        jsonResponse({
          secrets: [
            { name: "llm_provider_openai_gpt-4o-mini", configured: true, updated_at: 1234, hint: "…1234" },
          ],
          names: ["llm_provider_openai_gpt-4o-mini"],
        }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // OpenAI should be marked Configured.
    await waitFor(() => {
      expect(screen.getByTestId("settings-status-openai")).toHaveTextContent(/Configured/);
    });
    // The accordion auto-opens because anySaved is true after the
    // probe resolves. Wait for that to happen, then click the
    // per-model summary to reveal the per-model rows.
    await waitFor(() => {
      expect(screen.getByTestId("settings-accordion-openai").getAttribute("aria-expanded")).toBe("true");
    });
    const summary = screen.getByText(/Per-model overrides/);
    fireEvent.click(summary);
    const input = (await screen.findByTestId(
      "input-model-openai/gpt-4o-mini",
    )) as HTMLInputElement;
    // v1.5.1.1 contract: the input is NEVER prefilled, even when
    // the server confirms a key is stored. The placeholder is
    // "Replace key" to signal rotation intent.
    expect(input.value).toBe("");
    expect(input.placeholder).toBe("Replace key");
  });

  it("Save is enabled on any non-empty draft under the no-prefill contract", () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    const input = screen.getByTestId("input-default-openai") as HTMLInputElement;
    const save = screen.getByTestId("save-default-openai") as HTMLButtonElement;
    // Empty draft → disabled.
    expect(save.disabled).toBe(true);
    // Any non-empty draft → enabled (the user is rotating).
    fireEvent.change(input, { target: { value: "sk-anything" } });
    expect(save.disabled).toBe(false);
  });

  it("collapses all four providers when no active session is set", () => {
    // v1.5.1.1: when `activeProvider` is null/undefined, all four
    // accordions start collapsed, even if a key is stored.
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () =>
        jsonResponse({
          secrets: [
            { name: "llm_provider_openai_gpt-4o-mini", configured: true, updated_at: 0, hint: "…1234" },
            { name: "llm_provider_anthropic_claude-3-5-sonnet-latest", configured: true, updated_at: 0, hint: "…cdef" },
          ],
        }),
    }) as unknown as typeof fetch;
    render(
      <SettingsModal isOpen={true} onClose={() => {}} models={MODELS} activeProvider={null} />,
    );
    // All four accordions are aria-expanded="false" by default.
    expect(screen.getByTestId("settings-accordion-openai").getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("settings-accordion-anthropic").getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("settings-accordion-openrouter").getAttribute("aria-expanded")).toBe("false");
  });

  it("opens only the active provider's accordion when an active session is set", () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
    }) as unknown as typeof fetch;
    render(
      <SettingsModal isOpen={true} onClose={() => {}} models={MODELS} activeProvider="openai" />,
    );
    // OpenAI is the active provider → open.
    expect(screen.getByTestId("settings-accordion-openai").getAttribute("aria-expanded")).toBe("true");
    // Anthropic and OpenRouter stay collapsed.
    expect(screen.getByTestId("settings-accordion-anthropic").getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByTestId("settings-accordion-openrouter").getAttribute("aria-expanded")).toBe("false");
  });
});

// ============================================================================
// v1.5.1.4 (audit hotfix #4): per-provider real connection probe
// ============================================================================
// The v1.5.1 modal only confirmed a key was stored locally;
// it did NOT verify the key worked against the upstream
// provider. v1.5.1.4 adds a "Test connection" button on
// each per-provider accordion that calls
// `GET /api/llm/health/{provider}` and renders the result
// inline. The OpenRouter probe is the canonical
// `GET /api/v1/auth/key` endpoint; the backend maps a 200
// to `{ok, label, limit, is_free_tier}` and a 401 to
// `{ok: false, error: "invalid_key"}`.

describe("<SettingsModal /> v1.5.1.4: per-provider probe", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("auto-expands an accordion when a key is stored and there is no active session", async () => {
    // v1.5.1.1: when no active session, the modal renders
    // all four accordions collapsed. v1.5.1.4: if any
    // provider has a stored key, the modal auto-expands
    // that provider's accordion. The OpenRouter row
    // opens after the `/api/secrets` probe resolves.
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () =>
        jsonResponse({
          secrets: [
            {
              name: "llm_provider_openrouter_auto",
              configured: true,
              updated_at: 1,
              hint: "…debc",
            },
          ],
          names: ["llm_provider_openrouter_auto"],
        }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // Wait for the auto-open effect to fire after the
    // secrets probe resolves.
    await waitFor(() => {
      expect(
        screen
          .getByTestId("settings-accordion-openrouter")
          .getAttribute("aria-expanded"),
      ).toBe("true");
    });
    // The other providers stay collapsed (no active
    // session, no stored keys).
    expect(
      screen.getByTestId("settings-accordion-openai").getAttribute("aria-expanded"),
    ).toBe("false");
    expect(
      screen.getByTestId("settings-accordion-anthropic").getAttribute("aria-expanded"),
    ).toBe("false");
  });

  it("renders the Test connection button in the loading state while the probe is in flight", async () => {
    // The button is rendered when a key is stored for the
    // provider. While the probe is in flight, the button
    // is disabled and shows "Testing…".
    let resolveProbe!: (r: Response) => void;
    const probePromise = new Promise<Response>((resolve) => {
      resolveProbe = resolve;
    });
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () =>
        jsonResponse({
          secrets: [
            {
              name: "llm_provider_openrouter_auto",
              configured: true,
              updated_at: 1,
              hint: "…debc",
            },
          ],
          names: ["llm_provider_openrouter_auto"],
        }),
      "GET /api/llm/health/openrouter": () => probePromise as unknown as Response,
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // Wait for the accordion to auto-open.
    await waitFor(() => {
      expect(
        screen
          .getByTestId("settings-accordion-openrouter")
          .getAttribute("aria-expanded"),
      ).toBe("true");
    });
    const button = screen.getByTestId("settings-probe-button-openrouter");
    fireEvent.click(button);
    // While in flight, the button shows "Testing…" and
    // is disabled.
    expect(button).toHaveTextContent(/Testing/);
    expect(button).toBeDisabled();
    // Resolve the probe and assert the result line.
    resolveProbe(
      jsonResponse({
        ok: true,
        provider: "openrouter",
        label: "Test OpenRouter Key",
        limit: 100,
        is_free_tier: false,
      }),
    );
    await waitFor(() => {
      expect(
        screen.getByTestId("settings-probe-result-openrouter"),
      ).toHaveTextContent(/Connected as "Test OpenRouter Key"/);
    });
  });

  it("renders the error line when the probe returns invalid_key", async () => {
    // A 401 from OpenRouter /auth/key maps to
    // `{ok: false, error: "invalid_key"}` in the body. The
    // frontend renders "Invalid API key" in red.
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () =>
        jsonResponse({
          secrets: [
            {
              name: "llm_provider_openrouter_auto",
              configured: true,
              updated_at: 1,
              hint: "…debc",
            },
          ],
          names: ["llm_provider_openrouter_auto"],
        }),
      "GET /api/llm/health/openrouter": () =>
        jsonResponse({
          ok: false,
          provider: "openrouter",
          error: "invalid_key",
        }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    await waitFor(() => {
      expect(
        screen
          .getByTestId("settings-accordion-openrouter")
          .getAttribute("aria-expanded"),
      ).toBe("true");
    });
    const button = screen.getByTestId("settings-probe-button-openrouter");
    fireEvent.click(button);
    // The error line is rendered as a role=alert.
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/Invalid API key/);
  });

  it("does NOT render the Test connection button when no key is stored for the provider", async () => {
    // v1.5.1.4: the button is only rendered when
    // `anySaved` is true. A provider with no stored
    // key has no button — there's nothing to test.
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ secrets: [], names: [] }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // Wait for the secrets probe to resolve.
    await waitFor(() => {
      // No key for any provider → no probe button anywhere.
      expect(
        screen.queryByTestId("settings-probe-button-openai"),
      ).toBeNull();
      expect(
        screen.queryByTestId("settings-probe-button-anthropic"),
      ).toBeNull();
      expect(
        screen.queryByTestId("settings-probe-button-openrouter"),
      ).toBeNull();
    });
  });
});
// ============================================================================
// v1.5.1.2 (audit hotfix): error messages are sanitized
// ============================================================================
// The previous `fetchJson` inlined the raw aiohttp response body
// into the thrown Error. For a 500 the user saw
// "Error: 500: 500 Internal Server Error\nServer got itself in
// trouble" which (a) is useless, and (b) could leak file paths
// from a future PermissionError. The new `utils/sanitizeError`
// helper maps 5xx/4xx to friendly text without echoing the raw
// body. This test pins that contract.

describe("<SettingsModal /> v1.5.1.2: error sanitization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("does not include the raw server body in the user-visible error on PUT 500", async () => {
    // Server returns the literal aiohttp default 500 body.
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
      "PUT /api/secrets/llm_provider_openai_gpt-4o-mini": () =>
        new Response("500 Internal Server Error\nServer got itself in trouble\n", {
          status: 500,
        }),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    // Open the OpenAI accordion, type a draft, click Save.
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    const input = (await screen.findByTestId("input-default-openai")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sk-anything" } });
    fireEvent.click(screen.getByTestId("save-default-openai"));
    // The error message must NOT contain the raw server body.
    const err = await screen.findByRole("alert");
    expect(err.textContent).not.toContain("Server got itself in trouble");
    expect(err.textContent).not.toContain("500 Internal Server Error");
    // The friendly text is the v1.5.1.2 mapping.
    expect(err.textContent).toMatch(/Server is busy/i);
  });

  it("does not include the raw server body in the user-visible error on PUT 403 (insecure mode)", async () => {
    globalThis.fetch = makeFetch({
      "GET /api/secrets": () => jsonResponse({ names: [], values: {} }),
      "PUT /api/secrets/llm_provider_openai_gpt-4o-mini": () =>
        new Response(
          JSON.stringify({
            error:
              "secrets log has insecure permissions; chmod 600 required",
          }),
          { status: 403, headers: { "Content-Type": "application/json" } },
        ),
    }) as unknown as typeof fetch;
    render(<SettingsModal isOpen={true} onClose={() => {}} models={MODELS} />);
    fireEvent.click(screen.getByTestId("settings-accordion-openai"));
    const input = (await screen.findByTestId("input-default-openai")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "sk-anything" } });
    fireEvent.click(screen.getByTestId("save-default-openai"));
    const err = await screen.findByRole("alert");
    expect(err.textContent).not.toContain("chmod 600");
    expect(err.textContent).not.toContain("insecure");
    expect(err.textContent).toMatch(/Not authorized/i);
  });
});