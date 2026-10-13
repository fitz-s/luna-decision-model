---
name: luna-decision
description: Installs, checks and connects the Luna Decision Model local service (typed yes/no, choice and score decisions on GPT-6 Luna through the user's Codex ChatGPT login) to a project or agent host. Use when the user mentions Luna Decision Model, luna-decision, jev-luna, a local Jev/TypeSafe decision service, `jev_decide`, or asks to connect Claude Code, Codex, an OpenAI/TypeSafe Python SDK or raw HTTP to it, or to diagnose its errors.
---

# Luna Decision Model

Unofficial loopback service on the user's machine. Inference runs on OpenAI `gpt-6-luna` in the cloud through the user's own Codex-managed ChatGPT login. Full reference: `AGENTS.md` and `TECHNICAL_REFERENCE.md` in the repository. Do not restate their tables; read them.

## Safety rules
- Never ask for, print or store API keys or OAuth tokens. Codex owns the login; the service uses it in memory. `service.key` only gates loopback.
- `doctor` (no `--live`) makes no model call. **`doctor --live` and `decide` spend the user's subscription: get a yes first.** So does every real request.
- Any `config` command is a dry run unless it has `--write`, `--write-mcp` or `--restore*`. Run the dry run, show the file list under `writes`, and ask before writing. `--write-mcp` has no dry run: show `config` output and confirm first.
- Do not run `codex login` unless the login is confirmed missing and the user agrees. Do not change Codex or Claude global settings; only project files the user named.
- Do not edit `jev_luna/` or `luna_decision_model/`. Do not start a second service on a port that is occupied.

## Workflow
1. **Find or install it.** Look for an existing install: `.venv/bin/luna-decision` in the repository checkout, or `command -v luna-decision`. Set `LDM` to that path. If none exists, install from the repository checkout:
   ```bash
   python3 -m venv .venv
   .venv/bin/pip install '.[openai]'   # add ,typesafe for the TypeSafe SDK, ,mcp for MCP
   LDM=.venv/bin/luna-decision
   ```
2. **Start** in the background, or ask the user to run it in their own terminal (it blocks): `$LDM start`. Default port 20141; add `--port <n>` only if busy. Needs Python >=3.10 and `codex` on PATH with a ChatGPT login.
3. **Check, no model call:** `$LDM doctor`. Exit 0 means login ready and the running service matches this build. Add `--url http://127.0.0.1:<n>` for a non-default port.
4. **Connect** the host (next section), preview first.
5. **First real request** (spends usage; confirm): `$LDM doctor --live`, or the SDK call below.

## Pick the host path
- **Python app:** `from luna_decision_model import openai_client` then `openai_client().decisions.create(model="gpt-6-luna", input="...", questions=[{"type":"predicate","instructions":"..."}])`. The OpenAI base URL ends in `/v1` (default). `typesafe_client()` uses the root URL with no `/v1`. Needs the `openai` / `typesafe` extra in the app's environment: `pip install '/path/to/luna-decision-model[openai]'` from the repository checkout. Never pass `api_key`.
- **Claude Code:** `$LDM config --write-mcp /abs/project` (adds MCP tool `jev_decide` to that project's `.mcp.json`; undo with `--restore-mcp`). Needs the `mcp` extra. Optional project rule: `$LDM config --integration openai --project /abs/project`, review, then add `--write`; `--check` verifies, `--restore` removes.
- **Codex:** run `$LDM config`; in `~/.codex/config.toml` add `[mcp_servers.luna-decision]` with `command` and `args` from the printed `mcp` object and a `[mcp_servers.luna-decision.env]` table with its `DECIDE_D_URL` and `JEV_LUNA_HOME`. Show the snippet and let the user paste it. Codex 0.159.0 was the tested version.
- **Raw HTTP:** `POST http://127.0.0.1:20141/v1/systemone` (Jev wire) or `/v1/decisions` (OpenAI wire), header `Authorization: Bearer <service.key>`; `GET /health` needs no key. Details: `TECHNICAL_REFERENCE.md#http-api`.
In every MCP case the HTTP service must run separately, and `command` must be the absolute Python of the install that printed it.

## Diagnose by error code
| Code (status) | Meaning and next step |
| --- | --- |
| `codex_missing` / `codex_start` (422) | `codex` not on PATH or will not start. Install Codex or set `LDM_CODEX_BIN`. |
| `native_login_required` (401) | No Codex ChatGPT login. User runs `codex login`, or `$LDM start --login`. |
| `native_managed_login` (401) | Login is not ChatGPT-managed (API-key auth is unsupported). User re-logs in with ChatGPT. |
| `native_routing_capability`, `native_regional_route` (422) | Installed Codex does not expose the expected workspace routing: version drift. Check `codex --version` against 0.159.0; report, do not patch. |
| `native_auth_method`, `native_auth_profile`, `native_auth_rpc` (422), `native_auth_protocol`, `native_auth_channel` (502) | Codex app-server protocol differs or broke. Same: version drift. |
| `native_account_changed`, `native_policy_changed` (409) | Account changed mid-request. Retry once. |
| `local_auth` (401) | Wrong or missing `service.key`; the client must use the same `JEV_LUNA_HOME` as the server. |
| `invalid_host`, `invalid_origin` (403) | Request not addressed to the loopback host and port. |
| `unknown_model`, `invalid_request` (400/422) | Use `model: "gpt-6-luna"`; check question shape. |
| `usage_limit_reached`, `rate_limit_exceeded` (429) | Subscription allowance exhausted. Wait; there is no paid fallback. |
| `deadline` (504), `invalid_output` (502) | Slow or malformed upstream result. Retry once; keep inputs short. |
| `doctor` prints non-`Reject` errors by class name (e.g. `CodexLoginRequired`, `NativeAuthStateError`) with a generic message (`doctor.py:145`) | Codes like `native_login_required` appear in HTTP / `decide` responses, not in `doctor` output. |
| `doctor`: `target_server.reachable: false` or `build_matches_client: false` | Service not running on that port, or a stale server. Start/restart, or point `--url` at the right port. |
Full list: `AGENTS.md` "Version adaptation".

## Install this skill
Copy this folder (`skills/luna-decision/`) to `~/.claude/skills/luna-decision/` or a project's `.claude/skills/luna-decision/` for Claude Code, and to your Codex skills directory (for example `~/.codex/skills/luna-decision/`) for Codex. The skill needs no other files.
