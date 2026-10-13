# Luna Decision Model

**Keep your Jev workflows. Run typed decisions with the Codex subscription you already have.**

A small local service that lets existing Jev / TypeSafe decision code (yes/no, choice and score questions; semantic `ask` / `find`; Read Hook; selector MCP) run on OpenAI GPT-6 Luna through your own Codex ChatGPT login, with no metered API key.

[中文](README.zh-CN.md)

## Why a decision model

Agent and app workflows ask many small typed questions: is this tool call destructive, is this text unsafe, which file answers the question. A decision model answers them with a probability or label instead of generated text, and native Jev is already used this way:

- **Agent context:** [jev](https://github.com/BorisLeMeec/jev) finds code from a description (P@1 0.96 vs grep 0.08) and cut billed agent input tokens 30–41% in paired runs; [jev-codex-token-saver](https://github.com/jcressler/jev-codex-token-saver) cut Codex input tokens 39.2% over 108 paired runs at equal quality.
- **Guardrails:** [Red Hat's benchmark](https://developers.redhat.com/articles/2026/10/02/benchmarking-ai-decision-models-against-traditional-guardrails) ranked Jev first on content safety (86.20%, 360 ms median).
- **Tool-call risk:** [jev-benchmark](https://github.com/themsquared/jev-benchmark) found Jev matched Claude Sonnet 5 (91.7%) at 422 vs 1,371 ms median and about 40× lower cost per call.

Luna Decision Model runs these same callers on GPT-6 Luna through your Codex subscription; the [benchmark](#benchmark) shows how it compares.

## How it works

![Your Jev / TypeSafe app calls the local Luna Decision Model service, which calls GPT-6 Luna with your Codex login.](docs/assets/architecture.svg)

## Cost

![Cost per 1,000 requests: native Jev $0.026, GPT-6 Luna API $0.075, Luna Decision Model about 1.9 credits at GPT-6 Luna's credit rate.](docs/assets/cost.svg)

## Benchmark

![Jev, GPT-6 Luna through the Decisions API and through this project: accuracy, median latency and Brier on 460 public tasks and on Decision Bench v4, with cost per 1,000 requests.](docs/assets/public-comparison.svg)

p95 latency, calibration detail and rate sources: [docs/COMPARISON.md](docs/COMPARISON.md). Your own plan's usage: [docs/QUOTA.md](docs/QUOTA.md).

## Quickstart

You need Python 3.10+, the Codex CLI on your `PATH`, and a ChatGPT login with Luna access ([details](INSTALL.md#requirements)).

**1. Install and start** from a checkout of this repository:

```bash
python3 -m venv .venv
.venv/bin/pip install '.[openai]'
.venv/bin/luna-decision start
```

The service now runs in this terminal on `127.0.0.1:20141`; `Ctrl-C` stops it.

**2. Check it** in a second terminal, same directory:

```bash
.venv/bin/luna-decision doctor
```

**3. Ask a first question:**

```bash
.venv/bin/python -I - <<'PY'
from luna_decision_model import openai_client

with openai_client() as client:
    result = client.decisions.create(
        model="gpt-6-luna",
        input="The parcel arrived with a broken screen.",
        questions=[{"type": "predicate", "instructions": "Is the item damaged?"}],
    )
    print(result.answers[0].probability)
PY
```

## Connect your project

```bash
.venv/bin/luna-decision config --integration typesafe --project /absolute/path/to/your-project
```

This previews the change; add `--write` to apply it and `--restore` to undo it. It writes a rule telling coding agents to build clients with `typesafe_client()`. Use `--integration openai` for OpenAI SDK code. Native Jev CLI, Read Hook and selector setups: [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md).

## Documentation

| Read | For |
| --- | --- |
| [INSTALL.md](INSTALL.md) | Options, diagnosis, MCP, uninstall |
| [docs/INTEGRATIONS.md](docs/INTEGRATIONS.md) | SDKs, the TypeSafe plugin, native Jev callers |
| [docs/COMPARISON.md](docs/COMPARISON.md) · [docs/QUOTA.md](docs/QUOTA.md) | Full benchmark, prices, allowance estimates |
| [TECHNICAL_REFERENCE.md](TECHNICAL_REFERENCE.md) | API, CLI, environment variables, security |
| [CHANGELOG.md](CHANGELOG.md) · [CONTRIBUTING.md](CONTRIBUTING.md) · [AGENTS.md](AGENTS.md) | History, development, AI-agent entry |

## Notes

Unofficial and not affiliated with OpenAI; GPT-6 Luna access depends on your account, and there is no paid-API fallback. Benchmark and cost figures come from historical, non-paired runs ([conditions](docs/COMPARISON.md)).

[MIT](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)
