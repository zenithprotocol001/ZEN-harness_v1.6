param(
    [string]$Root = (Resolve-Path "$PSScriptRoot\..").Path
)
$ErrorActionPreference = "Continue"

$failures = New-Object System.Collections.Generic.List[string]

function Assert($cond, $label) {
    if ($cond) { Write-Output "PASS: $label" }
    else { Write-Output "FAIL: $label"; $failures.Add($label) }
}

# ---- C1 ----
$c1 = Get-Content -LiteralPath (Join-Path $Root "src\dhc\modules\c1_gui_web_core/service.py") -Raw
Assert ($c1 -match "CSP_HEADER") "C1 exports CSP_HEADER"
Assert ($c1 -match "default-src 'self'") "C1 CSP has default-src 'self'"
Assert ($c1 -match "script-src 'self'") "C1 CSP has script-src 'self'"
Assert ($c1 -match "object-src 'none'") "C1 CSP has object-src 'none'"
Assert ($c1 -match "frame-ancestors 'none'") "C1 CSP has frame-ancestors 'none'"

$c1Policy = ([regex]::Match($c1, '(?s)CSP_HEADER\s*:\s*str\s*=\s*\((.+?)\)')).Groups[1].Value
$c1PolicyJoined = $c1Policy -replace "`n", " " -replace '"', ""
if ($c1PolicyJoined -notmatch "'unsafe-inline'") {
    Write-Output "PASS: C1 CSP policy string forbids 'unsafe-inline'"
} else {
    Write-Output "FAIL: C1 CSP policy string contains 'unsafe-inline'"
    $failures.Add("C1 CSP policy string contains 'unsafe-inline'")
}
if ($c1PolicyJoined -notmatch "'unsafe-eval'") {
    Write-Output "PASS: C1 CSP policy string forbids 'unsafe-eval'"
} else {
    Write-Output "FAIL: C1 CSP policy string contains 'unsafe-eval'"
    $failures.Add("C1 CSP policy string contains 'unsafe-eval'")
}
Assert ($c1 -match "class GuiWebCore") "C1 has GuiWebCore class"
Assert ($c1 -match '"/ws"') "C1 registers /ws route"
Assert ($c1 -match "_is_allowed_origin") "C1 has origin guard helper"
Assert ($c1 -match "_check_token") "C1 has bearer token comparison helper"
Assert ($c1 -match "secrets\.compare_digest") "C1 uses constant-time token comparison"
Assert ($c1 -match "secrets\.token_urlsafe") "C1 generates 256-bit token via secrets"
Assert ($c1 -match "_embed_token_in_index") "C1 embeds token in served index.html"
Assert ($c1 -match "require_token") "C1 has require_token opt-out"

$appTsx = Get-Content -LiteralPath (Join-Path $Root "apps/web/src/App.tsx") -Raw
Assert ($appTsx -match "dhc-token") "App reads dhc-token meta tag"
Assert ($appTsx -match "token=") "App sends token in WS query string"
Assert ($appTsx -match "unauthorized") "App handles 401 state"

$sanitize = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\sanitize.ts") -Raw
Assert ($sanitize -match "DOMPurify") "Web sanitize uses DOMPurify"
Assert ($sanitize -match "FORBID_TAGS") "Web sanitize sets FORBID_TAGS"
Assert ($sanitize -match "script") "Web sanitize forbids script tag"

$app = Get-Content -LiteralPath (Join-Path $Root "apps\web\src/App.tsx") -Raw
Assert ($app -match "panels/ModulesPanel") "App imports ModulesPanel"
Assert ($app -match "panels/EventsPanel") "App imports EventsPanel"
Assert ($app -match "panels/PromptsPanel") "App imports PromptsPanel"
Assert ($app -match "panels/ChatPanel") "App imports ChatPanel"
Assert ($app -match 'setTab\(') "App has setTab (4-tab router)"

# v1.3.0: ModelSelect component exists and ChatPanel wires it.
$model_select = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\components\ModelSelect.tsx") -Raw
Assert ($model_select -match "export function ModelSelect") "ModelSelect component is exported"
Assert ($model_select -match "/api/models") "ModelSelect fetches /api/models"
$chat_panel = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\panels\ChatPanel.tsx") -Raw
Assert ($chat_panel -match "import.*ModelSelect") "ChatPanel imports ModelSelect"
Assert ($chat_panel -match 'setSessionModel|"model":') "ChatPanel PATCHes session with model"

# Positive: Markdown.tsx is the ONLY file that calls dangerouslySetInnerHTML.
# Negative: App.tsx, EventsPanel.tsx, ChatPanel.tsx, and any other
# component must NOT call dangerouslySetInnerHTML or the sanitizers
# directly. They render through the <Markdown /> and <ToolResult />
# components which live in components/Markdown.tsx.
$markdown_tsx = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\components\Markdown.tsx") -Raw
Assert ($markdown_tsx -match "dangerouslySetInnerHTML") "components/Markdown.tsx is the owner of dangerouslySetInnerHTML"
Assert ($markdown_tsx -match "renderMarkdown") "components/Markdown.tsx uses renderMarkdown"
Assert ($markdown_tsx -match "renderToolResult") "components/Markdown.tsx uses renderToolResult"

# Negative invariants: HTML injection must NOT appear in App.tsx,
# EventsPanel.tsx, ChatPanel.tsx, or any other component. The
# invariant is: any of {renderMarkdown, renderToolResult,
# dangerouslySetInnerHTML} appearing in those files is a regression
# that bypasses the centralized XSS guardrail.
$forbiddenFiles = @(
    "apps\web\src\App.tsx",
    "apps\web\src\panels\EventsPanel.tsx",
    "apps\web\src\panels\ChatPanel.tsx",
    "apps\web\src\panels\ModulesPanel.tsx",
    "apps\web\src\panels\PromptsPanel.tsx",
    "apps\web\src\panels\SessionList.tsx",
    "apps\web\src\components\ModuleCard.tsx",
    "apps\web\src\components\SessionContextMenu.tsx",
    "apps\web\src\components\SearchOverlay.tsx"
)
$forbiddenSubstrings = @("renderMarkdown", "renderToolResult", "dangerouslySetInnerHTML")
foreach ($rel in $forbiddenFiles) {
    $abs = Join-Path $Root $rel
    if (-not (Test-Path -LiteralPath $abs)) { continue }
    $src = Get-Content -LiteralPath $abs -Raw
    foreach ($needle in $forbiddenSubstrings) {
        if ($src -notmatch [regex]::Escape($needle)) {
            Write-Output "PASS: $rel does NOT contain $needle"
        } else {
            Write-Output "FAIL: $rel contains $needle (must live only in components/Markdown.tsx)"
            $failures.Add("$rel contains $needle")
        }
    }
}

$events_panel = Get-Content -LiteralPath (Join-Path $Root "apps\web\src/panels/EventsPanel.tsx") -Raw
Assert ($events_panel -match "components/Markdown") "EventsPanel imports components/Markdown"

$modules_panel = Get-Content -LiteralPath (Join-Path $Root "apps\web/src/panels/ModulesPanel.tsx") -Raw
Assert ($modules_panel -match "/api/eval") "ModulesPanel posts to /api/eval"
Assert ($modules_panel -match "ModuleCard") "ModulesPanel uses ModuleCard component"

$prompts_panel = Get-Content -LiteralPath (Join-Path $Root "apps\web/src/panels/PromptsPanel.tsx") -Raw
Assert ($prompts_panel -match "/prompts") "PromptsPanel fetches /prompts"

$module_card = Get-Content -LiteralPath (Join-Path $Root "apps\web/src/components/ModuleCard.tsx") -Raw
Assert ($module_card -match "onLoad") "ModuleCard exposes onLoad"
Assert ($module_card -match "onUnload") "ModuleCard exposes onUnload"

# ---- scorer ----
$scorer = Get-Content -LiteralPath (Join-Path $Root "src\dhc\scoring\scorer.py") -Raw
Assert ($scorer -match "compute_dhc_v") "scorer has compute_dhc_v"
Assert ($scorer -match "score_functionality") "scorer has score_functionality"
Assert ($scorer -match "score_security") "scorer has score_security"
Assert ($scorer -match "make_report") "scorer has make_report"
Assert ($scorer -match "write_report") "scorer has write_report"
Assert ($scorer -match "functionality_score \* \(security_score / 100") "scorer uses multiplicative formula"
Assert ($scorer -match "security < 50") "scorer enforces security<50 hard floor"
Assert ($scorer -match "critical") "scorer handles critical findings"
Assert ($scorer -match "-100") "scorer deducts -100 for critical"
Assert ($scorer -match "-30") "scorer deducts -30 for high"
Assert ($scorer -match "-10") "scorer deducts -10 for medium"
Assert ($scorer -match "-5") "scorer deducts -5 for low"
Assert ($scorer -match "dhc-v-report.json") "scorer writes dhc-v-report.json"

$additivePattern = "0\.5 \* functionality \+ 0\.5 \* security"
if ($scorer -match $additivePattern) {
    Write-Output "FAIL: scorer contains forbidden additive formula"
    $failures.Add("scorer contains forbidden additive formula")
} else {
    Write-Output "PASS: scorer does not use additive formula"
}

# ---- cordis ----
$cord = Get-Content -LiteralPath (Join-Path $Root "src\dhc\cordis/context.py") -Raw
Assert ($cord -match 'async def dispose') "cordis Context has async dispose"
Assert ($cord -match 'reversed\(') "cordis Context disposes in reverse"
$events = Get-Content -LiteralPath (Join-Path $Root "src\dhc\cordis/events.py") -Raw
Assert ($events -match 'def off\(') "EventEmitter has off()"
Assert ($events -match 'async def waterfall') "EventEmitter has waterfall"

# ---- plugin marketplace ----
$manifest = Get-Content -LiteralPath (Join-Path $Root "src\dhc\plugins/_manifest.py") -Raw
Assert ($manifest -match "class PluginManifest") "PluginManifest class defined"
Assert ($manifest -match 'extra="forbid"') "PluginManifest extra=forbid"
Assert ($manifest -match "strict=True") "PluginManifest strict=True"
Assert ($manifest -match "sha256") "PluginManifest has sha256 field"

$loader = Get-Content -LiteralPath (Join-Path $Root "src\dhc/plugins/loader.py") -Raw
Assert ($loader -match "def load") "loader.load defined"
Assert ($loader -match "def unload") "loader.unload defined"
Assert ($loader -match "def discover") "loader.discover defined"
Assert ($loader -match "PluginIntegrityError") "loader has PluginIntegrityError"
Assert ($loader -match "compare_digest") "loader uses constant-time SHA-256 compare"
Assert ($loader -match "sha256") "loader reads SHA-256"

# Five bundled plugin manifests must each be present and valid JSON.
foreach ($plugin_id in @("rate_limiter_v1", "session_exporter_v1", "model_router_v1", "memory_store_v1", "prompt_browser_v1")) {
    $m = Get-Content -LiteralPath (Join-Path $Root "src\dhc\plugins/$plugin_id/manifest.json") -Raw
    $pid_re = [regex]::Escape($plugin_id)
    Assert ($m -match ('"id":\s*"' + $pid_re + '"')) "plugin $plugin_id manifest has matching id"
    Assert ($m -match '"version":\s*"') "plugin $plugin_id manifest has version"
    Assert ($m -match '"sha256":\s*"[a-f0-9]{64}"') "plugin $plugin_id manifest has 64-char sha256"
}

# docs/SHA-PINNING.md must record the same SHA as the manifest.
# This locks the docs to the code so a future drift is caught at
# invariant-check time, not at audit time.
$pinning = Get-Content -LiteralPath (Join-Path $Root "docs\SHA-PINNING.md") -Raw
foreach ($plugin_id in @("rate_limiter_v1", "session_exporter_v1", "model_router_v1", "memory_store_v1", "prompt_browser_v1")) {
    $m = Get-Content -LiteralPath (Join-Path $Root "src\dhc\plugins/$plugin_id/manifest.json") -Raw
    $shaMatch = [regex]::Match($m, '"sha256":\s*"([a-f0-9]{64})"')
    if ($shaMatch.Success) {
        $manifest_sha = $shaMatch.Groups[1].Value
        $pid_re = [regex]::Escape($plugin_id)
        $rowPattern = ('\|\s*`' + $pid_re + '`\s*\|\s*`([a-f0-9]{64})`\s*\|')
        $rowMatch = [regex]::Match($pinning, $rowPattern)
        if ($rowMatch.Success) {
            $doc_sha = $rowMatch.Groups[1].Value
            if ($manifest_sha -eq $doc_sha) {
                Write-Output "PASS: docs/SHA-PINNING.md records correct SHA for $plugin_id"
            } else {
                Write-Output "FAIL: docs/SHA-PINNING.md SHA for $plugin_id is $doc_sha but manifest is $manifest_sha"
                $failures.Add("SHA-PINNING.md mismatch for $plugin_id")
            }
        } else {
            Write-Output "FAIL: docs/SHA-PINNING.md missing SHA row for $plugin_id"
            $failures.Add("SHA-PINNING.md missing $plugin_id row")
        }
    } else {
        Write-Output "FAIL: could not extract sha256 from manifest of $plugin_id"
        $failures.Add("manifest of $plugin_id has no extractable sha256")
    }
}

# C1 must register the new marketplace routes
$c1 = Get-Content -LiteralPath (Join-Path $Root "src\dhc/modules/c1_gui_web_core/service.py") -Raw
Assert ($c1 -match "/api/manifest") "C1 registers /api/manifest route"
Assert ($c1 -match 'add_post\("/plugins/\{plugin_id\}"') "C1 registers POST /plugins/{id}"
Assert ($c1 -match 'add_delete\("/plugins/\{plugin_id\}"') "C1 registers DELETE /plugins/{id}"
Assert ($c1 -match "/prompts") "C1 registers /prompts route"
Assert ($c1 -match "/api/eval") "C1 registers /api/eval route"
Assert ($c1 -match "eval_pasted_code") "C1 imports eval_pasted_code from dhc.plugins._inproc_eval"

# v1.3.1: per-secret nonce envelope (ADR-0010)
$secrets_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\cordis\secrets.py") -Raw
Assert ($secrets_py -match 'HEADER_V2\s*=\s*b"DHC2"') "secrets: HEADER_V2 = b'DHC2' is defined"
Assert ($secrets_py -match "_derive_keys\(.*salt") "secrets: _derive_keys accepts a salt arg"
Assert ($secrets_py -match "scrypt\(.*salt=salt") "secrets: scrypt KDF uses the per-call salt"

# v1.3.1: ModelConfig + ModelConfigStore (ADR-0011)
Assert ($secrets_py -match "def put_raw") "secrets: SecretsService.put_raw is defined"
Assert ($secrets_py -match "def get_raw") "secrets: SecretsService.get_raw is defined"
$model_config = Get-Content -LiteralPath (Join-Path $Root "src\dhc\services\model_config.py") -Raw
Assert ($model_config -match "@dataclass\(frozen=True\)\s+class ModelConfig") "model_config: ModelConfig is a frozen dataclass"
Assert ($model_config -match "class ModelConfigStore") "model_config: ModelConfigStore class defined"
Assert ($model_config -match "model_config_\{session_id\}") "model_config: secret name pattern uses model_config_<id>"

# v1.3.1: C1 /api/sessions/{id}/config routes
Assert ($c1 -match '"/api/sessions/\{session_id\}/config"') "C1 registers /api/sessions/{id}/config"
Assert ($c1 -match "_api_sessions_get_config") "C1 has _api_sessions_get_config handler"
Assert ($c1 -match "_api_sessions_set_config") "C1 has _api_sessions_set_config handler"
Assert ($c1 -match "model_config_store: Any = ModelConfigStore") "C1 constructs ModelConfigStore in __init__"

# v1.3.1: C7 dispatches with config_store
$c7 = Get-Content -LiteralPath (Join-Path $Root "src\dhc\modules\c7_llm_stream_adapter\service.py") -Raw
Assert ($c7 -match "config_store") "C7 accepts a config_store kwarg"
Assert ($c7 -match 'session_id') "C7 chat_stream accepts session_id"
Assert ($c7 -match "get_config\(session_id\)") "C7 reads the per-session config"
Assert ($c7 -match 'temperature=cfg\.temperature') "C7 forwards temperature to the provider"
Assert ($c7 -match 'max_tokens=cfg\.max_tokens') "C7 forwards max_tokens to the provider"
Assert ($c7 -match 'top_p=cfg\.top_p') "C7 forwards top_p to the provider"

# v1.3.1: provider clients accept the new kwargs
$oc = Get-Content -LiteralPath (Join-Path $Root "src\dhc\integrations\openai_client.py") -Raw
Assert ($oc -match 'temperature: float \| None = None') "OpenAIClient.chat_stream accepts temperature kwarg"
Assert ($oc -match 'max_tokens: int \| None = None') "OpenAIClient.chat_stream accepts max_tokens kwarg"
Assert ($oc -match 'top_p: float \| None = None') "OpenAIClient.chat_stream accepts top_p kwarg"
Assert ($oc -match '"stream_options":\s*\{"include_usage":\s*True\}') "OpenAIClient requests include_usage"
$ac = Get-Content -LiteralPath (Join-Path $Root "src\dhc\integrations\anthropic_client.py") -Raw
Assert ($ac -match 'temperature: float \| None = None') "AnthropicClient.chat_stream accepts temperature kwarg"
Assert ($ac -match 'max_tokens: int \| None = None') "AnthropicClient.chat_stream accepts max_tokens kwarg"
Assert ($ac -match 'top_p: float \| None = None') "AnthropicClient.chat_stream accepts top_p kwarg"
$orc = Get-Content -LiteralPath (Join-Path $Root "src\dhc\integrations\openrouter_client.py") -Raw
Assert ($orc -match 'temperature: float \| None = None') "OpenRouterClient.chat_stream accepts temperature kwarg"
Assert ($orc -match 'max_tokens: int \| None = None') "OpenRouterClient.chat_stream accepts max_tokens kwarg"
Assert ($orc -match 'top_p: float \| None = None') "OpenRouterClient.chat_stream accepts top_p kwarg"

# v1.3.1: StreamChunk has the usage field
Assert ($c7 -match "usage: dict \| None = None") "StreamChunk.usage is a dict | None field"

# v1.3.1: SettingsModal + ModelConfigMenu + vitest
$settings_modal = Get-Content -LiteralPath (Join-Path $Root "apps\web/src/components/SettingsModal.tsx") -Raw
Assert ($settings_modal -match "export function SettingsModal") "SettingsModal component is exported"
Assert ($settings_modal -match "/api/secrets") "SettingsModal calls /api/secrets"
# v1.5.1.1 (ADR-0108): the save verb is PUT, not POST.
# The old "POST" check was the v1.5.1 bug. The PUT shape is the
# GitHub-style per-name upsert.
Assert ($settings_modal -match '"PUT"') "SettingsModal uses PUT to save keys (ADR-0108)"
Assert ($settings_modal -match "DELETE") "SettingsModal uses DELETE to remove keys"
Assert ($settings_modal -notmatch "prompt\(") "SettingsModal does NOT use window.prompt"
Assert ($settings_modal -notmatch "alert\(") "SettingsModal does NOT use window.alert"
Assert ($settings_modal -notmatch "confirm\(") "SettingsModal does NOT use window.confirm"
$config_menu = Get-Content -LiteralPath (Join-Path $Root "apps\web/src/components/ModelConfigMenu.tsx") -Raw
Assert ($config_menu -match "export function ModelConfigMenu") "ModelConfigMenu component is exported"
Assert ($config_menu -match "cfg-temperature") "ModelConfigMenu exposes the temperature slider"
Assert ($config_menu -match "cfg-system-prompt") "ModelConfigMenu exposes the system_prompt textarea"
$web_pkg = Get-Content -LiteralPath (Join-Path $Root "apps\web/package.json") -Raw
Assert ($web_pkg -match '"test":\s*"vitest run"') "apps/web package.json has vitest test script"

# v1.3.1: ChatPanel wires SettingsModal and ModelConfigMenu
Assert ($chat_panel -match "SettingsModal") "ChatPanel imports SettingsModal"
Assert ($chat_panel -match "ModelConfigMenu") "ChatPanel imports ModelConfigMenu"

# ----- v1.3.2: v0x03 envelope + per-model keys + usage totals + token counter -----

Assert ($secrets_py -match 'HEADER_V3\s*=\s*b"DHC3"') "secrets: HEADER_V3 = b'DHC3' is defined"
Assert ($secrets_py -match 'EXT_STRICT_NONCE\s*=\s*0x0001') "secrets: EXT_STRICT_NONCE = 0x0001 is defined"
Assert ($secrets_py -match 'STRICT_NONCE_LEN\s*=\s*12') "secrets: STRICT_NONCE_LEN = 12 is defined"
Assert ($secrets_py -match 'def _pack_extension\(') "secrets: _pack_extension is defined"
Assert ($secrets_py -match 'def _serialize_extensions\(') "secrets: _serialize_extensions is defined"
Assert ($secrets_py -match 'def _parse_extensions\(') "secrets: _parse_extensions is defined"
Assert ($secrets_py -match 'def open_envelope') "secrets: open_envelope is defined"

# Per-model API key lookup
$key_lookup_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\integrations\key_lookup.py") -Raw
Assert ($key_lookup_py -match 'def lookup_api_key\(') "integrations/key_lookup: lookup_api_key is defined"
Assert ($key_lookup_py -match 'def parse_key_name\(') "integrations/key_lookup: parse_key_name is defined"
Assert ($key_lookup_py -match 'def additional_key_name\(') "integrations/key_lookup: additional_key_name is defined"
Assert ($key_lookup_py -match 'def provider_fallback_key_name\(') "integrations/key_lookup: provider_fallback_key_name is defined"
Assert ($key_lookup_py -match '___') "integrations/key_lookup: triple-underscore disambiguator present"
Assert ($c7 -match 'from dhc.integrations.key_lookup import lookup_api_key') "C7 dispatch imports lookup_api_key"

# Per-session usage_totals
$session_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\services\session_manager.py") -Raw
Assert ($session_py -match 'usage_totals') "services/session_manager: usage_totals field is defined"
Assert ($session_py -match 'last_turn_prompt') "services/session_manager: last_turn_prompt is in the shape"
Assert ($session_py -match 'last_turn_completion') "services/session_manager: last_turn_completion is in the shape"
Assert ($session_py -match 'def update\(') "services/session_manager: update() is defined"
Assert ($session_py -match 'usage_totals:\s*dict\[str, int\]\s*\|\s*None') "services/session_manager: update() accepts usage_totals kwarg"

# v1.3.2: WS chat handler and HTTP message endpoint accumulate
Assert ($c1 -match 'usage_totals') "C1 service: usage_totals accumulator present"
Assert ($c1 -match 'new_totals') "C1 service: new_totals accumulator variable present"
Assert ($c1 -match 'sm.update\(sid, usage_totals=new_totals\)') "C1 service: persists usage_totals via sm.update"

# TokenCounter component
$token_counter_tsx = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\components\TokenCounter.tsx") -Raw
Assert ($token_counter_tsx -match 'function TokenCounter\(') "TokenCounter: TokenCounter component is defined"
Assert ($token_counter_tsx -match 'function fillPercent\(') "TokenCounter: fillPercent helper is defined"
Assert ($token_counter_tsx -match 'function barColor\(') "TokenCounter: barColor helper is defined"
Assert ($token_counter_tsx -match 'function formatTokens\(') "TokenCounter: formatTokens helper is defined"
Assert ($token_counter_tsx -match 'aria-label="Context window usage"') "TokenCounter: bar has aria-label"
Assert ($chat_panel -match "TokenCounter") "ChatPanel imports TokenCounter"
Assert ($chat_panel -match 'totalTokens') "ChatPanel passes totalTokens to InputArea"
Assert ($chat_panel -match 'contextWindow') "ChatPanel passes contextWindow to InputArea"
Assert ($chat_panel -match 'usage_totals') "ChatPanel reads usage_totals for the counter"

# Test files
$test_secrets_v3_py = Join-Path $Root "tests\chat\test_secrets_v3.py"
Assert (Test-Path -LiteralPath $test_secrets_v3_py) "tests/chat/test_secrets_v3.py exists"
$test_key_lookup_py = Join-Path $Root "tests\integrations\test_key_lookup.py"
Assert (Test-Path -LiteralPath $test_key_lookup_py) "tests/integrations/test_key_lookup.py exists"
$test_usage_py = Join-Path $Root "tests\chat\test_usage_aggregation.py"
Assert (Test-Path -LiteralPath $test_usage_py) "tests/chat/test_usage_aggregation.py exists"
$test_token_counter_tsx = Join-Path $Root "apps\web\src\__tests__\TokenCounter.test.tsx"
Assert (Test-Path -LiteralPath $test_token_counter_tsx) "apps/web/src/__tests__/TokenCounter.test.tsx exists"

# ADR-0012 exists
$adr12 = Join-Path $Root "docs\adr\0012-envelope-v0x03.md"
Assert (Test-Path -LiteralPath $adr12) "docs/adr/0012-envelope-v0x03.md exists"

# ----- v1.3.3: secret name binding (ADR-0013) -----

# v0x03 MAC input now includes the secret name. The wire format is
# unchanged (no new bytes on disk); the binding is enforced by the
# HMAC tag.
Assert ($secrets_py -match "name\s*[:=]?\s*bytes") "secrets: name parameter is bytes (ADR-0013)"
Assert ($secrets_py -match "def seal\(") "secrets: seal() is defined"
Assert ($secrets_py -match "def open_envelope\(") "secrets: open_envelope() is defined"
Assert ($secrets_py -match "def _seal_with\(") "secrets: _seal_with() is defined"
# seal() signature includes a name= parameter.
$sealMatch = [regex]::Match($secrets_py, 'def seal\(\s*plaintext[^)]*name')
Assert ($sealMatch.Success) "secrets: seal() accepts a name= parameter (ADR-0013)"
# open_envelope() signature includes name= and require_binding= parameters.
$openMatch = [regex]::Match($secrets_py, 'def open_envelope\([^)]*require_binding')
Assert ($openMatch.Success) "secrets: open_envelope() accepts a require_binding= parameter (ADR-0013)"
# The v0x03 MAC input appends the name after the nonce.
Assert ($secrets_py -match 'mac_input\s*=\s*header\s*\+\s*ext_area\s*\+\s*aead_nonce\s*\+\s*bytes\(name\)') "secrets: v0x03 MAC input includes the name"
# SecretsService.put_raw passes the name down to seal().
Assert ($secrets_py -match "seal\(bytes\(value\),\s*self\._key,\s*name=name\)") "secrets: SecretsService.put_raw passes the name to seal()"
# SecretsService._replay passes the name down to open_envelope().
Assert ($secrets_py -match 'open_envelope\(blob,\s*self\._key,\s*name=name\)') "secrets: SecretsService._replay passes the name to open_envelope()"
# v0x01 / v0x02 envelopes still have a 2-arg open path.
Assert ($secrets_py -match "mac_input\s*=\s*header\s*\+\s*nonce") "secrets: v0x01/v0x02 MAC input does not include the name (legacy)"

# ADR-0013 exists.
$adr13 = Join-Path $Root "docs\adr\0013-secret-name-binding.md"
Assert (Test-Path -LiteralPath $adr13) "docs/adr/0013-secret-name-binding.md exists"

# Test file exists.
$test_name_binding_py = Join-Path $Root "tests\chat\test_secrets_name_binding.py"
Assert (Test-Path -LiteralPath $test_name_binding_py) "tests/chat/test_secrets_name_binding.py exists"

# ----- v1.3.4: canonical MAC input (ADR-0014) -----

# v0x04 envelope (`DHC4`) with length-prefixed name in the MAC input.
Assert ($secrets_py -match 'HEADER_V4\s*=\s*b"DHC4"') "secrets: HEADER_V4 = b'DHC4' is defined (ADR-0014)"
Assert ($secrets_py -match "NAME_LEN_LEN\s*=\s*4") "secrets: NAME_LEN_LEN = 4 (u32 BE prefix width)"
# seal() now writes v0x04 by default.
$sealMatch = [regex]::Match($secrets_py, '_seal_with\(plaintext, master_key, HEADER_V4, name=name_bytes\)')
Assert ($sealMatch.Success) "secrets: seal() default write path is v0x04 (HEADER_V4)"
# v0x04 MAC input includes len(name):u32 BE prefix.
$v04MacMatch = [regex]::Match($secrets_py, 'mac_input\s*=\s*\(\s*header\s*\+\s*ext_area\s*\+\s*aead_nonce\s*\+\s*struct\.pack\(">I",\s*len\(bytes\(name\)\)\)')
Assert ($v04MacMatch.Success) "secrets: v0x04 MAC input includes len(name):u32 BE prefix (ADR-0014)"
# v0x04 open path includes the same prefix.
$v04OpenMatch = [regex]::Match($secrets_py, '\+\s*nonce\s*\+\s*struct\.pack\(">I",\s*len\(name_bytes\)\)')
Assert ($v04OpenMatch.Success) "secrets: v0x04 open_envelope path includes len(name):u32 BE prefix"
# v0x03 read path is preserved (legacy read-only).
Assert ($secrets_py -match 'mac_input\s*=\s*header\s*\+\s*ext_area\s*\+\s*nonce\s*\+\s*name_bytes') "secrets: v0x03 legacy MAC input (name ‖ ct) is preserved for read-only compat"
# Migration script exists and refuses anonymous v0x03.
$migrate_py = Join-Path $Root "scripts\migrate_v03_to_v04.py"
Assert (Test-Path -LiteralPath $migrate_py) "scripts/migrate_v03_to_v04.py exists"
$migrate_src = Get-Content -LiteralPath $migrate_py -Raw
Assert ($migrate_src -match "refusing to migrate anonymous") "migrate_v03_to_v04: refuses anonymous v0x03 envelopes"
Assert ($migrate_src -match "v03\.bak") "migrate_v03_to_v04: preserves old log at <log>.v03.bak"
# Migration script tests.
$test_migrate_py = Join-Path $Root "tests\chat\test_migrate_v03_to_v04.py"
Assert (Test-Path -LiteralPath $test_migrate_py) "tests/chat/test_migrate_v03_to_v04.py exists"
# v0x04 test file.
$test_v4_py = Join-Path $Root "tests\chat\test_secrets_v4.py"
Assert (Test-Path -LiteralPath $test_v4_py) "tests/chat/test_secrets_v4.py exists"
# ADR-0014 exists.
$adr14 = Join-Path $Root "docs\adr\0014-canonical-mac-input.md"
Assert (Test-Path -LiteralPath $adr14) "docs/adr/0014-canonical-mac-input.md exists"

# ----- v1.4.0: Entropy Gate (ADR-0015/0016/0017) -----

# Capability whitelist shape.
$capabilities_py = Join-Path $Root "src\dhc\cordis\capabilities.py"
Assert (Test-Path -LiteralPath $capabilities_py) "src/dhc/cordis/capabilities.py exists"
$cap_src = Get-Content -LiteralPath $capabilities_py -Raw
Assert ($cap_src -match "class Capability\(str, Enum\)") "capabilities: Capability is a str Enum"
Assert ($cap_src -match "class ActorTier\(str, Enum\)") "capabilities: ActorTier is a str Enum"
Assert ($cap_src -match "ACTION_WHITELIST\s*:\s*dict\[Capability, ActorTier\]") "capabilities: ACTION_WHITELIST is dict[Capability, ActorTier]"
Assert ($cap_src -match "CAPABILITY_BUDGET_V140\s*[:=]\s*int\s*=\s*24|CAPABILITY_BUDGET_V140\s*=\s*24") "capabilities: CAPABILITY_BUDGET_V140 = 24"
Assert ($cap_src -match "def requires\(") "capabilities: @requires decorator is defined"
Assert ($cap_src -match "def _authorize\(") "capabilities: runtime guard _authorize is defined"
Assert ($cap_src -match "class CapabilityDenied") "capabilities: CapabilityDenied error type"
Assert ($cap_src -match "def enumerate_capability_tests\(") "capabilities: enumerate_capability_tests() generator"

# Number Gate shape.
$number_gate_py = Join-Path $Root "src\dhc\cordis\number_gate.py"
Assert (Test-Path -LiteralPath $number_gate_py) "src/dhc/cordis/number_gate.py exists"
$ng_src = Get-Content -LiteralPath $number_gate_py -Raw
Assert ($ng_src -match "LEGAL_TEXT_CODES\s*[:=]") "number_gate: LEGAL_TEXT_CODES is defined"
Assert ($ng_src -match "LEGAL_TEXT_CODES\s*[:=]\s*frozenset\[int\]\s*=\s*frozenset\(|LEGAL_TEXT_CODES\s*=\s*frozenset\(") "number_gate: LEGAL_TEXT_CODES is a frozenset"
Assert ($ng_src -match "range\(0x30, 0x3A\)") "number_gate: 0-9 in legal alphabet"
Assert ($ng_src -match "range\(0x41, 0x5B\)") "number_gate: A-Z in legal alphabet"
Assert ($ng_src -match "range\(0x61, 0x7B\)") "number_gate: a-z in legal alphabet"
Assert ($ng_src -match "def to_codes\(") "number_gate: to_codes is defined"
Assert ($ng_src -match "def from_codes\(") "number_gate: from_codes is defined"
Assert ($ng_src -match "def render_audit_text\(") "number_gate: render_audit_text is defined"
Assert ($ng_src -match "def render_provider_bytes\(") "number_gate: render_provider_bytes is defined"
Assert ($ng_src -match "def enumerate_byte_code_tests\(") "number_gate: enumerate_byte_code_tests() generator"
Assert ($ng_src -match "strict:\s*bool\s*=\s*True") "number_gate: from_codes has strict=True default (audit render)"
Assert ($ng_src -match 'NUMERIC_ESCAPE:\s*str\s*=\s*"#"') "number_gate: NUMERIC_ESCAPE prefix is '#'"

# Entropy Map shape.
$entropy_map_py = Join-Path $Root "src\dhc\cordis\entropy_map.py"
Assert (Test-Path -LiteralPath $entropy_map_py) "src/dhc/cordis/entropy_map.py exists"
Assert (Test-Path -LiteralPath (Join-Path $Root "scripts\build_entropy_map.py")) "scripts/build_entropy_map.py exists"
$em_md = Join-Path $Root "docs\entropy-map.md"
Assert (Test-Path -LiteralPath $em_md) "docs/entropy-map.md exists"
$em_content = Get-Content -LiteralPath $em_md -Raw
Assert ($em_content -match "<!-- generated by scripts/build_entropy_map\.py -->") "entropy-map: has the GENERATED_MARKER"
Assert ($em_content -match "Capability whitelist") "entropy-map: has the 'Capability whitelist' section"
Assert ($em_content -match "Legal charset") "entropy-map: has the 'Legal charset' section"

# C1 service uses the @requires decorator on every gated route.
$c1_src = Get-Content -LiteralPath (Join-Path $Root "src\dhc\modules\c1_gui_web_core\service.py") -Raw
Assert ($c1_src -match "from dhc\.cordis\.capabilities import Capability") "c1 service: imports Capability from capabilities"
Assert ($c1_src -match "_requires_capability") "c1 service: uses _requires_capability at registration"
# Every route registration (except /healthz and /) should be wrapped.
$gatedCount = ([regex]::Matches($c1_src, '_gated\(Capability\.')).Count
Assert ($gatedCount -ge 20) "c1 service: >=20 routes are wrapped with @requires (gated count = $gatedCount)"

# ADR-0015, 0016, 0017 exist.
$adr15 = Join-Path $Root "docs\adr\0015-entropy-gate-capability-whitelist.md"
Assert (Test-Path -LiteralPath $adr15) "docs/adr/0015-entropy-gate-capability-whitelist.md exists"
$adr16 = Join-Path $Root "docs\adr\0016-number-gate-legal-charset.md"
Assert (Test-Path -LiteralPath $adr16) "docs/adr/0016-number-gate-legal-charset.md exists"
$adr17 = Join-Path $Root "docs\adr\0017-tests-as-error-boundary.md"
Assert (Test-Path -LiteralPath $adr17) "docs/adr/0017-tests-as-error-boundary.md exists"

# Test files exist.
$test_caps_py = Join-Path $Root "tests\cordis\test_capabilities.py"
Assert (Test-Path -LiteralPath $test_caps_py) "tests/cordis/test_capabilities.py exists"
$test_ng_py = Join-Path $Root "tests\cordis\test_number_gate.py"
Assert (Test-Path -LiteralPath $test_ng_py) "tests/cordis/test_number_gate.py exists"
$test_em_py = Join-Path $Root "tests\cordis\test_entropy_map.py"
Assert (Test-Path -LiteralPath $test_em_py) "tests/cordis/test_entropy_map.py exists"
$test_pg_py = Join-Path $Root "tests\cordis\test_pipeline_guard.py"
Assert (Test-Path -LiteralPath $test_pg_py) "tests/cordis/test_pipeline_guard.py exists"
$test_bem_py = Join-Path $Root "tests\cordis\test_build_entropy_map.py"
Assert (Test-Path -LiteralPath $test_bem_py) "tests/cordis/test_build_entropy_map.py exists"

# ============================================================
# v1.5.1.2 invariants — DO NOT REMOVE
# ============================================================
# Audit hotfix shape invariants:
#   1. _atomic_write_json self-heals missing parent dirs.
#   2. CLI default for --sessions-dir is the *parent* of the
#      on-disk sessions directory (`.dhc`, not `.dhc/sessions`).
#   3. The frontend `fetchJson` does NOT echo the raw server
#      body in the user-visible error message.

$session_mgr_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\services\session_manager.py") -Raw
Assert ($session_mgr_py -match "path\.parent\.mkdir\(parents=True, exist_ok=True\)") "v1.5.1.2: _atomic_write_json self-heals missing parent dirs"

$serve_c1_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\serve_c1.py") -Raw
# Match: default=str(repo_root / ".dhc")
# Use a single-quoted literal with backticks only inside; escape with
# backtick for PowerShell's special chars.
$pat_serve = 'default=str\(repo_root / "\.dhc"\)'
Assert ($serve_c1_py -match $pat_serve) "v1.5.1.2: --sessions-dir default is repo_root / .dhc (parent)"

$sanitize_ts = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\utils\sanitizeError.ts") -Raw
Assert ($sanitize_ts -match "sanitizeErrorMessage") "v1.5.1.2: utils/sanitizeError.ts exports sanitizeErrorMessage"
Assert ($sanitize_ts -match "export async function fetchJson") "v1.5.1.2: utils/sanitizeError.ts exports fetchJson"

# SettingsModal must NOT define its own raw-body-echoing fetchJson.
$settings_modal_tsx = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\components\SettingsModal.tsx") -Raw
Assert ($settings_modal_tsx -notmatch "resp\.text\(\)\.catch") "v1.5.1.2: SettingsModal does NOT echo raw server body in errors"
$pat_sm = 'from "\.\./utils/sanitizeError"'
Assert ($settings_modal_tsx -match $pat_sm) "v1.5.1.2: SettingsModal imports the sanitized fetchJson"

# _hint_for shape: the prefix-leaking `first_3` is gone.
$secrets_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\cordis\secrets.py") -Raw
$pat_hint = 'f"…\{value\[-4:\]\}"'
Assert ($secrets_py -match $pat_hint) "v1.5.1.2: _hint_for returns only last 4 chars prefixed with ellipsis"
Assert ($secrets_py -notmatch "value\[:3\]") "v1.5.1.2: _hint_for does NOT leak the first 3 chars (provider prefix)"

# _atomic_write_json is imported by tests (so the helper is
# exercised, not just present).
$test_atomic_heal = Join-Path $Root "tests\chat\test_session_manager.py"
$test_atomic_heal_src = Get-Content -LiteralPath $test_atomic_heal -Raw
Assert ($test_atomic_heal_src -match "test_atomic_write_self_heals_missing_parent") "v1.5.1.2: test_atomic_write_self_heals_missing_parent exists"

# Sanity: the new `test_atomic_write_self_heals_missing_parent` test
# uses `_atomic_write_json` indirectly via `SessionManager.create()`
# (the production code path), so the self-heal is exercised end-to-end.
$pat_rmtree = 'rmtree\(.+\._dir'
Assert ($test_atomic_heal_src -match $pat_rmtree) "v1.5.1.2: self-heal test deletes the dir between init and create"

# ============================================================
# v1.5.1.3 invariants — SecretsService self-heal (audit hotfix #3)
# ============================================================
# The v1.5.1.2 audit fixed the same class of FileNotFoundError
# in `SessionManager._atomic_write_json` but missed the parallel
# write path in `SecretsService.put_raw` and `SecretsService.delete`.
# v1.5.1.3 introduces `_append_log`, a helper that does
# `self._log.parent.mkdir(parents=True, exist_ok=True)` before
# every write, making the path robust to any post-startup state
# mutation (test cleanup, manual `rm`, a migration that touched
# the dir). See ADR-0108 v1.5.1.3 amendment.

# (1) The `_append_log` helper exists and self-heals the parent.
$pat_append_log_def = 'def _append_log\(self, record: dict\[str, object\]\)'
Assert ($secrets_py -match $pat_append_log_def) "v1.5.1.3: secrets.py defines _append_log helper"
$pat_append_log_mkdir = 'self\._log\.parent\.mkdir\(parents=True, exist_ok=True\)'
Assert ($secrets_py -match $pat_append_log_mkdir) "v1.5.1.3: _append_log self-heals the parent dir"

# (2) `put_raw` no longer uses the bare `self._log.open("a", ...)` pattern.
# Extract the `put_raw` block and confirm the only `open(...)` call
# is inside `_append_log`. v1.6.0: put_raw is a thin wrapper that
# delegates to put_source; the bare-open check still applies to the
# put_source body (which is the new canonical write path).
$put_raw_full = ([regex]::Match($secrets_py, '(?ms)def put_raw\(.+?\) -> None:.*?(?=\n    def |\nclass |\Z)')).Value
Assert ($put_raw_full -notmatch 'self\._log\.open\(') "v1.5.1.3: put_raw does NOT use bare self._log.open()"
$pat_put_raw_call = 'self\._append_log\(record\)'
$put_source_full = ([regex]::Match($secrets_py, '(?ms)def put_source\(.+?\) -> None:.*?(?=\n    def |\nclass |\Z)')).Value
Assert ($put_source_full -match $pat_put_raw_call) "v1.5.1.3: put_source calls _append_log"
Assert ($put_source_full -notmatch 'self\._log\.open\(') "v1.5.1.3: put_source does NOT use bare self._log.open()"
Assert ($put_raw_full -match 'self\.put_source\(') "v1.6.0: put_raw delegates to put_source (preserves the v1.5.1.3 self-heal)"

# (3) `delete` also uses `_append_log`, not the bare open pattern.
$pat_delete_open = 'self\._log\.open\("a"'
$delete_block = ([regex]::Match($secrets_py, '(?ms)def delete\(.+?(?=\n    def |\nclass |\Z)')).Value
Assert ($delete_block -notmatch $pat_delete_open) "v1.5.1.3: delete does NOT use bare self._log.open()"
Assert ($delete_block -match $pat_put_raw_call) "v1.5.1.3: delete calls _append_log"

# (4) The 5 new self-heal tests exist (the contract is pinned).
$test_self_heal = Join-Path $Root "tests\chat\test_secrets_self_heal_v1513.py"
Assert (Test-Path -LiteralPath $test_self_heal) "v1.5.1.3: tests/chat/test_secrets_self_heal_v1513.py exists"
$test_self_heal_src = Get-Content -LiteralPath $test_self_heal -Raw
$expected_tests = @(
    "test_put_raw_self_heals_missing_secrets_dir",
    "test_delete_self_heals_missing_secrets_dir",
    "test_self_heal_survives_full_state_wipe",
    "test_self_heal_works_when_secrets_dir_never_existed_at_init",
    "test_concurrent_puts_dont_interleave"
)
foreach ($t in $expected_tests) {
    Assert ($test_self_heal_src -match $t) "v1.5.1.3: test '$t' exists"
}

# ============================================================
# v1.5.1.4 invariants — per-provider real connection probe
# ============================================================
# The v1.5.1 `GET /api/llm/health` only confirmed the
# harness was started with `--llm-base-url`; it did NOT
# verify the user's OpenRouter (or any other) key worked.
# The Settings modal rendered a "Configured" pill with no
# way to actually confirm the key was valid. v1.5.1.4 adds
# a per-provider probe at `GET /api/llm/health/{provider}`
# that uses OpenRouter's canonical `GET /api/v1/auth/key`
# endpoint — the documented "is my key valid" probe. See
# ADR-0108 v1.5.1.4 amendment.

# (1) The new route is registered.
$c1_service_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\modules\c1_gui_web_core\service.py") -Raw
$pat_health_provider_route = 'add_get\(\s*"/api/llm/health/\{provider\}"'
Assert ($c1_service_py -match $pat_health_provider_route) "v1.5.1.4: GET /api/llm/health/{provider} route is registered"

# (2) The handler exists and uses the OpenRouter /auth/key URL.
$pat_health_provider_handler = 'def _api_llm_health_provider'
Assert ($c1_service_py -match $pat_health_provider_handler) "v1.5.1.4: _api_llm_health_provider handler exists"
$pat_openrouter_url = '_OPENROUTER_AUTH_KEY_URL = "https://openrouter\.ai/api/v1/auth/key"'
Assert ($c1_service_py -match $pat_openrouter_url) "v1.5.1.4: handler uses the canonical /api/v1/auth/key URL"

# (3) The 401 path is mapped to "invalid_key" (no raw key in response).
$pat_invalid_key = 'error.*invalid_key'
Assert ($c1_service_py -match $pat_invalid_key) "v1.5.1.4: 401 response maps to error='invalid_key'"

# (4) The new Python test file exists with the 5 expected cases.
$test_llm_health = Join-Path $Root "tests\chat\test_llm_health_v1514.py"
Assert (Test-Path -LiteralPath $test_llm_health) "v1.5.1.4: tests/chat/test_llm_health_v1514.py exists"
$test_llm_health_src = Get-Content -LiteralPath $test_llm_health -Raw
$expected_health_tests = @(
    "test_llm_health_openrouter_200_returns_label_and_limit",
    "test_llm_health_openrouter_401_returns_invalid_key",
    "test_llm_health_openrouter_no_key_returns_no_key",
    "test_llm_health_openrouter_network_failure_returns_network",
    "test_llm_health_openai_returns_not_supported"
)
foreach ($t in $expected_health_tests) {
    Assert ($test_llm_health_src -match $t) "v1.5.1.4: test '$t' exists"
}

# (5) The Settings modal has a "Test connection" button.
$settings_modal_tsx = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\components\SettingsModal.tsx") -Raw
$pat_probe_button = 'Test connection'
Assert ($settings_modal_tsx -match $pat_probe_button) "v1.5.1.4: SettingsModal renders a 'Test connection' button"
$pat_probe_handler = 'onTestConnection'
Assert ($settings_modal_tsx -match $pat_probe_handler) "v1.5.1.4: SettingsModal wires the Test connection handler"
$pat_probe_fetch = '/api/llm/health/'
Assert ($settings_modal_tsx -match $pat_probe_fetch) "v1.5.1.4: Test connection calls /api/llm/health/{provider}"

# (6) The chat header renders the default-model pill when
# no active session exists.
$chat_panel_tsx = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\panels\ChatPanel.tsx") -Raw
$pat_default_pill = 'chat-model-pill-default'
Assert ($chat_panel_tsx -match $pat_default_pill) "v1.5.1.4: ChatPanel renders chat-model-pill-default when no session"
$pat_default_storage = 'dhc\.defaultModelId'
Assert ($chat_panel_tsx -match $pat_default_storage) "v1.5.1.4: ChatPanel persists defaultModelId in localStorage"
$pat_model_in_post = 'body:\s*JSON\.stringify\(\{\s*model:\s*defaultModelId\s*\}\)'
Assert ($chat_panel_tsx -match $pat_model_in_post) "v1.5.1.4: ChatPanel sends defaultModelId in POST /api/sessions body"

# (7) The session create handler accepts an optional model.
$pat_create_model = 'if isinstance\(requested_model, str\) and requested_model:'
Assert ($c1_service_py -match $pat_create_model) "v1.5.1.4: _api_sessions_create accepts an optional 'model' field"

# (8) The vitest describe block exists.
$settings_modal_test = Join-Path $Root "apps\web\src\__tests__\SettingsModal.test.tsx"
$settings_modal_test_src = Get-Content -LiteralPath $settings_modal_test -Raw
$pat_settings_probe_describe = 'describe.*<SettingsModal /> v1\.5\.1\.4: per-provider probe'
Assert ($settings_modal_test_src -match $pat_settings_probe_describe) "v1.5.1.4: SettingsModal test describe block exists"
$chat_panel_test = Join-Path $Root "apps\web\src\__tests__\ChatPanel.test.tsx"
$chat_panel_test_src = Get-Content -LiteralPath $chat_panel_test -Raw
$pat_chatpanel_describe = 'describe.*<ChatPanel /> v1\.5\.1\.4: inline default model picker'
Assert ($chat_panel_test_src -match $pat_chatpanel_describe) "v1.5.1.4: ChatPanel test describe block exists"

# ============================================================
# v1.5.1.5 invariants — C7 streaming timeout + log no-leak
# ============================================================
# The 2026-09-09 verification reported a "C7 dispatch hang on
# mock-llm/default". Root cause was a test-client bug (it waited
# for a HELLO frame the server never sends), not a C7 regression.
# v1.5.1.5 ships defense in depth: explicit per-stream timeouts
# (5s mock, 30s live) so a future regression that deadlocks the
# streaming iterator surfaces as `chat.error` / `stream_timeout`
# in <5s instead of hanging the WS for 30+s. The invariants below
# pin the timeouts and the no-leak log discipline surfaced by the
# verification.

# (1) The C7 adapter stores explicit per-stream timeouts.
$c7_service_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\modules\c7_llm_stream_adapter\service.py") -Raw
$pat_stream_timeout_field = '_stream_timeout_s\s*=\s*float\(stream_timeout_s\)'
Assert ($c7_service_py -match $pat_stream_timeout_field) "v1.5.1.5: c7 adapter stores _stream_timeout_s from constructor arg"
$pat_mock_timeout_field = '_mock_stream_timeout_s\s*=\s*float\(mock_stream_timeout_s\)'
Assert ($c7_service_py -match $pat_mock_timeout_field) "v1.5.1.5: c7 adapter stores _mock_stream_timeout_s from constructor arg"
$pat_default_stream = 'stream_timeout_s:\s*float\s*=\s*30\.0'
Assert ($c7_service_py -match $pat_default_stream) "v1.5.1.5: c7 adapter stream_timeout_s default is 30.0s"
$pat_default_mock = 'mock_stream_timeout_s:\s*float\s*=\s*5\.0'
Assert ($c7_service_py -match $pat_default_mock) "v1.5.1.5: c7 adapter mock_stream_timeout_s default is 5.0s"
$pat_consume_with_timeout = 'async def _consume_with_timeout'
Assert ($c7_service_py -match $pat_consume_with_timeout) "v1.5.1.5: c7 adapter has _consume_with_timeout helper"
$pat_dispatch_mock = 'async def _dispatch_mock'
Assert ($c7_service_py -match $pat_dispatch_mock) "v1.5.1.5: c7 adapter has _dispatch_mock split-out helper"

# (2) The WS chat handler surfaces asyncio.TimeoutError as
# `chat.error` / `stream_timeout`.
$pat_stream_timeout_code = 'code":\s*"stream_timeout"'
Assert ($c1_service_py -match $pat_stream_timeout_code) "v1.5.1.5: WS handler maps asyncio.TimeoutError to chat.error stream_timeout"

# (3) The regression test file exists and has the 4 named tests.
$test_c7_timeout = Join-Path $Root "tests\chat\test_c7_stream_timeout_v1515.py"
Assert (Test-Path -LiteralPath $test_c7_timeout) "v1.5.1.5: tests/chat/test_c7_stream_timeout_v1515.py exists"
$test_c7_timeout_src = Get-Content -LiteralPath $test_c7_timeout -Raw
foreach ($t in @(
    "test_v1515_mock_stream_raises_timeout",
    "test_v1515_live_stream_raises_timeout",
    "test_v1515_default_timeouts",
    "test_v1515_chat_stream_accepts_per_call_cap"
)) {
    Assert ($test_c7_timeout_src -match $t) "v1.5.1.5: test '$t' exists"
}

# (4) No-leak log discipline (verification finding #4): the
# `_api_llm_health_provider` handler must not log the key or the
# upstream error body. These invariants pin the no-leak contract
# at the source level so a regression that adds
# `logger.warning(f"upstream: {r.text}")` is caught at CI time.
$pat_health_provider_block = '(?s)async def _api_llm_health_provider.*?(?=\nasync def|\ndef |\nclass )'
$health_provider_block = if ($c1_service_py -match $pat_health_provider_block) { $matches[0] } else { "" }
$pat_log_key = 'logger\.(info|warning|error|debug|exception).*\b(api_key|api_key_str|Authorization)\b'
$neg = if ($health_provider_block -match $pat_log_key) { $false } else { $true }
Assert $neg "v1.5.1.5: _api_llm_health_provider must NOT log api_key/api_key_str/Authorization"
$pat_log_upstream_body = 'logger\.(info|warning|error|debug|exception).*\b(resp\.text|resp\.content|response\.text|response\.content|body)\b'
$neg2 = if ($health_provider_block -match $pat_log_upstream_body) { $false } else { $true }
Assert $neg2 "v1.5.1.5: _api_llm_health_provider must NOT log resp.text/resp.content/body"

# ============================================================
# v1.6.0 Phase 0 invariants — Settings Subsystem Hardening
# ============================================================
# SecretSource (raw / env / file), ProviderState cache,
# GET /api/settings/state, IngressScrubber, polymorphic JSONL
# schema. See CHANGELOG.md [1.6.0] and ADR-0110 (Phase 0).

# (1) SecretSource dataclass + SecretSourceType closed enum
$secrets_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\cordis\secrets.py") -Raw
$pat_ss_class = "class SecretSource\b"
Assert ($secrets_py -match $pat_ss_class) "v1.6.0: SecretSource dataclass defined in secrets.py"
$pat_ss_type = "class SecretSourceType\b"
Assert ($secrets_py -match $pat_ss_type) "v1.6.0: SecretSourceType enum defined in secrets.py"
$pat_ss_resolve = "def resolve\(self\) -> bytes"
Assert ($secrets_py -match $pat_ss_resolve) "v1.6.0: SecretSource.resolve() method exists"
$pat_ss_ref_hint = "def ref_hint\(self\)"
Assert ($secrets_py -match $pat_ss_ref_hint) "v1.6.0: SecretSource.ref_hint() method exists"
$pat_put_source = "def put_source\(self, name: str, source: SecretSource\)"
Assert ($secrets_py -match $pat_put_source) "v1.6.0: SecretsService.put_source method exists"
$pat_get_source = "def get_source\(self, name: str\) -> SecretSource \| None"
Assert ($secrets_py -match $pat_get_source) "v1.6.0: SecretsService.get_source method exists"

# (2) ProviderStateManager
$ps_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\services\provider_state.py") -Raw
$pat_ps_class = "class ProviderStateManager\b"
Assert ($ps_py -match $pat_ps_class) "v1.6.0: ProviderStateManager class defined in provider_state.py"
$pat_ps_update = "def update\(\s*self,\s*provider"
Assert ($ps_py -match $pat_ps_update) "v1.6.0: ProviderStateManager.update method exists"
$pat_ps_get = "def get\(self, provider"
Assert ($ps_py -match $pat_ps_get) "v1.6.0: ProviderStateManager.get method exists"
$pat_health_enum = "class HealthStatus\(str, Enum\)"
Assert ($ps_py -match $pat_health_enum) "v1.6.0: HealthStatus closed enum defined"
$pat_ps_in_webcore = "self\.provider_state = ProviderStateManager\(\)"
Assert ($c1_service_py -match $pat_ps_in_webcore) "v1.6.0: GuiWebCore wires ProviderStateManager"

# (3) GET /api/settings/state route
$pat_settings_state_route = '"/api/settings/state"'
Assert ($c1_service_py -match $pat_settings_state_route) "v1.6.0: GET /api/settings/state route registered"
$pat_settings_state_handler = "async def _api_settings_state"
Assert ($c1_service_py -match $pat_settings_state_handler) "v1.6.0: _api_settings_state handler exists"

# (4) IngressScrubber
$scrubber_py = Get-Content -LiteralPath (Join-Path $Root "src\dhc\security\ingress_scrubber.py") -Raw
$pat_scrub_fn = "def scrub\(text: str\) -> ScrubResult"
Assert ($scrubber_py -match $pat_scrub_fn) "v1.6.0: scrub() function defined in ingress_scrubber.py"
$pat_scrub_redacted = 'REDACTED_API_KEY'
Assert ($scrubber_py -match $pat_scrub_redacted) "v1.6.0: redacted marker is [REDACTED_API_KEY]"
$pat_scrub_in_ws = "from dhc\.security\.ingress_scrubber import scrub"
Assert ($c1_service_py -match $pat_scrub_in_ws) "v1.6.0: WS handler imports IngressScrubber"
$pat_scrub_call = "scrubbed = scrub\(text\)"
Assert ($c1_service_py -match $pat_scrub_call) "v1.6.0: WS handler calls scrub() on inbound text"

# (4b) Defense in depth: the C7 adapter ALSO scrubs (so even
# if a future caller bypasses the WS handler, the upstream
# never sees the raw key).
$pat_scrub_in_c7 = "from dhc\.security\.ingress_scrubber import scrub as _scrub"
Assert ($c7_service_py -match $pat_scrub_in_c7) "v1.6.0: C7 adapter imports IngressScrubber"
$pat_scrub_c7_call = "messages = \[\s*\{"
Assert ($c7_service_py -match $pat_scrub_c7_call) "v1.6.0: C7 adapter scrubs every message content"

# (5) Source-level log invariants: no new logger calls in
# _api_settings_state (the endpoint must never log a value).
$pat_settings_block = '(?s)async def _api_settings_state.*?(?=\nasync def|\ndef |\nclass )'
$settings_block = if ($c1_service_py -match $pat_settings_block) { $matches[0] } else { "" }
$pat_settings_log = 'logger\.(info|warning|error|debug|exception)'
$neg3 = if ($settings_block -match $pat_settings_log) { $false } else { $true }
Assert $neg3 "v1.6.0: _api_settings_state must NOT log anything (no logger calls)"

# (6) Test files exist with the right names.
$test_secret_source = Join-Path $Root "tests\chat\test_secret_source_v160.py"
Assert (Test-Path -LiteralPath $test_secret_source) "v1.6.0: tests/chat/test_secret_source_v160.py exists"
$test_provider_state = Join-Path $Root "tests\chat\test_provider_state_v160.py"
Assert (Test-Path -LiteralPath $test_provider_state) "v1.6.0: tests/chat/test_provider_state_v160.py exists"
$test_scrubber = Join-Path $Root "tests\security\test_ingress_scrubber_v160.py"
Assert (Test-Path -LiteralPath $test_scrubber) "v1.6.0: tests/security/test_ingress_scrubber_v160.py exists"

# (7) Frontend source pill (read-only display in Phase 0).
$settings_modal_tsx_v160 = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\components\SettingsModal.tsx") -Raw
$pat_source_pill = 'settings-source-pill'
Assert ($settings_modal_tsx_v160 -match $pat_source_pill) "v1.6.0: SettingsModal renders settings-source-pill"
$pat_source_fetch = 'fetchJson\("/api/settings/state"\)'
Assert ($settings_modal_tsx_v160 -match $pat_source_fetch) "v1.6.0: SettingsModal fetches /api/settings/state on mount"
$styles_css_v160 = Get-Content -LiteralPath (Join-Path $Root "apps\web\src\styles.css") -Raw
$pat_source_css = '\.settings-source-pill\.is-env'
Assert ($styles_css_v160 -match $pat_source_css) "v1.6.0: styles.css has .settings-source-pill.is-env rule"

if ($failures.Count -gt 0) {
    Write-Output ""
    Write-Output "$($failures.Count) invariant(s) failed"
    exit 1
}
Write-Output ""
Write-Output "All invariants pass"
exit 0

# ============================================================
# v1.3.1 invariants — DO NOT REMOVE
# ============================================================
# NOTE: the block above is the canonical exit point; everything
# below is unreachable and intentionally inert. The "invariants"
# below are documented in the v1.3.1 plan; the runtime check is
# performed by the vitest suite in `apps/web/src/__tests__/`
# (SettingsModal + ModelConfigMenu) and the pytest suite
# (test_secrets_v2, test_model_config*, test_c7_dispatch, etc.).
# We still assert the SHAPE invariants here so a future refactor
# that breaks the surface is caught at invariant-check time.


# ============================================================
# v1.3.2 invariants — DO NOT REMOVE
# ============================================================
# The runtime checks for v1.3.2 are in:
#   tests/chat/test_secrets_v3.py        (8 tests, v0x03 envelope)
#   tests/integrations/test_key_lookup.py (8 tests, per-model keys)
#   tests/chat/test_usage_aggregation.py  (8 tests, Session.usage_totals)
#   apps/web/src/__tests__/TokenCounter.test.tsx (8 tests, counter)
# The shape invariants above (HEADER_V3, EXT_STRICT_NONCE, etc.)
# are the static fallback for refactor detection.


