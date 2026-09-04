$ErrorActionPreference = "Continue"
$repo = "C:\Users\Rex\.config\opencode\harness_benchmark"
$py = "C:\Users\Rex\.config\opencode\python-runtime\python-3.14.7\python.exe"
# Auto-pick the most recent ship zip. Each new ship replaces the
# previous one; the test should be against the current target.
$relayDir = Join-Path $repo "relay"
$zip = Get-ChildItem -LiteralPath $relayDir -Filter "harness_benchmark-v*.zip" |
    Where-Object { $_.Name -notmatch "MANIFEST" } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1 -ExpandProperty FullName
if (-not $zip) { throw "No ship zip found in $relayDir" }

$results = @()

function Pass($name) { $script:results += [pscustomobject]@{name=$name; status="PASS"} }
function Fail($name, $detail) { $script:results += [pscustomobject]@{name=$name; status="FAIL: $detail"} }

# 1. 834 tests (v1.5.0: 783 + 51 CREC/branching/attachments: 10 branching +
#    12 attachments + 1 attachment-ref + 2 prompt-path + 4 filter-domains +
#    3 bijection + 1 (xpassed→passed) + 18 from regenerated 26-cap×3-case)
Write-Output ">>> pytest tests/ -q"
$env:PYTHONPATH = "src"
$out = & $py -m pytest tests/ -q 2>&1 | Out-String
if ($out -match "(\d+) passed") {
    $n = [int]$Matches[1]
    if ($n -ge 820) { Pass "pytest: $n passed (>=820)" } else { Fail "pytest: >=820 passed" "got $n" }
} else {
    Fail "pytest: parse failed" $out.Substring(0, [Math]::Min(200, $out.Length))
}

# 1b. vitest run for the web client.
Write-Output ">>> vitest run"
$webDir = Join-Path $repo "apps\web"
Push-Location $webDir
try {
    $vout = npm test 2>&1 | Out-String
} finally {
    Pop-Location
}
# Strip ANSI escape codes (the vitest default reporter uses 24-bit
# colors). Use [char]27 for ESC, not `e (PowerShell's `e is just 'e').
$esc = [char]27
$clean = [regex]::Replace($vout, "$esc\[[0-9;]*[A-Za-z]", "")
# Try both shapes: "Tests  8 passed (8)" and "Test Files  3 passed (3)".
if ($clean -match "Tests\s+(\d+)\s+passed") {
    $vpass = [int]$Matches[1]
    if ($vpass -ge 28) { Pass "vitest: $vpass frontend tests passed (>=28)" } else { Fail "vitest: >=28 tests passed" "got $vpass" }
} elseif ($clean -match "Test Files\s+(\d+)\s+passed") {
    $vpass = [int]$Matches[1]
    if ($vpass -ge 28) { Pass "vitest: $vpass frontend test files passed (>=28)" } else { Fail "vitest: >=28 files passed" "got $vpass" }
} else {
    Fail "vitest: parse failed" $clean.Substring(0, [Math]::Min(400, $clean.Length))
}

# 2. Invariants
Write-Output ">>> scripts/invariants_check.ps1"
$out = & powershell -ExecutionPolicy Bypass -File (Join-Path $repo "scripts\invariants_check.ps1") 2>&1 | Out-String
if ($out -match "All invariants pass") { Pass "invariants: all pass" } else { Fail "invariants" $out.Substring(0, [Math]::Min(400, $out.Length)) }

# 3. Scorer self-score
Write-Output ">>> scorer self-score"
$env:PYTHONPATH = "src"
$out = & $py -c "from dhc.scoring.scorer import make_report, write_report, ModuleScore; r = make_report([ModuleScore(f'c{i}', 100.0, 100.0) for i in range(1, 11)]); write_report(r, 'dhc-v-report.json'); print(r.dhc_v)" 2>&1 | Out-String
if ($out.Trim() -eq "100.0") { Pass "scorer: DHC-V=100.0" } else { Fail "scorer" $out.Trim() }

# 4. v1.2.0 live smoke (19 checks)
Write-Output ">>> v1.2.0 chat smoke"
$env:PYTHONPATH = "src"
$out = & $py "C:\Users\Rex\.config\opencode\harness_benchmark\tests\chat\smoke_v12.py" 2>&1 | Out-String
if ($out -match "Pass:\s*(\d+)\s*Fail:\s*(\d+)") {
    $p = [int]$Matches[1]
    $f = [int]$Matches[2]
    if ($p -ge 19 -and $f -eq 0) { Pass "v1.2.0 chat smoke: $p/19 passed" } else { Fail "v1.2.0 chat smoke" "Pass=$p Fail=$f" }
} else {
    Fail "v1.2.0 chat smoke: parse failed" $out.Substring(0, [Math]::Min(300, $out.Length))
}

# 5. Zip contents
Write-Output ">>> zip contents"
if (-not (Test-Path -LiteralPath $zip)) {
    Fail "zip exists" "no $zip"
    # Skip all remaining zip-related checks.
    $zip_present = $false
} else {
    $zip_present = $true
}
if ($zip_present) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $names = $z.Entries.FullName
    $z.Dispose()

    # 5a. No runtime artifacts
    $bad = $names | Where-Object { $_ -match "(^|\\)serve_c1\.(log|err\.log|port|token)($|\\)|(^|\\)heartbeat.*\.(log|err\.log)($|\\)|(^|\\)demo_live\.(log|err\.log)($|\\)|(^|\\)vite\.(log|err\.log)($|\\)|(^|\\)mock_llm\.(log|err\.log)($|\\)|(^|\\)secrets\.log($|\\)|__pycache__|\.pytest_cache|(^|\\)\.dhc(-smoke)?($|\\)" }
    if ($bad) { Fail "zip: no runtime artifacts" ($bad -join ", ") } else { Pass "zip: no runtime artifacts" }

    # 5b. All 22 doc files present (v1.2.0 adds 3 chat docs + 2 ADRs;
    #     v1.2.1 adds 1 ADR; v1.3.0 adds 2 docs + 3 ADRs)
    $want = @(
        "docs/README.md",
        "docs/architecture.md",
        "docs/security-model.md",
        "docs/plugin-authoring.md",
        "docs/SHA-PINNING.md",
        "docs/chat-architecture.md",
        "docs/session-storage.md",
        "docs/secrets-model.md",
        "docs/v1.3.0-technical-spec.md",
        "docs/v1.3.0-test-plan.md",
        "docs/adr/0001-three-tab-ui.md",
        "docs/adr/0002-plugin-marketplace-with-sha-integrity.md",
        "docs/adr/0003-ephemeral-port-and-bearer-token.md",
        "docs/adr/0004-chat-and-sessions.md",
        "docs/adr/0005-markdown-component.md",
        "docs/adr/0006-model-selection.md",
        "docs/adr/0007-api-key-management.md",
        "docs/adr/0008-retry-policy.md",
        "docs/adr/0009-provider-abstraction.md",
        "docs/adr/0010-per-secret-nonce.md",
        "docs/adr/0011-model-configuration.md",
        "docs/adr/0012-envelope-v0x03.md",
        "docs/adr/0013-secret-name-binding.md",
        "docs/adr/0014-canonical-mac-input.md",
        "docs/adr/0015-entropy-gate-capability-whitelist.md",
        "docs/adr/0015-amendment-1-cap-to-32.md",
        "docs/adr/0016-number-gate-legal-charset.md",
        "docs/adr/0017-tests-as-error-boundary.md",
        "docs/adr/0018-prompt-path-control.md",
        "docs/adr/0019-message-branching.md",
        "docs/adr/0020-attachments-v0x04.md",
        "docs/adr/0021-filter-domains.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "GLOSSARY.md",
        "relay/MANIFEST.txt"
    )
    $miss = @()
    foreach ($f in $want) {
        $hit = $names | Where-Object { $_ -eq $f -or $_ -replace "\\","/" -eq $f }
        if (-not $hit) { $miss += $f }
    }
    if ($miss) { Fail "zip: all 36 doc files" ("missing: " + ($miss -join ", ")) } else { Pass "zip: all 36 doc files present" }

    # 5c. README in zip is v1.6.0 (case-sensitive match to skip readme.txt)
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $readmeEntry = $z.Entries | Where-Object {
        $_ -clike "README.md" -or $_ -clike "*/README.md"
    } | Select-Object -First 1
    if ($readmeEntry) {
        $reader = New-Object System.IO.StreamReader($readmeEntry.Open())
        $readme = $reader.ReadToEnd()
        $reader.Close()
        if ($readme -match "v1\.6\.0") { Pass "zip: README is v1.6.0" } else { Fail "zip: README is v1.6.0" "banner not v1.6.0" }
    } else {
        Fail "zip: README present" "no README.md in zip"
    }
    $z.Dispose()
}

# 6. SHA-PINNING.md matches the 5 manifests
Write-Output ">>> SHA pinning consistency"
$pinning = Get-Content -LiteralPath (Join-Path $repo "docs\SHA-PINNING.md") -Raw
$ok = $true
foreach ($plugin_id in @("rate_limiter_v1","session_exporter_v1","model_router_v1","memory_store_v1","prompt_browser_v1")) {
    $m = Get-Content -LiteralPath (Join-Path $repo "src\dhc\plugins\$plugin_id\manifest.json") -Raw
    $sm = [regex]::Match($m, '"sha256":\s*"([a-f0-9]{64})"')
    if (-not $sm.Success) { $ok = $false; break }
    $manifest_sha = $sm.Groups[1].Value
    $rowPattern = ('\|\s*`' + $plugin_id + '`\s*\|\s*`([a-f0-9]{64})`\s*\|')
    $rm = [regex]::Match($pinning, $rowPattern)
    if (-not $rm.Success) { $ok = $false; break }
    if ($rm.Groups[1].Value -ne $manifest_sha) { $ok = $false; break }
}
if ($ok) { Pass "SHA pinning: all 5 docs/manifests in sync" } else { Fail "SHA pinning consistency" "see script" }

# 7. Source files in zip
if ($zip_present) {
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $srcFiles = $z.Entries | Where-Object { $_.FullName -like "src/dhc/plugins/*/manifest.json" -or $_.FullName -like "src\dhc\plugins\*\manifest.json" }
    $z.Dispose()
    if ($srcFiles.Count -eq 5) { Pass "zip: 5 plugin manifests" } else { Fail "zip: 5 plugin manifests" "got $($srcFiles.Count)" }
}

# 8. Root layout: no staging dir prefix
if ($zip_present) {
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $hasStaging = $z.Entries | Where-Object { $_.FullName -match "(^|\\)harness_benchmark[\\/]" }
    $z.Dispose()
    if ($hasStaging) { Fail "zip: no staging prefix" ($hasStaging[0].FullName) } else { Pass "zip: no staging prefix" }
}

# 8b. v1.4.0: Entropy Map presence and marker.
$entropyMap = Join-Path $repo "docs\entropy-map.md"
if (Test-Path -LiteralPath $entropyMap) {
    $emContent = Get-Content -LiteralPath $entropyMap -Raw
    if ($emContent -match "<!-- generated by scripts/build_entropy_map\.py -->") {
        Pass "entropy map: docs/entropy-map.md exists with GENERATED_MARKER"
    } else {
        Fail "entropy map: GENERATED_MARKER missing" "first line is not the marker"
    }
} else {
    Fail "entropy map: docs/entropy-map.md missing" "run scripts/build_entropy_map.py"
}

# 8c. v1.5.0 (Phase 1, Findings 1+2): capability bijection.
# Property: set(Capability) == ACTION_WHITELIST.keys() == web_core.registered_capabilities.
$env:PYTHONPATH = "src"
$out = & $py -c "
import tempfile, pathlib
from dhc.cordis.capabilities import Capability, ACTION_WHITELIST
from dhc.modules.c1_gui_web_core.service import GuiWebCore
with tempfile.TemporaryDirectory() as td:
    sd = pathlib.Path(td) / 'sessions'; sd.mkdir()
    gwc = GuiWebCore(sessions_dir=sd)
    enum_set = set(Capability); reg_set = gwc.registered_capabilities; wl_set = set(ACTION_WHITELIST.keys())
    if enum_set != wl_set:
        print(f'wl_mismatch: enum-cap={enum_set - wl_set} dangling={wl_set - enum_set}'); raise SystemExit(1)
    if enum_set != reg_set:
        print(f'route_mismatch: orphan={enum_set - reg_set} dangling={reg_set - enum_set}'); raise SystemExit(1)
    print('bijection_ok')
" 2>&1 | Out-String
if ($out -match "bijection_ok") { Pass "capability bijection: enum == whitelist == routes" } else { Fail "capability bijection" $out.Trim() }

# 8d. v1.5.0 (ADR-0018): prompt-path is unfiltered. The C7 dispatch
# module does not import number_gate.
$out = & $py -c "
import inspect
from dhc.modules.c7_llm_stream_adapter import service as c7
src = inspect.getsource(c7)
if 'number_gate' in src: raise SystemExit(1)
print('prompt_path_unfiltered_ok')
" 2>&1 | Out-String
if ($out -match "prompt_path_unfiltered_ok") { Pass "prompt-path: C7 does not import number_gate" } else { Fail "prompt-path: C7 imports number_gate" $out.Trim() }

# 8e. v1.5.0 (ADR-0020): attachment ref invisible to key_lookup.
$out = & $py -c "
from dhc.integrations.key_lookup import lookup_api_key
called = []
def tracker(name): called.append(name); return None
lookup_api_key('openai', 'gpt-4o-mini', tracker)
if any(name.startswith('attachment:') for name in called): raise SystemExit(1)
print('attachment_ref_invisible_ok')
" 2>&1 | Out-String
if ($out -match "attachment_ref_invisible_ok") { Pass "attachment ref: invisible to key_lookup" } else { Fail "attachment ref: key_lookup" $out.Trim() }

# 9. v1.2.x + v1.3.0 + v1.3.1 + v1.3.2 + v1.3.3 + v1.3.4 + v1.4.0 + v1.5.0 + v1.5.1 source files in zip
if ($zip_present) {
    $z = [System.IO.Compression.ZipFile]::OpenRead($zip)
    $names = $z.Entries.FullName
    $z.Dispose()
    $want120 = @(
        "src/dhc/cordis/secrets.py",
        "src/dhc/cordis/capabilities.py",
        "src/dhc/cordis/number_gate.py",
        "src/dhc/cordis/entropy_map.py",
        "src/dhc/cordis/pipeline_guard.py",
        "src/dhc/cordis/attachments.py",
        "src/dhc/cordis/plugin_memory.py",
        "src/dhc/services/session_manager.py",
        "src/dhc/services/model_registry.py",
        "src/dhc/services/model_config.py",
        "src/dhc/integrations/base.py",
        "src/dhc/integrations/key_lookup.py",
        "src/dhc/integrations/openai_client.py",
        "src/dhc/integrations/anthropic_client.py",
        "src/dhc/integrations/openrouter_client.py",
        "tests/fixtures/mock_llm.py",
        "tests/chat/test_secrets_v3.py",
        "tests/chat/test_secrets_v4.py",
        "tests/chat/test_secrets_name_binding.py",
        "tests/chat/test_migrate_v03_to_v04.py",
        "tests/integrations/test_key_lookup.py",
        "tests/chat/test_usage_aggregation.py",
        "tests/chat/test_session_model.py",
        "tests/chat/test_branching.py",
        "tests/chat/test_c7_dispatch.py",
        "tests/chat/test_chat_ws.py",
        "tests/cordis/test_capabilities.py",
        "tests/cordis/test_number_gate.py",
        "tests/cordis/test_entropy_map.py",
        "tests/cordis/test_pipeline_guard.py",
        "tests/cordis/test_build_entropy_map.py",
        "tests/cordis/test_capability_bijection.py",
        "tests/cordis/test_prompt_path_unfiltered.py",
        "tests/cordis/test_filter_domains.py",
        "tests/cordis/test_attachments.py",
        "tests/cordis/test_attachment_ref_invisibility.py",
        "tests/cordis/test_plugin_memory.py",
        "apps/web/src/panels/ChatPanel.tsx",
        "apps/web/src/panels/SessionList.tsx",
        "apps/web/src/components/Markdown.tsx",
        "apps/web/src/components/ModelSelect.tsx",
        "apps/web/src/components/ModelConfigMenu.tsx",
        "apps/web/src/components/SettingsModal.tsx",
        "apps/web/src/components/TokenCounter.tsx",
        "apps/web/src/components/SearchOverlay.tsx",
        "apps/web/src/components/BranchButton.tsx",
        "apps/web/src/components/LeafSwitcher.tsx",
        "apps/web/src/components/PathReconstruction.tsx",
        "apps/web/src/components/AttachmentsPanel.tsx",
        "apps/web/src/components/WelcomeView.tsx",
        "apps/web/src/components/SuggestionCard.tsx",
        "apps/web/src/components/MessageBubble.tsx",
        "apps/web/src/components/DeveloperModeMenu.tsx",
        "apps/web/src/components/ModalPortal.tsx",
        "apps/web/src/components/icons.tsx",
        "apps/web/src/types/chat.ts",
        "apps/web/src/__tests__/SettingsModal.test.tsx",
        "apps/web/src/__tests__/ModelConfigMenu.test.tsx",
        "apps/web/src/__tests__/TokenCounter.test.tsx",
        "apps/web/src/__tests__/BranchButton.test.tsx",
        "apps/web/src/__tests__/LeafSwitcher.test.tsx",
        "apps/web/src/__tests__/PathReconstruction.test.tsx",
        "apps/web/src/__tests__/AttachmentsPanel.test.tsx",
        "apps/web/src/__tests__/WelcomeView.test.tsx",
        "apps/web/src/__tests__/MessageBubble.test.tsx",
        "apps/web/src/__tests__/SessionList.test.tsx",
        "apps/web/src/__tests__/ChatPanel.test.tsx",
        "apps/web/src/__tests__/DeveloperModeMenu.test.tsx",
        "docs/adr/0022-stream-cancel.md",
        "docs/adr/0108-key-management-contract.md",
        "tests/chat/test_secrets_metadata_v1511.py",
        "tests/chat/test_secrets_self_heal_v1513.py",
        "tests/chat/test_llm_health_v1514.py",
        "tests/chat/test_c7_stream_timeout_v1515.py",
        "tests/chat/test_secret_source_v160.py",
        "tests/chat/test_provider_state_v160.py",
        "tests/security/test_ingress_scrubber_v160.py",
        "src/dhc/services/provider_state.py",
        "src/dhc/security/__init__.py",
        "src/dhc/security/ingress_scrubber.py",
        "apps/web/src/utils/sanitizeError.ts"
    )
    $miss120 = @()
    foreach ($f in $want120) {
        # Use -ceq (case-sensitive equal) so that the v1.5.1 / v1.5.1.1
        # casing is honored; the default -eq is case-insensitive in
        # PowerShell and would silently match wrong-cased paths.
        $hit = $names | Where-Object { $_ -ceq $f -or ($_ -replace "\\","/") -ceq $f }
        if (-not $hit) { $miss120 += $f }
    }
    if ($miss120) { Fail "zip: v1.2.x+v1.3.0+v1.3.1+v1.3.2+v1.3.3+v1.3.4+v1.4.0+v1.5.0+v1.5.1+v1.5.1.1+v1.5.1.2+v1.5.1.3+v1.5.1.4+v1.5.1.5+v1.6.0 source files" ("missing: " + ($miss120 -join ", ")) } else { Pass "zip: all v1.2.x+v1.3.0+v1.3.1+v1.3.2+v1.3.3+v1.3.4+v1.4.0+v1.5.1+v1.5.1.1+v1.5.1.2+v1.5.1.3+v1.5.1.4+v1.5.1.5+v1.6.0 source files present" }
}

# Summary
Write-Output ""
Write-Output "============================================"
$results | Format-Table -AutoSize | Out-String | Write-Output
$pass = ($results | Where-Object { $_.status -eq "PASS" }).Count
$fail = ($results | Where-Object { $_.status -like "FAIL*" }).Count
Write-Output "Pass: $pass  Fail: $fail"
if ($fail -gt 0) { exit 1 } else { exit 0 }
