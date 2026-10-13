# AGENTS.md — Luna Decision Model

Unofficial loopback HTTP service on the user's machine. Existing Jev / TypeSafe decision callers (typed yes/no `noul`, `choice`, `score`; OpenAI-style `decisions`) keep working, but inference runs on OpenAI `gpt-6-luna` in the cloud through the user's own Codex-managed ChatGPT login. Python >=3.10, version 0.1.0, MIT, open source. Install from a checkout of this repository.

Facts: unofficial, no OpenAI affiliation; access depends on the user's account. Requests (including `doctor --live` and `decide`) spend the user's Codex allowance with no fixed per-request rate and no paid-API fallback; `doctor` alone makes no model call.
Benchmark numbers live in `docs/COMPARISON.md`; never invent better ones.

## Repo map
| Path | Role |
| --- | --- |
| `jev_luna/` | Implementation (legacy package name). `core.py` HTTP service + prompt/readout, `cli.py`, `connect.py` (SDK factories), `native_rpc.py` (Codex auth), `transport.py`, `mcp.py`, `host_config.py`, `integration_config.py`, `doctor.py`, `assets/` |
| `luna_decision_model/` | Brand facade: re-exports `main`, `request`, `service_key`, `openai_client`, `typesafe_client` (+ async) from `jev_luna` |
| `scripts/` | Maintainer tooling: `prepare_adapters.py` (builds pinned native adapters + preparation receipt), `integration_contract.py` (SDK/native wiring contract against a mock upstream), `render_docs_charts.py` |
| `tests/` | `python -m pytest -q tests`; no credentials or network |
| `evidence/`, `reports/` | Not in the repository (git-ignored). Maintainer's local archive of hash-bound receipts; current 0.1.0 report: `evidence/release-010/RESULTS.md`. If present, leave untouched |
| `docs/` | Current user docs + `comparison-data.json` (source of every chart number) + `assets/*.svg` (generated); `docs/internal/` maintainer notes |
| `skills/luna-decision/` | Portable Agent Skill for installing/using this service |

## Do not edit (and why)
| Path | Reason |
| --- | --- |
| `jev_luna/**`, `luna_decision_model/**` | `doctor.identity()` hashes every `.py/.mjs/.patch/.md/.json/.go` file in both dirs into `build_sha256`; any byte change (even a comment) is a new build and stales every receipt. Edit only for a real code change, and expect a restart (`doctor` flags a stale server) |
| `jev_luna/assets/**` | Single source of the native integration assets; sha-pinned by `jev_luna/assets/lock.json` (checked by `tests/test_native_adapters.py`); `.gitattributes` protects `*.patch` whitespace |
| local `evidence/**`, `reports/**` (git-ignored) | Receipts embed sha256 of repo files and of each other |
| `tests/fixtures/*.json` | Hash-referenced by about 30 quality receipts |
| `docs/assets/*.svg` | Generated; run `python3 scripts/render_docs_charts.py` (`--check` verifies) |
| `[project.scripts] jev-luna`, `python -m jev_luna` | Legacy command names kept for existing callers |

Doc edits are safe for runtime but invalidate the doc digests in the maintainer's local `evidence/release-010/OUTER_MANIFEST.json`; call the result a doc-only revision.

## Commands
`luna-decision` = `python -I -m luna_decision_model` = `jev-luna` = `python -m jev_luna`. Default port 20141 (`--port <n>` only if busy). Loopback only.

```bash
# install from source (repo root); extras: openai, typesafe, mcp, test
python3 -m venv .venv && .venv/bin/pip install '.[openai]'     # dev/CI: '.[test,mcp]' then .venv/bin/python -m pytest -q tests
LDM=.venv/bin/luna-decision


$LDM start [--login] [--port N]   # agents: run in the background or have the user run it in their own terminal; checks Codex login, serves; never replaces an occupied port
$LDM serve                        # foreground server without the login check
$LDM doctor                       # no model call: login + compares build of the service on :20141
$LDM doctor --live                # SPENDS subscription: text, long text, inline image smokes
echo '{"model":"gpt-6-luna","state":"...","questions":{"q":{"type":"noul","instructions":"..."}}}' | $LDM decide   # SPENDS; --protocol openai for /v1/decisions
$LDM config                                          # prints URLs + MCP command/env (no key)
$LDM config --integration {typesafe|openai|native-jev|codex-selector} --project /abs/dir   # dry-run (default)
   ... --write | --check | --restore                 # writes .claude/rules/luna-decision-connection.md + .luna-decision-connect.{json,lock}
$LDM config --write-mcp /abs/dir | --restore-mcp /abs/dir   # edits ONLY that project's .mcp.json (entry "luna-decision"); no dry-run, no --integration
$LDM mcp                                             # stdio MCP server (needs the mcp extra); the HTTP service must already run
```
Build: `python -m build` (sdist + wheel in `dist/`).

## Pick a host path
| Host | Do this |
| --- | --- |
| Python app | `from luna_decision_model import openai_client` (`base_url=http://127.0.0.1:20141/v1`) or `typesafe_client` (root `http://127.0.0.1:20141`, no `/v1`). Needs the `openai` / `typesafe` extra, installed into the app's environment from the checkout (`pip install '/path/to/luna-decision-model[openai]'`); key is read from `service.key` automatically, passing `api_key=` raises |
| Claude Code | `$LDM config --write-mcp /abs/project` adds MCP tool `jev_decide`; optionally `config --integration openai --project /abs/project --write` for a project rule. Start the service separately |
| Codex | Run `$LDM config`, copy `mcp.command/args/env` into `~/.codex/config.toml`: `[mcp_servers.luna-decision]` `command = "<mcp.command>"`, `args = ["-I","-m","luna_decision_model","mcp"]`, and table `[mcp_servers.luna-decision.env]` with `DECIDE_D_URL` and `JEV_LUNA_HOME`. `command` must be the absolute Python of the install that ran `config`. Start the HTTP service separately |
| Raw HTTP | `POST /v1/systemone` (Jev wire: `model`, `state`, `questions{id:{type noul\|choice\|score,...}}`) or `POST /v1/decisions` (OpenAI wire: `model:"gpt-6-luna"`, `input`, `questions[{type predicate\|choice\|score,...}]`). Header `Authorization: Bearer <contents of $JEV_LUNA_HOME/service.key>`, `Content-Type: application/json`. `GET /health` needs no key and proves liveness only. Default key dir: macOS `~/Library/Application Support/jev-luna`, Linux `$XDG_CONFIG_HOME/jev-luna` or `~/.config/jev-luna`; `service.key` is created by the first `serve`/`start` |

Never write or request an API key or OAuth token. The local key only gates loopback; it is not multi-user authorization.

## Version adaptation
- Package version: `jev_luna/__init__.py:2` and `pyproject.toml:7`, edited together by hand. `--version` and the Codex RPC `clientInfo` read `__version__`.
- Codex CLI: 0.159.0 is the only tested version (documented in `native_rpc.py` docstring and README/INSTALL). No runtime version check; incompatibility fails closed by capability. Codes appear as `error.code` in HTTP responses (status shown); `doctor` and the CLI only print the exception class (`CodexLoginRequired`, `NativeAuthStateError`) or the message:
  - `codex_missing` 422: no `codex` on PATH / `LDM_CODEX_BIN`. `codex_start` 422: cannot spawn `codex app-server`.
  - `native_login_required` 401: no login; run `codex login` (or `start --login`). `native_login_unavailable` 422: Codex gave no usable auth; inspect login first. `native_managed_login` 401: login is not ChatGPT-managed (API-key auth unsupported).
  - `native_routing_capability` 422: `account/read` lacks `workspaceRouting` (Codex too old/new). `native_regional_route` 422: origin is not `https://chatgpt.com` or override not in NO_CONSTRAINT/us/us_cr. `native_provider_profile` 422, `native_account_binding` 422, `native_token_unavailable` / `native_account_unavailable` 401.
  - `native_auth_method` / `native_auth_profile` / `native_auth_rpc` 422: Codex rejected or requested an operation outside the `initialize/getAuthStatus/account/read` allowlist (`native_rpc.ALLOWED_RPC`) — protocol drift. `native_auth_protocol` / `native_auth_channel` 502, `native_auth_deadline` 504: broken or slow Codex process.
  - `native_account_changed` / `native_policy_changed` 409: account or routing changed mid-request; retry.
  - Service errors: `local_auth` 401 (wrong/missing service key), `invalid_host`/`invalid_origin` 403, `unknown_model` 400, `invalid_request` 422 (400 on `/v1/decisions`), `deadline` 504 (service deadline 120 s), `invalid_output`/`model_mismatch` 502, `proxy_config` 422 (only an unauthenticated HTTP CONNECT proxy is supported), upstream `usage_limit_reached` / `rate_limit_exceeded` / `subscription_sharing_usage_limit_exceeded` 429 (allowance exhausted).
  - Fix by adapting `native_rpc.validate_pair`, never by guessing accounts from JWTs. Add a version probe only if you also update docs.
- SDK pins (`pyproject.toml`): `openai>=3.28,<4`, `mcp>=1.30,<2`, `typesafe-sdk>=0.6,<0.7` (runtime) but `test` extra has `typesafe-sdk>=0.6` unbounded; `aiohttp>=3.12,<4`, `filelock>=3.19,<4`. `connect.py` only import-checks SDKs; no runtime version assertion.
- Upstream Jev / selector commits and file digests: `jev_luna/assets/lock.json`. Do not hand-edit digests.
- Bump checklist: (1) `jev_luna/__init__.py` + `pyproject.toml` version; (2) literal version strings in INSTALL.md, this file and the skill; (3) new CHANGELOG.md entry; (4) `MANIFEST.in` if root files or top-level directories were added or moved; (5) `python3 scripts/render_docs_charts.py` if `docs/comparison-data.json` changed; (6) `python -m pytest -q tests`, then `python -m build`. Codex version change: update the "tested" statement in README/INSTALL/TECHNICAL_REFERENCE after a real `doctor --live`, not before.

## Docs map
`README.md#quickstart`, `#connect-your-project` (+ `README.zh-CN.md`) · `INSTALL.md#install-from-source`, `#run`, `#verify-and-diagnose`, `#mcp-optional` · `TECHNICAL_REFERENCE.md#http-api`, `#command-line`, `#environment-variables`, `#compatibility`, `#security` · `docs/INTEGRATIONS.md#python-and-node-sdks`, `#project-connection` · `docs/COMPARISON.md` Luna vs native Jev data · `docs/QUOTA.md` estimate subscription use · `docs/OPENAI_SOURCES.md` provenance of public claims · `CHANGELOG.md` history (release codenames rc3-rc6 belong only here) · `CONTRIBUTING.md` tests, packaging, reporting · `docs/internal/` maintainer-only notes.

Style for user docs: complete sentences, one caveat once in its best place, numbers only from `docs/comparison-data.json` or existing docs, no unverified claims.
