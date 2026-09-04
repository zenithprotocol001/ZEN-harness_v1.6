# ADR-0017: Tests as Error Boundary — Runtime Guard and Test Suite as Projections of One Policy

- **Status:** Accepted 2026-09-07
- **Supersedes:** none
- **Superseded by:** none
- **Related:** ADR-0015 (Entropy Gate, capability whitelist), ADR-0016 (Number Gate, legal charset)

## Context

ADR-0015 and ADR-0016 establish two policy-data sources:

- `ACTION_WHITELIST: dict[Capability, ActorTier]` — the capability table.
- `LEGAL_TEXT_CODES: frozenset[int]` — the 62-code legal alphabet.

Both are *data*. Both are *finite*. Both are *the source of truth* for the security boundary they represent. A test suite that hand-writes one test per capability × actor-tier combination is, by construction, a copy of the policy that can drift. An audit document that hand-writes a row per capability is another copy. A runtime guard that hand-codes the same checks is a third copy.

The principle CREC demands is: **one source, three projections, generator-enforced.** A human should not write a test, an audit row, or a runtime check by hand; a generator should.

## Decision

The capability whitelist and the 62-code alphabet are each consumed by a **generator**:

1. **The runtime enforcer** reads the policy and rejects anything off-whitelist.
2. **The test generator** iterates the policy and emits a pass-test, a deny-test, and a raise-test for every legal action.
3. **The Entropy Map generator** iterates the policy and emits `docs/entropy-map.md` as a living threat matrix.

One source. Three projections. The generator is a Python function, not a code-generator tool; it produces `pytest.param` tuples for the test suite and markdown strings for the audit doc. The runtime enforcer and the generators all import from the same module.

### The capability test generator

```python
# src/dhc/cordis/capabilities.py

def enumerate_capability_tests() -> list[pytest.param]:
    """Generate one pytest.param per Capability × ActorTier combination.
    
    The test file `tests/cordis/test_capabilities.py` is a thin
    pytest wrapper:
    
        @pytest.mark.parametrize("case", enumerate_capability_tests())
        def test_capability_runtime_enforcement(case):
            cap, tier, expected = case
            if expected == "pass":
                assert _check(cap, tier) is None
            elif expected == "deny":
                with pytest.raises(CapabilityDenied):
                    _check(cap, tier)
            elif expected == "raise":
                with pytest.raises(ValueError):
                    _check(cap, str(cap.value) + "_unknown_tier")
    """
```

The function returns a list of `pytest.param` tuples, one per `(Capability, ActorTier, expected_outcome)` combination. The test file calls this function at module load; pytest picks up the parametrized cases. **A new capability automatically gets a new test case; there is no hand-written test list to drift.**

### The Entropy Map generator

```python
# src/dhc/cordis/entropy_map.py

def render_entropy_map() -> str:
    """Render docs/entropy-map.md from ACTION_WHITELIST and
    LEGAL_TEXT_CODES. Returns a markdown string.
    
    The output is a table:
    
        | Capability   | input-class     | risk     | rationale |
        |--------------|-----------------|----------|-----------|
        | secret.put   | legal-text      | LOW      | ...       |
        | chat.send    | free-text       | MEDIUM   | ...       |
        | ...          | ...             | ...      | ...       |
    
    The rationale is derived from the capability name and the
    ActorTier; the `risk` column is a per-capability annotation
    in the whitelist (default: MEDIUM).
    """
```

The generator is invoked at build time by `scripts/build_entropy_map.py` and the output is checked into the repo. The check-in is a generator-emitted artifact, not a hand-written doc. **A new capability automatically gets a new row in the Entropy Map.**

### The 256-byte sweep generator

```python
# src/dhc/cordis/number_gate.py

def enumerate_byte_code_tests() -> list[pytest.param]:
    """Generate one pytest.param per byte code (0..255)."""
    return [
        pytest.param(
            code,
            id=f"code_{code:03d}_0x{code:02X}"
        )
        for code in range(256)
    ]
```

The test file calls this function and asserts the strict projection matches the expected output (legal codes pass, non-legal codes are escaped). **The full 256-code space is covered by one parametrized test, not by hand.**

### The principle, stated precisely

> The runtime guard reads the policy.
> The test generator iterates the policy.
> The audit generator iterates the policy.
> The policy is data. The data is finite. The data is the source of truth.

The test count is not a goal; coverage of the policy space is the goal. v1.4.0 has:
- 1 parametrized test × 256 byte codes = **256 cases** in `test_all_256_byte_codes`.
- 1 parametrized test × 24 capabilities × 2 actor tiers = **48 cases** in the capability test file.
- 1 parametrized test × 5 payload sizes = **5 cases** in the lossless roundtrip test.
- 1 parametrized test × 3 payload types = **3 cases** in the audit-render test.

That is ~312 generated test cases on top of the hand-written edge cases. The hand-written edge cases are bounded; the generator cases are exhaustive.

### The build-time step

`scripts/build_entropy_map.py` is a build-time script that invokes `render_entropy_map()` and writes the output to `docs/entropy-map.md`. The DoD includes a check that `docs/entropy-map.md` exists, is non-empty, and was generated by the script (a `<!-- generated by scripts/build_entropy_map.py -->` comment is the marker). A hand-edit of `docs/entropy-map.md` is detected by the invariant checker (the marker must be present).

The build step is part of `scripts/package_relay.ps1`; the artifact always includes a freshly-generated Entropy Map.

## Consequences

### Positive

- **One source of truth.** The policy is data. The data is the source. The runtime, the tests, and the audit doc all read the same data.
- **Generator-enforced coverage.** The test count grows with the policy, not with hand-written effort. A new capability gets 2 (pass + deny) + 1 (raise) test cases for free.
- **The audit doc cannot drift.** It is generated from the policy. A hand-edit is detected.
- **The entropy is bounded.** The capability set is finite. The byte-code set is finite (256). The agent can traverse every option.

### Neutral

- The test file is a thin wrapper. Most of the logic is in the generator. A new contributor who wants to add a hand-written test must also add the policy entry; there is no escape hatch.
- The build-time step is a one-line addition to `scripts/package_relay.ps1`. A new contributor who bypasses the build (e.g. commits a manual edit) breaks the invariant check.
- The 256-code sweep is a single parametrized test that runs 256 cases. Total wall time is < 100 ms. No performance concern.

### Negative

- **Generator maintenance.** A future maintainer who wants to add a new test must edit the generator, not the test file. This is a small but real learning curve.
- **The "exhaustive" claim is a function of the policy size.** If the policy grows to 100 capabilities, the test count grows to 600 cases. That is still tractable; it is also the point of the bounded-entropy rule.

## Wire format (unchanged)

No wire-format change. The generators emit pytest parameters and markdown strings; nothing crosses the network.

## Test plan

The tests for the generators themselves are 2 in `tests/cordis/test_entropy_map.py`:

1. `test_entropy_map_marker_present` — `docs/entropy-map.md` starts with the `<!-- generated by scripts/build_entropy_map.py -->` marker. (Static shape.)
2. `test_entropy_map_table_covers_every_capability` — every `Capability` value appears as a row in the Entropy Map. (Generated: iterate `Capability`, regex-search the markdown.)

The tests for the *generators* are hand-written edge cases in `tests/cordis/test_capabilities.py` and `tests/cordis/test_number_gate.py`. The generators themselves are not tested for output correctness — they are tested by the parametrized cases that consume them.

## Rejected alternatives

- **Hand-written test cases for every capability.** Rejected: hand-written tests drift. The whole point of the proposal is generator-enforced coverage.
- **A separate code-generation tool (e.g. Jinja templates, hygen).** Rejected: the generators are 20-line Python functions. A templating tool is overkill.
- **The audit doc is hand-written.** Rejected: the doc is a render of the policy. A hand-written doc drifts. The principle is "one source, three projections."
- **The audit doc is generated at runtime, not build time.** Rejected: a runtime-generated doc is not stable. The artifact in the relay zip must be a static file.
- **The Entropy Map covers every byte code (256 rows).** Rejected: the byte-code risk is uniform (each non-legal code is `#0xNN`; the risk is "escaped, never interpreted"). 256 rows is noise. The Entropy Map covers *capabilities*, not *byte codes*. The byte-code sweep is a separate parametrized test, not a doc row.

## Implementation notes

- `src/dhc/cordis/capabilities.py` — `enumerate_capability_tests()` is exported. The test file imports it and parametrizes over the result.
- `src/dhc/cordis/number_gate.py` — `enumerate_byte_code_tests()` is exported. The test file imports it and parametrizes.
- `src/dhc/cordis/entropy_map.py` — `render_entropy_map()` is exported. The build script imports it.
- `scripts/build_entropy_map.py` — invokes `render_entropy_map()` and writes the output.
- `scripts/package_relay.ps1` — calls `build_entropy_map.py` before zipping. The zip always includes a freshly-generated Entropy Map.
- `scripts/invariants_check.ps1` — checks the marker comment and the table coverage.
- `scripts/dod_verify.ps1` — adds a 13th DoD check: `docs/entropy-map.md` exists, is non-empty, has the marker, and was last modified after the most recent change to `capabilities.py`.
