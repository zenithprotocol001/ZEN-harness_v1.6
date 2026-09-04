import { useEffect, useMemo, useState } from "react";
import type { ModelOption } from "./ModelSelect";
import { Icon } from "./icons";
import { ModalPortal } from "./ModalPortal";
import { fetchJson } from "../utils/sanitizeError";

export type SettingsModalProps = {
  isOpen: boolean;
  onClose: () => void;
  /** Pre-fetched list of models. Drives the per-model key rows. */
  models: ModelOption[];
  /**
   * The provider id of the session that opened this modal (if any).
   * When set, that provider's accordion is open by default; when
   * null (no active session), all four accordions collapse to the
   * "Not set" pill.
   *
   * v1.5.1.1: this is the "no active session → collapse all" rule.
   */
  activeProvider?: string | null;
};

type SaveState =
  | { kind: "empty" }
  | { kind: "saving" }
  | { kind: "saved" }
  | { kind: "error"; message: string };

type RowKey = string;

type KeyRow = {
  rowKey: RowKey;
  label: string;
  provider: string;
  modelId: string | null; // null for the per-provider "default" row
  keyName: string;
  state: SaveState;
  draft: string;
  /** True when the server confirms a value is stored under `keyName`.
   *  v1.5.1.1 (ADR-0108): the server never returns the value to the
   *  browser, so we only track presence (`isStored`), not the value
   *  itself. The input is therefore always empty until the user types.
   */
  isStored: boolean;
  /** Last-saved value (for dirty check). null = not saved. Only set
   *  when the server returns a value (legacy v1.5.0 callers). New
   *  callers should use `isStored` instead. */
  savedValue: string | null;
  parentRowKey?: RowKey;
  /** User-added additional row (e.g. ___2). */
  isExtra?: boolean;
  /** v1.6.0 (Phase 0): source of the stored value. Populated
   *  from /api/settings/state. "raw" is the v1.5.1.4 default;
   *  "env" means the value comes from an env var (ref = name);
   *  "file" means the value comes from a file (ref = path).
   *  Source is read-only display in Phase 0; the env/file
   *  PUT API is a follow-up. */
  source?: "raw" | "env" | "file";
  sourceRef?: string | null;
};

const PROVIDER_PREFIX = "llm_provider_";

/** Friendly display name for a provider id. */
const PROVIDER_DISPLAY: Record<string, string> = {
  openai: "OpenAI",
  anthropic: "Anthropic",
  openrouter: "OpenRouter",
  "mock-llm": "Mock (testing)",
};

function displayNameFor(provider: string): string {
  return PROVIDER_DISPLAY[provider] ?? (provider.charAt(0).toUpperCase() + provider.slice(1));
}

function secretNameForModel(provider: string, modelId: string, n: number = 1): string {
  const modelPart = modelId.includes("/") ? modelId.split("/").slice(1).join("/") : modelId;
  if (n <= 1) return `${PROVIDER_PREFIX}${provider}_${modelPart}`;
  return `${PROVIDER_PREFIX}${provider}_${modelPart}___${n}`;
}

function providerFallbackKeyName(provider: string, models: ModelOption[]): string {
  const m = models.find((x) => x.provider === provider);
  if (!m) return `${PROVIDER_PREFIX}${provider}_`;
  return secretNameForModel(provider, m.id);
}

// v1.5.1.2: the local `fetchJson` was removed. The shared
// `utils/sanitizeError.fetchJson` maps HTTP 5xx / 4xx to friendly
// text without echoing the raw server body. This is the same
// helper used by ModelConfigMenu.

export function SettingsModal(props: SettingsModalProps) {
  // Group models by provider. The dep is the JSON shape so the
  // memo is stable across re-renders that pass the same content.
  const modelsKey = JSON.stringify(props.models);
  const grouped = useMemo(() => {
    const map = new Map<string, ModelOption[]>();
    for (const m of props.models) {
      const list = map.get(m.provider) ?? [];
      list.push(m);
      map.set(m.provider, list);
    }
    return Array.from(map.entries());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [modelsKey]);

  // Mutable row state. Initialized lazily on first render with
  // `saved: false` for everything. Updated by the user (drafts,
  // extras) and by the probe (saved status).
  const [rows, setRows] = useState<Record<RowKey, KeyRow>>(() => {
    const out: Record<RowKey, KeyRow> = {};
    for (const [provider, models] of grouped) {
      out[`default:${provider}`] = {
        rowKey: `default:${provider}`,
        label: `${displayNameFor(provider)} (default — applies to all ${displayNameFor(provider)} models unless overridden)`,
        provider,
        modelId: null,
        keyName: providerFallbackKeyName(provider, models),
        state: { kind: "empty" },
        draft: "",
        isStored: false,
        savedValue: null,
      };
      for (const m of models) {
        out[`model:${m.id}`] = {
          rowKey: `model:${m.id}`,
          label: m.name,
          provider,
          modelId: m.id,
          keyName: secretNameForModel(provider, m.id),
          state: { kind: "empty" },
          draft: "",
          isStored: false,
          savedValue: null,
        };
      }
    }
    return out;
  });

  // Per-row show/hide secret state.
  const [showMap, setShowMap] = useState<Record<RowKey, boolean>>({});
  const toggleShow = (rowKey: RowKey) =>
    setShowMap((prev) => ({ ...prev, [rowKey]: !prev[rowKey] }));

  // When the model list changes, add new rows (and remove rows for
  // models that disappeared). Preserve user state (draft, extras)
  // for rows whose keyName didn't change.
  useEffect(() => {
    setRows((prev) => {
      const next: Record<RowKey, KeyRow> = {};
      for (const k of Object.keys(prev)) {
        if (prev[k].isExtra) next[k] = prev[k];
      }
      for (const [provider, models] of grouped) {
        const defaultRow: KeyRow = {
          rowKey: `default:${provider}`,
          label: `${displayNameFor(provider)} (default — applies to all ${displayNameFor(provider)} models unless overridden)`,
          provider,
          modelId: null,
          keyName: providerFallbackKeyName(provider, models),
          state: { kind: "empty" },
          draft: "",
          isStored: false,
          savedValue: null,
        };
        const existing = prev[`default:${provider}`];
        if (existing && existing.keyName === defaultRow.keyName) {
          next[`default:${provider}`] = {
            ...defaultRow,
            draft: existing.draft,
            state: existing.state,
            isStored: existing.isStored,
            savedValue: existing.savedValue,
          };
        } else {
          next[`default:${provider}`] = defaultRow;
        }
        for (const m of models) {
          const rowKey = `model:${m.id}`;
          const baseRow: KeyRow = {
            rowKey,
            label: m.name,
            provider,
            modelId: m.id,
            keyName: secretNameForModel(provider, m.id),
            state: { kind: "empty" },
            draft: "",
            isStored: false,
            savedValue: null,
          };
          const existing = prev[rowKey];
          if (existing && existing.keyName === baseRow.keyName) {
            next[rowKey] = {
              ...baseRow,
              draft: existing.draft,
              state: existing.state,
              isStored: existing.isStored,
              savedValue: existing.savedValue,
            };
          } else {
            next[rowKey] = baseRow;
          }
        }
      }
      return next;
    });
  }, [grouped]);

  // Probe /api/secrets to discover saved keys. Runs when the modal
  // opens; refreshes the saved status of every existing row.
  useEffect(() => {
    if (!props.isOpen) return;
    let cancelled = false;
    (async () => {
      try {
        const body = (await fetchJson("/api/secrets")) as
          | {
              secrets?: Array<{ name: string; configured: boolean; updated_at: number; hint: string }>;
              names?: string[];
              values?: Record<string, string>;
            }
          | null;
        if (cancelled) return;
        // v1.5.1.1 (ADR-0108): the metadata shape is preferred; the
        // v1.5.0 `names` shape is still accepted for backwards compat.
        // The metadata endpoint NEVER returns the key value — only the
        // 7-char hint (first 3 + … + last 4). We track presence via
        // `isStored`; the input is never prefilled.
        const namesFromSecrets = (body?.secrets ?? []).map((s) => s.name);
        const allSaved = new Set(
          namesFromSecrets.length > 0 ? namesFromSecrets : (body?.names ?? []),
        );
        const allValues = body?.values ?? {};
        setRows((prev) => {
          const next: Record<RowKey, KeyRow> = {};
          for (const k of Object.keys(prev)) {
            const r = prev[k];
            const stored = allSaved.has(r.keyName);
            // Legacy v1.5.0 callers may include `values`; preserve
            // them for the dirty check. New callers (v1.5.1.1+) only
            // see the metadata shape and use `isStored`.
            next[k] = {
              ...r,
              isStored: stored,
              savedValue: stored ? (allValues[r.keyName] ?? null) : null,
            };
          }
          return next;
        });
      } catch {
        // ignore
      }
      // v1.6.0 (Phase 0): also fetch /api/settings/state to
      // discover the *source* of each stored key (raw / env / file).
      // The source is read-only display in Phase 0; the env/file
      // PUT API is a follow-up. The state endpoint NEVER echoes a
      // raw key value (per the no-leak contract; see ADR-0110).
      try {
        const state = (await fetchJson("/api/settings/state")) as
          | { providers?: Record<string, { status: string; source: string | null; ref_hint: string | null; health: string }> }
          | null;
        if (cancelled) return;
        const providers = state?.providers ?? {};
        // Build a keyName -> {source, ref} map by looking at each
        // provider's primary + fallback key names (matches the
        // server's logic in _api_settings_state).
        const sourceByKey: Record<string, { source: "raw" | "env" | "file"; ref: string | null }> = {};
        for (const r of Object.values(providers)) {
          if (r.status !== "configured") continue;
          // The server only knows the provider-level source; the
          // per-row source is the same for all rows in a provider
          // (the v1.5.1.4 model is per-model keys, but a single
          // provider typically has one source for all keys).
          const src = (r.source ?? "raw") as "raw" | "env" | "file";
          // The server doesn't tell us which keyName it is; we
          // approximate by matching the row's keyName prefix.
          // (Phase 0 display only; the full per-key source mapping
          // is a follow-up.)
          sourceByKey[src] = { source: src, ref: r.ref_hint };
        }
        setRows((prev) => {
          const next: Record<RowKey, KeyRow> = {};
          for (const k of Object.keys(prev)) {
            const r = prev[k];
            // Match by keyName prefix: e.g. "llm_provider_openrouter_auto"
            // matches provider "openrouter".
            const providerMatch = r.keyName.match(/^llm_provider_([a-z0-9-]+)_/);
            const provider = providerMatch ? providerMatch[1] : null;
            let info = null;
            if (provider && providers[provider] && providers[provider].status === "configured") {
              const src = (providers[provider].source ?? "raw") as "raw" | "env" | "file";
              info = { source: src, ref: providers[provider].ref_hint ?? null };
            }
            next[k] = {
              ...r,
              source: info?.source ?? r.source ?? "raw",
              sourceRef: info?.ref ?? r.sourceRef ?? null,
            };
          }
          return next;
        });
      } catch {
        // ignore — settings/state is best-effort
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [props.isOpen]);

  const isRowSaved = (r: KeyRow): boolean => r.isStored || r.savedValue !== null;
  // v1.5.1.1 (ADR-0108): the server never returns the key value, so
  // we can't compare the draft against a "saved value". A non-empty
  // draft is always a save intent (the user is rotating or replacing).
  const isDirty = (r: KeyRow): boolean => Boolean(r.draft.trim());
  const canSave = (r: KeyRow): boolean => isDirty(r) && r.state.kind !== "saving";

  const updateRow = (rowKey: RowKey, patch: Partial<KeyRow>) => {
    setRows((prev) => {
      const cur = prev[rowKey];
      if (!cur) return prev;
      return { ...prev, [rowKey]: { ...cur, ...patch } };
    });
  };

  const saveKey = async (rowKey: RowKey) => {
    const row = rows[rowKey];
    if (!row) return;
    if (!canSave(row)) return;
    updateRow(rowKey, { state: { kind: "saving" } });
    try {
      await fetchJson(`/api/secrets/${encodeURIComponent(row.keyName)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: row.draft }),
      });
      updateRow(rowKey, { state: { kind: "saved" } });
      setRows((prev) => {
        const cur = prev[rowKey];
        if (!cur) return prev;
        return { ...prev, [rowKey]: { ...cur, savedValue: row.draft } };
      });
    } catch (e) {
      updateRow(rowKey, { state: { kind: "error", message: String(e) } });
    }
  };

  const deleteKey = async (rowKey: RowKey, keyName?: string) => {
    const row = rows[rowKey];
    if (!row) return;
    const target = keyName ?? row.keyName;
    try {
      await fetchJson(`/api/secrets/${encodeURIComponent(target)}`, {
        method: "DELETE",
      });
      if (row.isExtra) {
        setRows((prev) => {
          const next = { ...prev };
          delete next[rowKey];
          return next;
        });
      } else {
        updateRow(rowKey, { state: { kind: "empty" }, draft: "" });
        setRows((prev) => {
          const cur = prev[rowKey];
          if (!cur) return prev;
          return { ...prev, [rowKey]: { ...cur, savedValue: null } };
        });
      }
    } catch (e) {
      updateRow(rowKey, { state: { kind: "error", message: String(e) } });
    }
  };

  const addAnother = (rowKey: RowKey) => {
    const row = rows[rowKey];
    if (!row || !row.modelId) return;
    let n = 2;
    while (rows[`extra:${row.modelId}:${n}`]) n += 1;
    const newKey = secretNameForModel(row.provider, row.modelId, n);
    const extraRow: KeyRow = {
      rowKey: `extra:${row.modelId}:${n}`,
      label: `${row.label} (additional #${n})`,
      provider: row.provider,
      modelId: row.modelId,
      keyName: newKey,
      state: { kind: "empty" },
      draft: "",
      savedValue: null,
      parentRowKey: rowKey,
      isExtra: true,
    };
    setRows((prev) => ({ ...prev, [extraRow.rowKey]: extraRow }));
  };

  return (
    <ModalPortal open={props.isOpen} onClose={props.onClose} panelTestId="settings-modal">
      <header className="settings-modal-header">
        <div>
          <h2 className="settings-modal-title">API Keys</h2>
          <p className="settings-modal-subtitle">
            Encrypted at rest, never sent to the browser in any response.
          </p>
        </div>
        <button
          type="button"
          className="settings-modal-close"
          aria-label="Close settings"
          onClick={props.onClose}
          data-testid="settings-modal-close"
        >
          <Icon name="close" size={18} />
        </button>
      </header>
      <div className="settings-modal-body">
        <p className="settings-modal-description">
          Enter your API keys per model. A "default" key per provider acts
          as a fallback for any model that doesn't have its own key. Keys
          are encrypted in the harness's local secrets log.
        </p>
        {grouped.length === 0 && (
          <p className="settings-modal-empty">No models available.</p>
        )}
        {grouped.map(([provider, models]) => {
          const defaultRow = rows[`default:${provider}`];
          const modelRows = models
            .map((m) => rows[`model:${m.id}`])
            .filter((r): r is KeyRow => Boolean(r));
          const extraRows = Object.values(rows).filter(
            (r) => r.isExtra && r.provider === provider,
          );
          const anySaved =
            (defaultRow && isRowSaved(defaultRow)) ||
            modelRows.some((r) => isRowSaved(r)) ||
            extraRows.some((r) => isRowSaved(r));
          // v1.5.1.1: when no active session, collapse all four.
          // When there is an active session, open only the active
          // provider. When there are stored keys, the active-session
          // rule is overridden (the user has something to look at).
          const noActive = props.activeProvider == null;
          const isActive = props.activeProvider === provider;
          const defaultOpen = !noActive && isActive ? true : anySaved;
          return (
            <ProviderAccordion
              key={provider}
              provider={provider}
              displayName={displayNameFor(provider)}
              defaultOpen={defaultOpen}
              defaultRow={defaultRow}
              modelRows={modelRows}
              extraRows={extraRows}
              isRowSaved={isRowSaved}
              canSave={canSave}
              showMap={showMap}
              onSave={saveKey}
              onDelete={deleteKey}
              onAddAnother={addAnother}
              onDraftChange={(rk, v) => updateRow(rk, { draft: v })}
              onToggleShow={toggleShow}
            />
          );
        })}
      </div>
      <footer className="settings-modal-footer">
        <button
          type="button"
          className="settings-close-button"
          onClick={props.onClose}
          data-testid="settings-modal-footer-close"
        >
          Close
        </button>
      </footer>
    </ModalPortal>
  );
}

function ProviderAccordion(props: {
  provider: string;
  displayName: string;
  defaultOpen: boolean;
  defaultRow?: KeyRow;
  modelRows: KeyRow[];
  extraRows: KeyRow[];
  isRowSaved: (r: KeyRow) => boolean;
  canSave: (r: KeyRow) => boolean;
  showMap: Record<RowKey, boolean>;
  onSave: (rk: RowKey) => void;
  onDelete: (rk: RowKey, keyName?: string) => void;
  onAddAnother: (rk: RowKey) => void;
  onDraftChange: (rk: RowKey, v: string) => void;
  onToggleShow: (rk: RowKey) => void;
}) {
  const anySaved =
    (props.defaultRow && props.isRowSaved(props.defaultRow)) ||
    props.modelRows.some((r) => props.isRowSaved(r)) ||
    props.extraRows.some((r) => props.isRowSaved(r));
  const [open, setOpen] = useState<boolean>(props.defaultOpen || anySaved);
  // If a probe revealed that this provider has a saved key after
  // mount, expand it. We only auto-open (never auto-close) — the
  // user's manual collapse is preserved.
  useEffect(() => {
    if (anySaved && !open) {
      setOpen(true);
    }
    // We deliberately exclude `open` from deps so a user-initiated
    // collapse isn't undone by a re-probe.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anySaved]);
  // Per-model sub-accordion is auto-opened when any of the per-model
  // rows (or their extras) is saved. User can still collapse it.
  const anyModelSaved =
    props.modelRows.some((r) => props.isRowSaved(r)) ||
    props.extraRows.some((r) => props.isRowSaved(r));
  const [modelOpen, setModelOpen] = useState<boolean>(anyModelSaved);
  useEffect(() => {
    if (anyModelSaved && !modelOpen) {
      setModelOpen(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anyModelSaved]);
  const statusLabel = anySaved ? "Configured" : "Not set";
  // v1.5.1.4 (audit hotfix #4): per-provider real connection
  // probe. The "Configured" pill only tells the user that a
  // key is stored locally; it does NOT confirm the key works
  // against the upstream provider. v1.5.1.4 adds a
  // "Test connection" button that calls
  // `GET /api/llm/health/{provider}` and renders the result
  // inline. For OpenRouter, the probe is the canonical
  // `GET /api/v1/auth/key` (the OpenRouter-documented
  // "is my key valid" endpoint). For other providers the
  // route returns `not_supported` and we render a
  // best-effort message.
  type ProbeState =
    | { kind: "idle" }
    | { kind: "probing" }
    | { kind: "ok"; label: string | null; isFreeTier: boolean | null }
    | { kind: "invalid" }
    | { kind: "network" }
    | { kind: "not_supported" };
  const [probe, setProbe] = useState<ProbeState>({ kind: "idle" });
  const onTestConnection = async () => {
    setProbe({ kind: "probing" });
    try {
      const body = (await fetchJson(
        `/api/llm/health/${encodeURIComponent(props.provider)}`,
      )) as {
        ok: boolean;
        provider: string;
        label?: string | null;
        is_free_tier?: boolean | null;
        error?: string;
      };
      if (body.ok) {
        setProbe({
          kind: "ok",
          label: body.label ?? null,
          isFreeTier: body.is_free_tier ?? null,
        });
      } else if (body.error === "invalid_key") {
        setProbe({ kind: "invalid" });
      } else if (body.error === "network") {
        setProbe({ kind: "network" });
      } else if (body.error === "not_supported") {
        setProbe({ kind: "not_supported" });
      } else {
        // unknown error
        setProbe({ kind: "network" });
      }
    } catch {
      // The sanitized fetchJson helper already maps 5xx to
      // a friendly message; we don't need to re-map.
      setProbe({ kind: "network" });
    }
  };
  return (
    <section
      className={`settings-provider ${open ? "is-open" : ""}`}
      data-provider={props.provider}
    >
      <button
        type="button"
        className="settings-provider-header"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        data-testid={`settings-accordion-${props.provider}`}
      >
        <span
          className={`settings-provider-chevron ${open ? "is-open" : ""}`}
          aria-hidden="true"
        >
          <Icon name="chevronRight" size={14} />
        </span>
        <span className="settings-provider-name">{props.displayName}</span>
        <span
          className={`settings-provider-status ${anySaved ? "is-saved" : "is-empty"}`}
          data-testid={`settings-status-${props.provider}`}
        >
          {statusLabel}
        </span>
      </button>
      {open && (
        <div className="settings-provider-body">
          {anySaved && (
            <div
              className="settings-provider-probe"
              data-testid={`settings-probe-${props.provider}`}
            >
              <button
                type="button"
                className="settings-provider-probe-button"
                onClick={onTestConnection}
                disabled={probe.kind === "probing"}
                data-testid={`settings-probe-button-${props.provider}`}
              >
                {probe.kind === "probing" ? "Testing…" : "Test connection"}
              </button>
              {probe.kind === "ok" && (
                <span
                  className="settings-provider-probe-result is-ok"
                  data-testid={`settings-probe-result-${props.provider}`}
                >
                  Connected{probe.label ? ` as "${probe.label}"` : ""}
                  {probe.isFreeTier === true ? " (free tier)" : ""}
                </span>
              )}
              {probe.kind === "invalid" && (
                <span
                  className="settings-provider-probe-result is-invalid"
                  role="alert"
                >
                  Invalid API key
                </span>
              )}
              {probe.kind === "network" && (
                <span className="settings-provider-probe-result is-network">
                  Network error — try again
                </span>
              )}
              {probe.kind === "not_supported" && (
                <span className="settings-provider-probe-result is-na">
                  Live probe not available for this provider
                </span>
              )}
            </div>
          )}
          {props.defaultRow && (
            <KeyRowView
              key={props.defaultRow.rowKey}
              row={props.defaultRow}
              saved={props.isRowSaved(props.defaultRow)}
              canSave={props.canSave(props.defaultRow)}
              show={props.showMap[props.defaultRow.rowKey] === true}
              testIdPrefix={`default-${props.provider}`}
              onSave={() => props.onSave(props.defaultRow!.rowKey)}
              onDelete={() => props.onDelete(props.defaultRow!.rowKey)}
              onDraftChange={(v) => props.onDraftChange(props.defaultRow!.rowKey, v)}
              onToggleShow={() => props.onToggleShow(props.defaultRow!.rowKey)}
            />
          )}
          {props.modelRows.length > 0 && (
            <details
              className="settings-model-accordion"
              data-testid={`settings-models-${props.provider}`}
              open={modelOpen}
            >
              <summary
                className="settings-model-summary"
                onClick={(e) => {
                  e.preventDefault();
                  setModelOpen((v) => !v);
                }}
              >
                Per-model overrides ({props.modelRows.length})
                <Icon name="chevronDown" size={12} />
              </summary>
              <div className="settings-model-rows">
                {props.modelRows.map((r) => {
                  const extras = props.extraRows.filter((x) => x.modelId === r.modelId);
                  return (
                    <div key={r.rowKey}>
                      <KeyRowView
                        row={r}
                        saved={props.isRowSaved(r)}
                        canSave={props.canSave(r)}
                        show={props.showMap[r.rowKey] === true}
                        testIdPrefix={`model-${r.modelId}`}
                        onSave={() => props.onSave(r.rowKey)}
                        onDelete={() => props.onDelete(r.rowKey)}
                        onDraftChange={(v) => props.onDraftChange(r.rowKey, v)}
                        onAddAnother={extras.length > 0 ? undefined : () => props.onAddAnother(r.rowKey)}
                        extraCount={extras.length}
                        onToggleShow={() => props.onToggleShow(r.rowKey)}
                      />
                      {extras.map((er) => (
                        <KeyRowView
                          key={er.rowKey}
                          row={er}
                          saved={props.isRowSaved(er)}
                          canSave={props.canSave(er)}
                          show={props.showMap[er.rowKey] === true}
                          testIdPrefix={`extra-${er.modelId}-${er.keyName.split("___")[1]}`}
                          onSave={() => props.onSave(er.rowKey)}
                          onDelete={() => props.onDelete(er.rowKey)}
                          onDraftChange={(v) => props.onDraftChange(er.rowKey, v)}
                          onToggleShow={() => props.onToggleShow(er.rowKey)}
                        />
                      ))}
                    </div>
                  );
                })}
              </div>
            </details>
          )}
        </div>
      )}
    </section>
  );
}

function KeyRowView(props: {
  row: KeyRow;
  saved: boolean;
  canSave: boolean;
  show: boolean;
  testIdPrefix: string;
  onSave: () => void;
  onDelete: () => void;
  onDraftChange: (v: string) => void;
  onAddAnother?: () => void;
  extraCount?: number;
  onToggleShow: () => void;
}) {
  const {
    row,
    saved,
    canSave,
    show,
    testIdPrefix,
    onSave,
    onDelete,
    onDraftChange,
    onAddAnother,
    extraCount,
    onToggleShow,
  } = props;
  return (
    <div className="settings-key-row" data-row={testIdPrefix}>
      <div className="settings-key-label">{row.label}</div>
      <div className="settings-key-controls">
        <div className="settings-key-input-wrap">
          <input
            type={show ? "text" : "password"}
            className="settings-key-input"
            placeholder={saved ? "Replace key" : "Paste key…"}
            value={row.draft}
            onChange={(e) => onDraftChange(e.target.value)}
            data-testid={`input-${testIdPrefix}`}
            autoComplete="off"
            spellCheck={false}
          />
          <button
            type="button"
            className="settings-key-eye"
            aria-label={show ? "Hide key" : "Show key"}
            aria-pressed={show}
            onClick={onToggleShow}
            data-testid={`show-${testIdPrefix}`}
          >
            <Icon name={show ? "eyeOff" : "eye"} size={14} />
          </button>
        </div>
        {/* v1.6.0 (Phase 0): read-only source pill. The env/file
            PUT API is a follow-up; for now the pill is display
            only. The raw mode (v1.5.1.4 default) is suppressed
            because it carries no information beyond "saved". */}
        {saved && row.source && row.source !== "raw" && (
          <span
            className={`settings-source-pill is-${row.source}`}
            data-testid={`source-${testIdPrefix}`}
            title={
              row.source === "env"
                ? `Imported from environment variable: ${row.sourceRef ?? ""}`
                : `Imported from file: ${row.sourceRef ?? ""}`
            }
          >
            {row.source === "env" ? "env" : "file"}
            {row.sourceRef ? `: ${row.sourceRef}` : ""}
          </span>
        )}
        <button
          type="button"
          className={`settings-save-button ${canSave ? "is-active" : ""}`}
          onClick={onSave}
          disabled={!canSave}
          aria-disabled={!canSave}
          data-testid={`save-${testIdPrefix}`}
        >
          {row.state.kind === "saving" ? "Saving…" : saved ? "Update" : "Save"}
        </button>
        {saved && (
          <button
            type="button"
            className="settings-delete-button"
            onClick={onDelete}
            data-testid={`delete-${testIdPrefix}`}
          >
            <Icon name="trash" size={14} />
            <span>Delete</span>
          </button>
        )}
        {onAddAnother && (
          <button
            type="button"
            className="settings-add-another-button"
            onClick={onAddAnother}
            data-testid={`add-another-${testIdPrefix}`}
          >
            {extraCount && extraCount > 0
              ? `+ Add another key (${extraCount} extra)`
              : "+ Add another key"}
          </button>
        )}
      </div>
      {row.state.kind === "error" && (
        <p className="settings-error" role="alert">
          {row.state.message}
        </p>
      )}
    </div>
  );
}

export { secretNameForModel, providerFallbackKeyName };
