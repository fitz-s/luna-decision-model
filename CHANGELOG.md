# Changelog

Luna Decision Model 0.1.0 is the first recommended release. The prereleases below used `0.2.0rcN` version numbers, so pip orders them above 0.1.0: install 0.1.0 into a new directory instead of upgrading an old environment in place. If an older service still runs on port 20141, start the new one with `luna-decision start --port <n>` and check with `luna-decision doctor --url http://127.0.0.1:<n>` until the new version is accepted. Historical reports and receipts are kept in the maintainer's local archive, not in the repository.

## Unreleased

Open-source cleanup, faster credential lookup, optional Fast mode and a Python 3.10 fix.

- Faster: one `codex app-server` now serves successive credential lookups instead of a new process per request, cutting local overhead from about 0.42 s to 0.03 s per request (median). Each lookup still reads the native login and workspace; the process is replaced when Codex's `auth.json`, `config.toml` or `codex` binary changes, after a refresh or fault, and after 300 s. Reuse applies only when Codex stores credentials in `auth.json` (its default file store); keyring, auto and managed configurations keep one process per lookup. A lookup that overlaps a store change is discarded and read again, and only a broken channel is retried, bound to the same account and routing.

- Added: optional Codex Fast mode. Start the service with `LDM_SERVICE_TIER=fast` to send Codex's Fast request (`service_tier: "priority"` and the `x-codex-routing-hint` header); it uses included limits at 2.5× the Standard rate. Unset or `default` sends nothing new, an invalid value stops startup, and a rejected Fast request is returned rather than retried at the default tier. `/health` and `doctor` show the tier; traces record requested and reported tiers. This changes the runtime build hash.
- Added: `LDM_REASONING_EFFORT` (`auto` default, or `none`, `low`, `medium`, `high`) fixes the reasoning effort for every request; an invalid value stops startup and `/health` shows the setting. This changes the runtime build hash.
- Added, off by default: `LDM_RESPONSES_LITE=1` (Codex Responses Lite request form) and `LDM_TRANSPORT=websocket` (Codex Responses WebSocket). A capability rejection sends the decision in the standard HTTP form and disables that option until restart; `/health` and `doctor` show configured and active values. This changes the runtime build hash.
- Added, off by default: `LDM_HEDGE_AFTER_MS`, a single delayed hedge for effort-`none` batches with at most two physical attempts, replacing the three-attempt retry for those batches. This changes the runtime build hash.
- Added, off by default: `LDM_PROBABILITY_SHRINK` (0 to 0.5) moves returned probabilities toward uniform, `p' = (1 − a)·p + a/K`, without changing any answer; on Decision Bench v4 `a = 0.10` cut ECE from 0.074 to 0.011 and confident wrong answers from 66 to 0 (see [COMPARISON.md](docs/COMPARISON.md#probability-shrink)). `/health`, `doctor` and traces show the value. This changes the runtime build hash.
- Fixed: on Python 3.10 a request deadline could surface as HTTP 500 and go unenforced, because `concurrent.futures.TimeoutError` is a separate class before 3.11 (`jev_luna/transport.py`).
- Support is macOS and Linux; Windows is not supported yet and was removed from CI.
- Removed the private release pipeline: the offline installer ZIP and `install.py`, `scripts/package_release.py`, `verify_release.py`, `install_bundle.py`, `package_review.py` (review/evaluation bundles), `install_local.py`, `build_acceptance_report.py`, `qualify_payloads.py`, `verify_trace_capture.py` and the legacy `prepare_integrations.py`. Build with `python -m build`; CI builds the sdist and wheel and smoke-tests the wheel.
- Removed the historical experiment runners under `tools/`, the live runners `tests/*_live.py` and the unwired `.mjs` tests. Adapter preparation moved to `scripts/prepare_adapters.py`; the integration contract moved to `scripts/integration_contract.py`.
- Removed the root `integrations/` copy; `jev_luna/assets/` is the single source of the native assets and their lock.
- Removed the root compatibility shims `decide_d.py`, `subscription_auth.py` and `jev_luna.py`. Import `jev_luna.core` and `jev_luna.subscription_auth` instead; the `jev-luna` command and `python -m jev_luna` are unchanged.
- Renamed release-round test files to subject names.
- Removed the `jev-layer` adapter profile and `jev-layer.patch`: it targeted a locally modified Jev client with no upstream commit. The pinned `jev-upstream` and `codex-selector` profiles are unchanged. This changes the runtime build hash.

## 0.1.0 — 2026-10-13

- Installation ZIP with `install.py` (default port 20141) for offline installs.
- Packages the unchanged baseline readout with a clearer boundary: inference runs in OpenAI's cloud through ordinary Codex Responses; the local service only adapts requests.
- Adds project connection guidance: `luna-decision config --integration {typesafe,openai,native-jev,codex-selector}` with dry-run by default and `--write`, `--check`, `--restore`, plus the Node helper `sdk-local.mjs`. Offline acceptance used a controlled local backend with zero native model calls: 42 tests / 19 subtests, 11 contract checks with real original callers, and dry-run / write / check / restore checks for all four profiles. Native host entry points were not re-run live for this release.
- Adds the historical public-460 comparison, price references and the subscription-use estimation method ([docs/COMPARISON.md](docs/COMPARISON.md), [docs/QUOTA.md](docs/QUOTA.md)).
- New artifact identities; historical evaluations keep their original build and run IDs. The version number does not imply new calibration or speed results.

## Prereleases

**0.2.0rc6** — Raised the optional MCP dependency floor to 1.30: a full-chain test found that MCP 1.23 left in-flight HTTP and upstream work running after host stdin EOF, while 1.30 cancelled both. Constrained pip installation destinations despite inherited configuration and verified installed module paths and bytes. Removed external Responses backend selection. Separated runtime, source, review and evaluation artifacts. Evaluated the `keyed-probability-v2` readout on a 24-case development cohort; it failed its Brier and latency gates and stayed off. Last release whose native host entry points (Jev CLI, Read Hook, TypeSafe JS SDK, selector MCP) were exercised live on macOS arm64.

**0.2.0rc5** — Added `start`, key-free `config`, the original-SDK client helpers and the ZIP installer. Readout experiments did not pass every promotion gate.

**0.2.0rc4** — Fixed transport framing, connection handling and diagnostics.

**0.2.0rc3** — First release under the name `luna-decision-model`, with native Codex OAuth compatibility and a measured acceptance (Chinese report kept in the maintainer's local archive, not in the repository).

**jev-luna 0.1.0 – 0.1.1rc3** — Original subscription-Luna Jev compatibility bridge. The names `jev-luna` and `jev_luna` remain aliases and share the same credential and state locations (the `decide_d` and `subscription_auth` import shims were removed after 0.1.0); renaming did not migrate credentials or running services.
