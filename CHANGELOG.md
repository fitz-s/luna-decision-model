# Changelog

Luna Decision Model 0.1.0 is the first recommended release. The prereleases below used `0.2.0rcN` version numbers, so pip orders them above 0.1.0: install 0.1.0 into a new directory instead of upgrading an old environment in place. If an older service still runs on port 20141, start the new one with `luna-decision start --port <n>` and check with `luna-decision doctor --url http://127.0.0.1:<n>` until the new version is accepted. Historical reports and receipts are kept in the maintainer's local archive, not in the repository.

## Unreleased

Open-source cleanup and a Python 3.10 fix.

- Fixed: on Python 3.10 a request deadline could surface as HTTP 500 and go unenforced, because `concurrent.futures.TimeoutError` is a separate class before 3.11 (`jev_luna/transport.py`).
- Support is macOS and Linux; Windows is not supported yet and was removed from CI.
- Removed the private release pipeline: the offline installer ZIP and `install.py`, `scripts/package_release.py`, `verify_release.py`, `install_bundle.py`, `package_review.py` (review/evaluation bundles), `install_local.py`, `build_acceptance_report.py`, `qualify_payloads.py`, `verify_trace_capture.py` and the legacy `prepare_integrations.py`. Build with `python -m build`; CI builds the sdist and wheel and smoke-tests the wheel.
- Removed the historical experiment runners under `tools/`, the live runners `tests/*_live.py` and the unwired `.mjs` tests. Adapter preparation moved to `scripts/prepare_adapters.py`; the integration contract moved to `scripts/integration_contract.py`.
- Removed the root `integrations/` copy; `jev_luna/assets/` is the single source of the native assets and their lock.
- Removed the root compatibility shims `decide_d.py`, `subscription_auth.py` and `jev_luna.py`. Import `jev_luna.core` and `jev_luna.subscription_auth` instead; the `jev-luna` command and `python -m jev_luna` are unchanged.
- Renamed release-round test files to subject names.
- Removed the `jev-layer` adapter profile and `jev-layer.patch`: it targeted a locally modified Jev client with no upstream commit. The pinned `jev-upstream` and `codex-selector` profiles are unchanged. This changes the runtime build hash.
- Latency re-measured on 0.1.0 (38 public cases, default effort `none`: p50 3.0 s, p95 6.2 s) with a reasoning-effort comparison; see [docs/COMPARISON.md](docs/COMPARISON.md#reasoning-effort).

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
