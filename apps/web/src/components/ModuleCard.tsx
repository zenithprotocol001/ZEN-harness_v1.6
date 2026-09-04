type Health = "ok" | "idle" | "error";

const HEALTH_COLORS: Record<Health, string> = {
  ok: "#3fb950",
  idle: "#8b949e",
  error: "#f78166",
};

const HEALTH_LABELS: Record<Health, string> = {
  ok: "ok",
  idle: "idle",
  error: "error",
};

export function ModuleCard(props: {
  id: string;
  name: string;
  version: string;
  health: Health;
  kind: "core" | "plugin";
  loaded?: boolean;
  busy?: boolean;
  // v1.5.0.1: indicates the plugin is in the user's auto-load
  // memory list (i.e. it will load on next launch). The card
  // shows a small ★ so the user can see at a glance which
  // plugins are persistent across sessions.
  autoLoad?: boolean;
  onLoad?: () => void;
  onUnload?: () => void;
}) {
  return (
    <div className="module-card" data-kind={props.kind}>
      <div className="module-card-row">
        <div>
          <div className="module-id">
            {props.id}
            {props.kind === "plugin" && props.autoLoad && (
              <span
                className="module-autoload-star"
                title="Auto-loads on next launch (in the memory list)"
                style={{ color: "#d29922", marginLeft: 6 }}
              >
                ★
              </span>
            )}
          </div>
          <div className="module-name">{props.name}</div>
        </div>
        <div className="module-meta">
          <span
            className="module-health"
            style={{
              color: HEALTH_COLORS[props.health],
              borderColor: HEALTH_COLORS[props.health],
            }}
            title={`health: ${HEALTH_LABELS[props.health]}`}
          >
            ●
          </span>
          <span className="module-version">v{props.version}</span>
        </div>
      </div>
      {props.kind === "plugin" && (
        <div className="module-actions">
          {props.loaded ? (
            <button
              onClick={props.onUnload}
              disabled={!props.onUnload || props.busy}
            >
              {props.busy ? "..." : "Unload"}
            </button>
          ) : (
            <button
              onClick={props.onLoad}
              disabled={!props.onLoad || props.busy}
            >
              {props.busy ? "..." : "Load"}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
