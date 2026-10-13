# Evaluation and readout experiments

Maintainer notes: readout-profile experiments and their historical results. Release history is in [CHANGELOG.md](../../CHANGELOG.md); the user-facing comparison is [docs/COMPARISON.md](../COMPARISON.md).

The default is `baseline`. The frozen baseline 32-question split (24 + 8), two-batch concurrency, none/medium reasoning selection, 8192-byte reasoning-selection boundary and public normalization remain fixed. Packaging a new candidate does not convert an experimental score into default qualification.

| Profile | Scope / evidence |
| --- | --- |
| `baseline` | Production default; full original inputs/public math |
| `compact-weight-v1` | Complete vector format experiment |
| `probability-v1` | Probability instruction experiment |
| `compact-probability-v1` | Combined experiment; no independent promotion evidence |
| `blocked-weight-v1` | Historical rc5 failure: 45/60 → 24/60 correct; rejected despite shorter latency |
| `keyed-weight-v1` | Historical rc5 named short IDs; partial quality gains, failure and median-latency gates not all passed |
| `keyed-probability-v2` | rc6 independently evaluated candidate, opt-in; default stays baseline |

Experimental profiles are explicit process configuration, e.g. `LDM_READOUT_PROFILE=keyed-probability-v2 luna-decision start --port <free port>`. Preserve existing listeners. Health and `X-LDM-Readout-Profile` identify the actual profile. Every request retains all questions, options and descriptions; never fill missing tail probabilities, clip zeros, delete failures or tune application thresholds to make a result pass.

## rc6 protocol and budget

The authorized development gate uses 24 cases × baseline/keyed-weight-v1/new candidate (72 logical requests). Only if the predeclared gate passes may an independent CLINC source/task holdout with a conservative intent-group proxy (original seed/template lineage is unavailable) use 96 cases × two profiles (192). Ceiling: 264 logical HTTP requests, 850 actual upstream attempts including auth refresh, 1.5 million observed tokens. Native subscription omits unsupported `max_output_tokens`; an in-flight terminal response may cross the observed-token threshold, which is not a hard wire cap. Unknown usage stops the next trial; failures remain in denominators. No SDB run is authorized by this budget. Run this lane separately from final-artifact native acceptance; installation/client live checks have their own allocation.

Keep gold separate from model input and score only after inference completes. Report exact labelled denominators, full-vector Brier, max-probability ECE, untrimmed NLL (zero gold probability is infinity), latency distribution, failures, retries, observed/unknown usage and actual attempts. Paired/grouped uncertainty and all promotion gates matter; a better point estimate alone is insufficient. Stochastic behavior requires independent repeated evidence and remains default off.

The reproduction scripts (`rebuild_quality_rc6.py`, `prepare_quality_rc6.py`, `run_quality_rc6.py`, `score_quality_rc6.py`, formerly under `tools/`) were removed from the tree in the open-source cleanup; recover them from git history at commit `54c73b3` or earlier. Use a new isolated runtime directory; their source/download/license/hash manifest describes exact inputs. Verify source hashes before reproducing, respect source licenses, and allocate a native-account budget before running the model.

## rc6 development result

The immutable research wheel `f22ab2e…` completed 72/72 development requests, with 72 physical upstream attempts, 99,649 observed tokens and zero unknown-usage attempts. Baseline was 23/24 correct with 24 valid vectors; keyed-weight-v1 was 21/24 with one invalid vector; keyed-probability-v2 was 22/24 with 24 valid vectors. Failures remain in the 24-case denominator.

The new candidate's six-case binary Brier improved from 0.1449 to 0.13355, while 18-case multiclass Brier worsened from 0.035625 to 0.062377; these are separate task/type metrics. Its all-request p95 was 8.078s versus baseline 4.343s. Both controls had zero extreme errors in this small batch, so the proposed extreme-error mechanism had no discriminating evidence. Brier and latency gates failed. The 96-case holdout remains unconsumed; no SDB calls were made and no profile was promoted. This does not show a default-model performance improvement.

Sanitized `STATUS.json`, `DEVELOPMENT_SUMMARY.json`, fixed source/license manifests and exact-rebuild verification were recorded with the study. The final wheel has its own container hash and explicit runtime-file binding to the research wheel; a non-inference fix does not turn the study into a final-wheel model test.

## Caller input boundary

An existing historical 20-case selector projection audit found complete facts visible in 15 cases and five clipped by the original caller's excerpt projection. This is a caller-input boundary: rc6 neither changes clipping nor re-tests those 20 cases. A model profile cannot establish that it read facts never sent on the wire.

## Historical evidence

rc3 static same-question historical results: Luna 533/627, published Jev 526/627, published OpenAI Decisions 525/627. Luna failures remain errors; six ambiguous cases are excluded from labelled accuracy. Those results do not prove current installed-artifact quality.

Historical rc2 SDB: final correctness 59.17%, primary real-time log-AUC 25.33%. Published Jev and OpenAI Decisions log-AUC references were 58.50% and 32.39%. rc5 keyed candidate SDB was 29.95% log-AUC / 76.46% final correctness in one different run; its failures and attempts were retained. Different dates, machines, routes and repetitions prevent a same-condition latency or quality-equivalence claim. Original SDB scheduler/scorer remains the reference; no rc6 SDB was authorized.

rc5 H3 short IDs yielded 43/60 → 47/60 correct and paired Brier improvement, but failures 1 → 2 and p50 4.298s → 4.534s. NLL was infinite in both. Its default-promotion gate did not pass. The native subscription route rejected `top_logprobs`; an include-only probe returned no logprobs. Missing probability data must not become fabricated complete distributions. No paid Decisions API or alternate model was used.

## Pinned product references

These are implementation references, not competing local benchmark runs or features automatically supported by this package:

| Reference | Fixed commit | Applied lesson / boundary |
| --- | --- | --- |
| [OpenAI Python 3.29.0](https://github.com/openai/openai-python/blob/7e1643a67322956ac81fc7d0c3f0f9161e2e5482/src/openai/resources/decisions.py#L128) | `7e1643a67322956ac81fc7d0c3f0f9161e2e5482` | Original sync/async SDK types and exact installed-version receipts |
| [Codex 0.159.0](https://github.com/openai/codex/blob/687a119f0fcaace47e1f1abcc77cec6c813fd6da/codex-rs/protocol/src/models.rs#L883) | `687a119f0fcaace47e1f1abcc77cec6c813fd6da` | Native account and image capability must be tested, not inferred from SDK comments |
| [llama.cpp server](https://github.com/ggml-org/llama.cpp/blob/4413a192410fad1f054e9216114087a3e01527a0/tools/server/README.md#L472) | `4413a192410fad1f054e9216114087a3e01527a0` | Separate process health, capability and metrics; no new model/cache/scheduler adopted |
| [Jev selector](https://github.com/jcressler/jev-codex-token-saver/blob/1e1214bd7a1d7b2d3ee32bf1b8a88bbf9cd9d820/src/mcp-server.mjs#L63) | `1e1214bd7a1d7b2d3ee32bf1b8a88bbf9cd9d820` | Preserve locked application policy; describe tool inputs/annotations |
| [MCP Python 1.30.0](https://github.com/modelcontextprotocol/python-sdk/blob/8c2fa6eabacde869d75568e33b9d466b58c2f512/src/mcp/server/fastmcp/server.py#L462) | `8c2fa6eabacde869d75568e33b9d466b58c2f512` | Installed tool schema/metadata require verification |
| [StreamDecisionBench](https://github.com/JacobLinCool/StreamDecisionBench/blob/a0aff621a8831c534dd210a49cd40fa14ceb14a8/scripts/lite/lite_report.py#L322) | `a0aff621a8831c534dd210a49cd40fa14ceb14a8` | Reuse explicit offline run reports; historical scores remain separate |

Official image guidance is inconsistent: the [Decisions guide](https://developers.openai.com/api/docs/guides/decisions) describes inline images, while the [current Python API reference](https://developers.openai.com/api/reference/python/resources/decisions/methods/create) allows HTTP(S) image strings and the [vision guide](https://developers.openai.com/api/docs/guides/images-vision#giving-a-model-images-as-input) describes URL input. Client acceptance of a string does not establish native subscription capability. The current product's declared image boundary and exact-artifact native receipts take precedence over extrapolating from these documents.
