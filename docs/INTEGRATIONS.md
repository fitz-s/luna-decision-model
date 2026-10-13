# Connect existing applications

Your application keeps its questions, candidates, thresholds, retries and policy. Only the connection changes: point the original SDK, or a prepared native adapter, at the local service. Start the service first ([quickstart](../README.md#quickstart)). The examples use the default port 20141, and `luna-decision` means the executable in your environment, for example `.venv/bin/luna-decision`.

| Your code uses | Do this |
| --- | --- |
| OpenAI Python or Node SDK (`decisions.create`) | [Swap the client constructor](#python-and-node-sdks) |
| TypeSafe Python or Node SDK (`system_one` / `systemOne`) | [Swap the client constructor](#python-and-node-sdks) |
| The TypeSafe Claude Code plugin | [Write a project connection rule](#project-connection), then see [the plugin notes](#official-typesafe-claude-code-plugin) |
| The native Jev CLI, Read Hook or Codex selector MCP | [Prepare locked adapters](#native-jev-cli-read-hook-and-selector-advanced) |

## Python and Node SDKs

To use the factories in your application's own environment, install this repository there with the matching extra:

```bash
pip install '/path/to/luna-decision-model[openai]'     # or [typesafe] (TypeSafe 0.6.x)
```

```python
from luna_decision_model import openai_client, async_openai_client
from luna_decision_model import typesafe_client, async_typesafe_client

openai = openai_client()          # http://127.0.0.1:20141/v1
typesafe = typesafe_client()      # http://127.0.0.1:20141
```

Each factory returns the original SDK client (`OpenAI`, `TypeSafeClient`, or their async versions) with the local URL and service key filled in. Call `decisions.create(...)` or `system_one(...)` as before, and close clients with their usual context manager. SDK timeout and retry options pass through. The factories ignore ambient HTTP proxies unless you supply your own HTTP client. Pass `base_url=` only if you changed the port; the OpenAI URL ends in `/v1`, the TypeSafe URL does not.

A TypeSafe example:

```python
from luna_decision_model import typesafe_client
from typesafe_sdk import Noul

with typesafe_client() as client:
    result = client.system_one(
        state="The switch is enabled.",
        questions={"enabled": Noul(instructions="Is it enabled?")},
    )
    print(result.nouls["enabled"].noul)
```

For Node, install the original `openai` or `@typesafe-ai/sdk` package. `luna-decision config --integration openai` (or `typesafe`) prints the exact `file://` URL of the installed `sdk-local.mjs` helper; import from that URL:

```js
import { typesafeClient, openaiClient } from "file:///absolute/installed/jev_luna/assets/sdk-local.mjs";
import { TypeSafeClient } from "@typesafe-ai/sdk";
import OpenAI from "openai";

const typesafe = typesafeClient(TypeSafeClient, {baseURL: "http://127.0.0.1:20141"});
const openai = openaiClient(OpenAI, {baseURL: "http://127.0.0.1:20141/v1"});
```

The helper creates one explicit SDK instance and reads the service key when it is constructed; it does not change global `fetch` or environment variables. Recreate the client after the key changes. It is for server-side Node only (TypeSafe 0.6.0 needs Node 20+, OpenAI 7.32.0 needs Node 22+), never for browsers.

Constructor settings take precedence over SDK environment variables, so code that hard-codes a remote base URL must be edited to use these factories.

## Project connection

`config --integration` writes a short rule into your project that tells coding agents (for example the TypeSafe skill in Claude Code) to use these factories. Every path must be absolute.

```bash
luna-decision config --integration typesafe --project /absolute/project            # preview (dry-run)
luna-decision config --integration typesafe --project /absolute/project --write    # create the files
luna-decision config --integration typesafe --project /absolute/project --check    # verify, no network
luna-decision config --integration typesafe --project /absolute/project --restore  # remove them
```

Profiles: `typesafe`, `openai`, `native-jev`, `codex-selector`. The dry-run prints the constructor snippets and the files it would create, and touches nothing: no credential read, no network, no plugin, hook or host settings.

`--write` creates three files in the project:

| File | Purpose |
| --- | --- |
| `.claude/rules/luna-decision-connection.md` | Guidance for coding agents: which constructor to use and when |
| `.luna-decision-connect.json` | Credential-free journal used by `--restore` |
| `.luna-decision-connect.lock` | Coordination lock; kept after restore to avoid races |

Existing `.claude/settings*.json`, hooks, skills, MCP configuration and your own rules are never changed. `--check` validates the rule and the local key with the same profile, project, port and `JEV_LUNA_HOME` as the write; it makes no HTTP request but may create the lock. `--restore` needs the profile and project but not the old port, and removes only the rule and journal, and only if unchanged; edited content is kept and reported. Repeating a write or restore is idempotent. Try it on a fresh project first.

## Official TypeSafe Claude Code plugin

The [`typesafe-ai/skills`](https://github.com/typesafe-ai/skills) plugin (`typesafe` 0.5.7, commit `65a39f393687675ce170e6094757de20370365b9` inspected) is a development skill that helps an agent write Noul, Choice and Score code. It is not an inference server or a hook, so installing it does not redirect anything. Keep the vendor skill unchanged and ask it to read the project connection rule when writing decision code.

The rule tells the agent to use the local constructor, read `service.key` at runtime, and keep application policy. That key only authenticates the loopback service (it is not a TypeSafe, OpenAI or Codex credential); keep it out of notes, committed code and host configuration.

## Native Jev CLI, Read Hook and selector (advanced)

The supported native callers are the [`BorisLeMeec/jev`](https://github.com/BorisLeMeec/jev) CLI and Read Hook and the [`jev-codex-token-saver`](https://github.com/jcressler/jev-codex-token-saver) selector bundle, at the commits pinned in `jev_luna/assets/lock.json`. They need adapters built from those exact sources, which requires this repository's `scripts/prepare_adapters.py` (standalone; it can be copied anywhere), Git, Go and Node.

**1. Fetch the pinned sources** into a new directory (the selector commit already contains its `dist/server.mjs`):

```bash
git clone https://github.com/BorisLeMeec/jev /absolute/pinned-upstreams/jev-cli
git -C /absolute/pinned-upstreams/jev-cli checkout --detach e81c1d006b8b23a616486610f311039088521d0c
git clone https://github.com/jcressler/jev-codex-token-saver /absolute/pinned-upstreams/codex-selector
git -C /absolute/pinned-upstreams/codex-selector checkout --detach 1e1214bd7a1d7b2d3ee32bf1b8a88bbf9cd9d820
```

**2. Build the adapters** from the repository root. Find `--asset-root` with `.venv/bin/python -I -c "import pathlib,jev_luna; print(pathlib.Path(jev_luna.__file__).parent/'assets')"`.

```bash
python3 scripts/prepare_adapters.py \
  --asset-root /absolute/installed/jev_luna/assets \
  --work-dir /absolute/new/adapter-output \
  --upstream-root /absolute/pinned-upstreams \
  --prepare-only --output /absolute/preparation.json
```

The tool checks the installed assets and the source, patch and contract locks (add `--wheel /absolute/<built>.whl` to also verify them against a built wheel), builds into fresh directories (Go may fetch locked module dependencies), and writes a receipt naming each adapter and its hash. It does not clone sources, start a model backend or configure a host.

**3. Connect with the receipt.**

- Native Jev: `luna-decision config --integration native-jev --project /absolute/project --preparation /absolute/preparation.json`. Run the prepared binary (`adapters.jev` in the receipt) with `JEV_URL=http://127.0.0.1:20141/v1/systemone` and `JEV_LUNA_HOME=<state dir>`. For isolated tests also set `JEV_DATA`, otherwise the CLI writes usage under `~/.jev`. A plugin that launches an absolute `${CLAUDE_PLUGIN_ROOT}/bin/jev` must be pointed at the prepared binary explicitly; changing `PATH` is not enough.
- Selector: `luna-decision config --integration codex-selector --project /absolute/project --preparation /absolute/preparation.json --node /absolute/node`. The generated command loads the shipped TypeSafe preload before the original bundle with `DECIDE_D_URL=http://127.0.0.1:20141/v1/systemone`. Start the HTTP service separately. The selector keeps its original MCP instructions, tool schemas, caps, sessions and 15 s caller deadline. A `local-fallback` result means the model was not reached; only `mode=jev` confirms routing. The preload rejects OpenAI Decisions URLs; use the OpenAI SDK factory for those.

Optional `--jev-bin` or `--selector-bundle` overrides must match the hashes in the receipt; an unverified path only produces a dry-run plan. `--restore` needs neither the old receipt nor the old binary. These profiles write guidance only: they never register a second Read hook or change an active host. Original activation, the Read matcher and gates, the 20 s host timeout and the 120 s HTTP timeout stay unchanged.

Example with the native CLI:

```python
import json, os, subprocess
from pathlib import Path

prepared = json.loads(Path("/absolute/preparation.json").read_text())
subprocess.run(
    [prepared["adapters"]["jev"], "ask", "Does this handle duplicate-charge refunds?",
     "/absolute/project/billing.py", "--json"],
    env={**os.environ, "JEV_URL": "http://127.0.0.1:20141/v1/systemone"},
    check=True,
)
```

Caller-by-caller boundaries are in the [technical reference](../TECHNICAL_REFERENCE.md#existing-host-entry-points).

## Other skills and callers

Skills that construct a compatible SDK client can use the factories above. Check any skill that hard-codes endpoints, launches an absolute vendor binary, requires a key before routing, uses a gateway path or asks for free-form text generation before relying on it.

Protocol references: [TypeSafe Python](https://docs.typesafe.ai/sdk/python), [TypeSafe JavaScript](https://docs.typesafe.ai/sdk/javascript), [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions), [OpenAI Python](https://github.com/openai/openai-python), [OpenAI JavaScript](https://github.com/openai/openai-node).

## Notes

The project rule guides coding agents; it does not reroute existing requests or prove every generated application compatible. Connection errors stay errors, with no fallback to a paid endpoint or another model. Adapter preparation was tested on macOS arm64 with Go 1.26.0 and Node 25.5.0 only; Windows is not supported yet, and Linux, other third-party skills and live Claude or Codex host activation are unverified.
