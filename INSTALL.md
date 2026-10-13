# Install and run

The short path is the [README quickstart](README.md#quickstart). This page covers requirements, extras, running, diagnosis, MCP and removal.

## Requirements

| Need | Detail |
| --- | --- |
| Python | 3.10 or later (tested on macOS with 3.12 and 3.14) |
| Codex CLI | Native `codex` on `PATH` (0.159.0 tested); never installed or updated by this project |
| Login | Your own ChatGPT login in Codex with GPT-6 Luna access and workspace routing |

API-key logins, process-local temporary logins, missing workspace routing and non-ChatGPT origins are rejected.

## Install from source

From a checkout of this repository:

```bash
python3 -m venv .venv
.venv/bin/pip install '.[openai]'
```

| Extra | Adds |
| --- | --- |
| `openai` | Original OpenAI Python SDK, for `openai_client()` (`openai>=3.28,<4`) |
| `typesafe` | Original TypeSafe Python SDK, for `typesafe_client()` (`typesafe-sdk>=0.6,<0.7`; 0.6.0 tested). Not the TypeSafe Claude plugin or the Node SDK. |
| `mcp` | MCP stdio adapter (`mcp>=1.30,<2`), see [MCP](#mcp-optional) |
| `test` | Test dependencies, see [CONTRIBUTING.md](CONTRIBUTING.md) |

Combine extras as needed, for example `.venv/bin/pip install '.[openai,typesafe,mcp]'`.

## Run

| Command | Effect |
| --- | --- |
| `.venv/bin/luna-decision start` | Check the Codex login, then serve on `127.0.0.1:20141` in the foreground; `Ctrl-C` stops it |
| `.venv/bin/luna-decision start --login` | Same, but run `codex login` first if no login exists |
| `.venv/bin/luna-decision start --port <n>` | Use another port if 20141 is busy; an occupied port is never taken over |
| `.venv/bin/luna-decision serve` | Serve without the login pre-check |

If you use `--port <n>`, pass the same address everywhere else: `luna-decision doctor --url http://127.0.0.1:<n>`, `luna-decision config --port <n>`, and `openai_client(base_url="http://127.0.0.1:<n>/v1")`.

## Verify and diagnose

| Command | What it does | Uses subscription |
| --- | --- | --- |
| `.venv/bin/luna-decision doctor` | Checks your Codex login and the running service, and that the service runs the same build as this installation; a mismatch is a failure | No |
| `.venv/bin/luna-decision doctor --live` | Also sends small text, long-text and image requests to Luna to confirm the route works | Yes |
| `.venv/bin/luna-decision config` | Prints the local URLs, the MCP command and file locations; never prints the key | No |

`GET /health` shows only that the process is reachable. Login problems are reported as errors, never by switching model or backend. For request traces, set `DECIDE_D_TRACE_DIR` ([environment variables](TECHNICAL_REFERENCE.md#environment-variables)).

## MCP (optional)

Install the `mcp` extra, keep the HTTP service running, then register the stdio adapter with your MCP host. `luna-decision config` prints the exact entry: the environment's absolute Python path with arguments `-I -m luna_decision_model mcp`. `luna-decision config --write-mcp /absolute/project` writes it into that project's `.mcp.json`, and `--restore-mcp /absolute/project` removes it if unchanged. The tool is named `jev_decide` and keeps a 30-second tool deadline.

## Stop and uninstall

Stop the service with `Ctrl-C`. Remove any MCP entries or project connections you added (`config --restore-mcp`, `config --integration <name> --project <dir> --restore`), then delete the `.venv` directory. Your Codex login is not touched, and the app-state directory holding the service key ([`JEV_LUNA_HOME`](TECHNICAL_REFERENCE.md#environment-variables)) is kept.

## Notes

GPT-6 Luna access depends on your account, client and rollout. Supported platforms: macOS and Linux; Windows is not supported yet (loopback key protection relies on POSIX file permissions). Real-machine testing covered macOS only; Python 3.10, Linux, keyring-based Codex credential storage and regional accounts are unverified.
