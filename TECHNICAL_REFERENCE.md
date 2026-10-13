# Technical reference

Reference for Luna Decision Model 0.1.0. For setup see [INSTALL.md](INSTALL.md); for connecting applications see [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

## Architecture and data flow

![How it works: your app calls the local service, which uses your Codex login to reach GPT-6 Luna.](docs/assets/architecture.svg)

1. Your application sends a TypeSafe or OpenAI Decisions request to the local service on `127.0.0.1`.
2. The service validates it, compiles a structured readout prompt and asks native Codex (app-server RPC) for a fresh ChatGPT authorization and workspace route for this request.
3. It sends ordinary Codex Responses inference to `https://chatgpt.com/backend-api/codex/responses` with model `gpt-6-luna`.
4. It normalizes the answer into the original wire format and returns it.

There is no answer cache (cache TTL is fixed at zero). A pooled HTTP session reuses connections, not answers or credentials. Deadlines and cancellation propagate from the caller to the upstream request; the caller's own retry policy is not replaced. The service never calls Platform `/v1/decisions`: the official Decisions API uses the same `gpt-6-luna` identifier through a different endpoint ([official sources](docs/OPENAI_SOURCES.md)).

## HTTP API

| Endpoint | Wire format | Base URL to configure |
| --- | --- | --- |
| `POST /v1/systemone` | TypeSafe Jev: keyed `noul`, `choice`, `score` questions; full distributions with the original normalization, confidence and score math | Root: `http://127.0.0.1:20141` |
| `POST /v1/decisions` | OpenAI Decisions: ordered predicate, choice and score questions, names, per-question refusal, distributions, observed usage | With `/v1`: `http://127.0.0.1:20141/v1` |
| `GET /health` | Build and source identity, transport, readout profile and service tier | Reachability check |

The TypeSafe SDK takes the root URL and the OpenAI SDK takes the `/v1` URL. Mixing them up is the most common connection error.

| Aspect | Behavior |
| --- | --- |
| Authentication | `Authorization: Bearer <local service key>`. The key lives in `service.key` in the app-state directory and is read by the client helpers. It is not an OpenAI or TypeSafe API key. |
| Input | Text, supported structured Jev state, inline base64 images or absolute HTTP(S) image URLs (up to 128 images). URLs are forwarded unchanged and never fetched locally; `file_id` is rejected. Request bodies are capped at 64 MB. |
| Models | `/v1/decisions` accepts `gpt-6-luna`. `/v1/systemone` also accepts the Jev aliases `jev-latest`, `jev-preview`, `jev-<version>` and `typesafe/jev-1.13[-20260917]`. Every request runs on `gpt-6-luna`; other models are rejected. |
| Deadline | 120 s service deadline by default; a caller timeout sent in `X-Decide-D-Timeout-Ms` is honored. |
| Errors | Validation, authentication, upstream and timeout errors stay distinct. |
| Usage accounting | Responses report actual upstream usage. Incomplete attempts are reported as unknown, never as zero, in `X-Decide-D-Usage-Status` and `X-Decide-D-Unknown-Usage-Attempts`. `X-LDM-Readout-Profile` names the active readout profile. |

## Command line

`luna-decision` (also `jev-luna`, `python -m luna_decision_model`, `python -m jev_luna`). `luna-decision --version` prints the version.

| Command | Purpose | Options |
| --- | --- | --- |
| `start` | Check the native Codex login, then serve the API in the foreground | `--port` (default 20141), `--host` (`127.0.0.1`, `localhost`, `::1`), `--login` (run `codex login` only if no login exists) |
| `serve` | Serve the API without the login pre-check | `--port`, `--host` |
| `doctor` | Check login and the running service without a model call | `--url` (default `$JEV_LUNA_BASE_URL` or `http://127.0.0.1:20141`), `--live` (also run small Luna requests; uses subscription), `--output FILE` (save a credential-free receipt) |
| `config` | Print connection settings and the MCP command; never prints the key | `--port`, `--host`, `--write-mcp DIR`, `--restore-mcp DIR`, and the project connection options below |
| `config --integration` | Preview, write, check or undo a project connection ([guide](docs/INTEGRATIONS.md#project-connection)) | `--integration {typesafe,openai,native-jev,codex-selector}`, `--project DIR`; one of `--write`, `--check`, `--restore` (default: dry-run); native profiles: `--preparation FILE`, `--jev-bin`, `--selector-bundle`, `--node` |
| `login` | Reuse the existing Codex ChatGPT login, or run `codex login` if none exists | — |
| `decide` | Read one JSON request from stdin and print the typed answer | `--protocol {jev,openai}` (default `jev`), `--url`, `--timeout` (default 120 s) |
| `mcp` | Run the stdio MCP adapter (tool `jev_decide`); needs the `mcp` extra and a running service | — |

All paths passed to `config` must be absolute.

## Environment variables

The `DECIDE_D_*` prefix is a legacy name kept for compatibility.

| Variable | Read by | Effect |
| --- | --- | --- |
| `JEV_LUNA_BASE_URL` | `doctor`, `decide` | Default service URL. |
| `JEV_LUNA_HOME` | service, helpers | Absolute app-state directory holding `service.key`. Default: `~/Library/Application Support/jev-luna` (macOS), `$XDG_CONFIG_HOME/jev-luna` or `~/.config/jev-luna` (Linux). |
| `LDM_CODEX_BIN` | service | Path to the `codex` executable if it is not on `PATH`. |
| `DECIDE_D_TRACE_DIR` | service | Absolute directory for diagnostic traces. Off by default. Traces hold hashes, timing, observed usage and unknown attempts; never request text, OAuth tokens or the service key. |
| `DECIDE_D_CONCURRENCY` | service | Concurrent upstream calls (default 4). |
| `DECIDE_D_DEADLINE` | service | Service deadline in seconds (default 120). |
| `LDM_READOUT_PROFILE` | service | Readout profile. Default `baseline`. Other profiles are experiments that did not pass their quality gates. |
| `LDM_SERVICE_TIER` | service | `default` (also when unset) or `fast`. `fast` requests [Codex Fast mode](https://learn.chatgpt.com/docs/agent-configuration/speed#fast-mode) exactly as Codex does, with `service_tier: "priority"` and an `x-codex-routing-hint` header, and uses included limits at 2.5× the Standard rate. Any other value stops the service at startup. If the upstream rejects a Fast request, the service returns that error and never retries at the default tier. `/health` and `doctor` show the setting, and traces record the requested tier and the tier the upstream reports; the ChatGPT backend has reported `default` for Fast requests too, so that field does not confirm the tier served. |
| `LDM_REASONING_EFFORT` | service | `auto` (also when unset) keeps the adaptive policy: effort `none`, or `medium` for input over 8192 bytes with a choice or score question. `none`, `low`, `medium` or `high` sends that effort on every request; any other value stops the service at startup. `/health` shows it as `reasoning_policy.override`, and traces record the effort sent. |
| `LDM_RESPONSES_LITE` | service | `1` sends the Codex Responses Lite form (header `x-openai-internal-codex-responses-lite: true`, base instructions and an empty tool list as developer input items, no top-level `instructions` or `tools`, `reasoning.context: "all_turns"`) with unchanged state, questions and schema, and a pre-output rejection of that form (HTTP 400/422 or an unsupported/invalid-request error naming no field or a Lite-only field) resends the decision in the standard form and turns Lite off until restart, while `0` or unset sends the standard form and `/health` shows `responses_lite.configured` and `.active`. |
| `LDM_TRANSPORT` | service | `websocket` sends each decision as an independent `response.create` without `previous_response_id` over a Codex Responses WebSocket (`OpenAI-Beta: responses_websockets=2026-02-06`, routing hint and the same auth headers; one decision per socket, at most 4 sockets, reused only for identical handshake headers after a clean completion), and a handshake answered with HTTP 200, 400, 404, 405, 426 or 501 sends the decision over HTTP and keeps WebSocket off until restart (`websocket_connection_limit_reached` affects only that decision; 401, 403, 429 and other errors are returned as usual), while `http` or unset uses HTTP and `/health` shows `upstream_transport.configured` and `.active`. |
| `LDM_HEDGE_AFTER_MS` | service | A whole number of milliseconds below the request deadline (`0` or unset disables) limits each batch sent with reasoning effort `none` to two physical attempts, the second starting either as a hedge when the first has streamed no output text that long after it was sent and an inference slot and connection are free, or as the only retry after a retryable failure or login renewal, and returns the first validated outcome (refusals included) while cancelling the other, whose usage counts as unknown unless it completed. |
| `DECIDE_D_URL` | MCP adapter, Node preload | `/v1/systemone` URL of the service (default port 20141). |
| `DECIDE_D_CLIENT_TIMEOUT` | MCP adapter | Tool deadline in seconds (default 30). |
| `JEV_URL`, `JEV_DATA` | native Jev CLI | Service `/v1/systemone` URL; Jev's own usage directory (see [native callers](docs/INTEGRATIONS.md#native-jev-cli-read-hook-and-selector-advanced)). |

`DECIDE_D_MODEL`, `DECIDE_D_TRANSPORT`, `DECIDE_D_BETA` and `DECIDE_D_CACHE_TTL` must stay at their defaults (`gpt-6-luna`, `subscription`, `1`, `0`). Setting `DECIDE_D_UPSTREAM`, `DECIDE_D_RESPONSES_URL` or `DECIDE_D_PROXY_KEY` stops the service: external model backends are not supported.

## Python and JavaScript entry points

| Import | Returns |
| --- | --- |
| `luna_decision_model.openai_client(base_url=...)` / `async_openai_client` | Original OpenAI SDK client (sync / async) for the local `/v1` URL with the service key. Default `http://127.0.0.1:20141/v1`. Needs the `openai` extra. |
| `luna_decision_model.typesafe_client(base_url=...)` / `async_typesafe_client` | Original TypeSafe SDK client for the local root URL. Default `http://127.0.0.1:20141`. Needs the `typesafe` extra. |
| `luna_decision_model.service_key()` | The local service key, for custom HTTP clients. |
| `await luna_decision_model.request(body, protocol="jev" or "openai")` | Minimal built-in async client (used by `decide`). |
| `jev_luna/assets/sdk-local.mjs` | Node helpers `typesafeClient(TypeSafeClient, {baseURL})` and `openaiClient(OpenAI, {baseURL})`. `config --integration` prints its exact installed file URL. |

The factories keep the SDK's own timeout and retry options and ignore ambient HTTP proxies unless you pass your own HTTP client. The legacy command `jev-luna` and package `jev_luna` (`python -m jev_luna`) are the same runtime and share its state location.

## Existing host entry points

Native callers keep their upstream activation and policy; only the transport changes. Preparing them needs pinned sources, Go and Node ([guide](docs/INTEGRATIONS.md#native-jev-cli-read-hook-and-selector-advanced)).

| Caller | Connection | Preserved upstream policy |
| --- | --- | --- |
| Native Jev CLI | `ask` / `find` with the prepared binary and `JEV_URL=http://127.0.0.1:<port>/v1/systemone` | 120 s per attempt, up to five retryable attempts, exit codes, candidate policy |
| Read Hook | Original `PreToolUse` Read hook with the prepared binary | Offset/limit/disable, 400-line / 80 kB / confidence gates; the host's 20 s watchdog is not an HTTP deadline |
| Codex selector MCP | Pinned original Node bundle with the shipped `--import` preload | Original three tools, one source pass plus targeted recovery; small packets bypass the model |
| Original JS SDK | Node `--import` preload with `DECIDE_D_URL=http://127.0.0.1:<port>/v1/systemone` | Payload unchanged; repeated matching import is idempotent; conflicting configuration fails |

A selector result proves model routing only when it reports `mode=jev`; `bypass` and `local-fallback` mean the model was not used.

## Compatibility

Upstream adapter sources are pinned by commit and file hash in `jev_luna/assets/lock.json`, which ships in the package. That lock is authoritative.

| Component | Tested version | Evidence |
| --- | --- | --- |
| Codex CLI | 0.159.0 | Native login and routing |
| Python | 3.12 and 3.14 on macOS; 3.10 declared minimum | Real machine (macOS only) |
| OpenAI SDK | Python 3.29.0; JavaScript 7.32.0 (Node 22+) | Real SDK against a controlled local backend, sync and async |
| TypeSafe SDK | Python 0.6.0; JavaScript 0.6.0 (Node 20+) | Real SDK against a controlled local backend, sync and async |
| TypeSafe Claude plugin `typesafe` | 0.5.7 (`65a39f3`) | Source inspected; no live Claude host test |
| MCP Python SDK | `>=1.30,<2` | Full-chain cancellation test |
| Native Jev CLI / Read Hook | `BorisLeMeec/jev` `e81c1d0` | Locked adapter; prepared with Go 1.26.0 |
| Codex selector | `jcressler/jev-codex-token-saver` `1e1214b` | Locked adapter; prepared with Node 25.5.0 |

Codex has no version check; an incompatible Codex fails closed with a `native_routing_capability` or `native_regional_route` error.

## Security

- Loopback only. The local service key guards the service against other local processes; it is not multi-user or remote authorization.
- OAuth and refresh tokens stay with Codex, are fetched fresh per request, and are never written to logs or traces.
- Native Codex-managed OAuth is the only backend. An HTTP CONNECT proxy still sends the authorization only to the ChatGPT origin; external Responses or model relays are rejected.

To report a vulnerability, see [CONTRIBUTING.md](CONTRIBUTING.md#security-reports).

## Notes

Native host entry points were last exercised live with an earlier prerelease ([changelog](CHANGELOG.md)); 0.1.0 has offline evidence only for them. Windows is not supported yet. Not verified: Linux on real machines, Python 3.10 on a real machine, every third-party skill or plugin, and the live Claude host watchdog chain.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Project code is [MIT](LICENSE). [Third-party notices](THIRD_PARTY_NOTICES.md) and the complete upstream MIT texts ship with the adapter resources in the wheel and sdist. Research datasets keep their original licenses and are not relicensed under MIT.
