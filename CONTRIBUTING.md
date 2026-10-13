# Contributing

Read [AGENTS.md](AGENTS.md) first: it maps the repository and lists the files that must not be edited and why. In short, any byte change under `jev_luna/` or `luna_decision_model/` changes the runtime build hash, and the maintainer's local `evidence/` and `reports/` archives (git-ignored, not in the repository) are hash-bound historical record that must stay untouched.

## Workflow

Work from a fresh branch or worktree and keep each change narrow. Add tests for changed behavior, including adversarial checks for any changed boundary; never loosen a gate to make a change pass. Runtime changes must keep full inputs, the original public math, deadlines, usage accounting, fresh native credentials and zero answer caching.

## Tests and checks

```bash
python3 -m pip install '.[test,mcp]'
python3 -m pip check
python3 -m pytest -q tests
python3 scripts/render_docs_charts.py --check
```

Pytest needs no credentials or network.

CI (`.github/workflows/ci.yml`) runs the tests on Ubuntu and macOS (Windows is not supported yet) with Python 3.10, 3.12 and 3.14, then builds the sdist and wheel and smoke-tests the wheel in a fresh environment. Local macOS runs do not prove the other matrix entries.

## Documentation and charts

Chart numbers come from [`docs/comparison-data.json`](docs/comparison-data.json). Edit the data or `scripts/render_docs_charts.py`, then run `python3 scripts/render_docs_charts.py` to regenerate `docs/assets/*.svg`; do not edit the SVGs by hand. User-facing docs state each caveat once and link to it elsewhere. Release history belongs in [CHANGELOG.md](CHANGELOG.md); maintainer notes are in [docs/internal/](docs/internal/).

## Packaging

`python -m build` writes the sdist and wheel to `dist/`. `MANIFEST.in` lists what the sdist carries; update it when you add a root file or a top-level directory. Package data for the wheel is declared in `pyproject.toml`.

## Integration contract

`scripts/integration_contract.py` runs the real original Python and JavaScript SDKs, the locked native Read Hook and the locked selector MCP against the service with a local mock upstream. It isolates `HOME`, app state and native usage and makes zero subscription calls, so it proves wiring, not model quality or live host activation.

```bash
python3 -m pytest -q tests/test_integration_config.py tests/test_native_adapters.py
python3 scripts/integration_contract.py --runtime-root /absolute/installed/site-packages \
  --node-sdk-root /absolute/application/node_modules --preparation /absolute/preparation.json \
  --node /absolute/node --output /absolute/contract-receipt.json
```

`--runtime-root` must contain the installed wheel modules and assets; run it with that environment's Python and record the wheel hash with the receipt. Source preparation ([native adapters](docs/INTEGRATIONS.md#native-jev-cli-read-hook-and-selector-advanced)) and Node SDK installs are explicit prerequisites.

## Live evaluation

Run live evaluation only with an explicit account and budget allocation. Record build and input hashes, the native CLI identity, the readout profile, attempts and known/unknown usage. Keep gold labels separate from model input and keep failures in the denominators. Protocols and past results are in [docs/internal/EXPERIMENTS.md](docs/internal/EXPERIMENTS.md).

## Security reports

Report vulnerabilities through GitHub private vulnerability reporting (Security tab → Report a vulnerability); do not open a public issue. Include a minimal credential-free reproduction and the affected artifact hash. Never share OAuth or refresh tokens, service keys, raw private input or account diagnostics.
