# Luna compared with native Jev

![Jev, GPT-6 Luna through the Decisions API and through this project: accuracy, median latency and Brier on 460 public tasks and on Decision Bench v4, with cost per 1,000 requests.](assets/public-comparison.svg)

| Suite | Correct: Luna / Jev | p50 latency: Luna / Jev | p95 latency: Luna / Jev | Brier (lower is better): Luna / Jev |
| --- | --- | --- | --- | --- |
| Tool-call risk (60) | 55 / 55 | 1.20 s / 0.42 s | 2.03 s / 0.54 s | 0.164 / 0.116 (multiclass) |
| Toxicity (200) | 160 / 157 | 1.09 s / 0.15 s | 1.85 s / 0.20 s | 0.164 / 0.149 (binary) |
| Tool selection (200) | 198 / 199 | 1.12 s / 0.15 s | 2.21 s / 0.22 s | 0.016 / 0.005 (multiclass) |

Provenance: Luna's correct counts, latency and Brier all come from the same archived public-460 run; native Jev figures are its benchmark authors' published results. Details under [Sources](#sources).

- **Accuracy** is similar on all three suites.
- **Latency** is end-to-end wall time including network, as recorded in the archived run.
- **Calibration**: Luna's Brier score and selected-class (max-p) ECE were worse on all three suites. Binary and multiclass Brier use different definitions and are not averaged.

Risk uses 60 author-labelled agent tool calls; toxicity binarizes Civil Comments crowd scores at 0.5; tool selection uses BFCL multiple-function selection gold (function choice only, not arguments or execution). Native Jev ran as `jev-latest` for risk and `jev-1.13.0` for the other two. Unrounded values, Wilson intervals, calibration bins and ECE: [comparison-data.json](comparison-data.json).

## Same model through the Decisions API

[Decision Bench v4](https://github.com/atlanai/decision-bench/pull/15) (merge commit `8d1563c`) ran Jev and `gpt-6-luna` through OpenAI's Decisions API on the same 1,071 rows. This project does not call the Decisions API (see [OPENAI_SOURCES.md](OPENAI_SOURCES.md)), so its numbers are not in this table, but the model is the same.

| Decision Bench v4 | Jev 1.13 | GPT-6 Luna, Decisions API |
| --- | --- | --- |
| Correct, all 1,071 rows | 990 (92.4%) | 969 (90.5%) |
| p50 / p95 latency | 439 / 545 ms | 326 / 585 ms |
| Brier (lower is better) | 0.112 | 0.138 |
| AGT-1, which tool to call (BFCL, 36 rows) | 35 / 36 | 35 / 36 |
| SAF-2, toxic comment (Civil Comments, 30 rows) | 29 / 30 | 29 / 30 |

Figures are from each model's published `scores.json` ([Jev](https://github.com/atlanai/decision-bench/blob/8d1563c8740600367172f4bffd2858f400bdd32d/results/bench-v4/jev-1.13/scores.json), [Luna](https://github.com/atlanai/decision-bench/blob/8d1563c8740600367172f4bffd2858f400bdd32d/results/bench-v4/gpt-6-luna-decisions/scores.json)); the benchmark does not publish per-task latency or Brier. The benchmark's Brier sums squared error over all classes for every task, so it differs from the binary Brier above and is not comparable with it. Luna's run (2026-10-08, harness `9cac8f8`) went through OpenRouter, and Jev's ran on 2026-09-23 with harness `4b919ae`. These are different datasets and an OpenRouter hop, not a paired comparison with the public-460 suites.

## Cost per 1,000 requests

![Per 1,000 requests: $0.026 to $0.075 on API rate cards, about 1.9 credits at GPT-6 Luna's credit rate.](assets/cost.svg)

At the token mix measured in the archived Luna run (284,370 input and 11,742 output tokens over 460 requests, about **618 input + 26 output tokens per request**):

| Route | Rate per 1M tokens (input / output) | Per 1,000 requests |
| --- | --- | --- |
| [Native TypeSafe Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev), same token mix | $0.042 / no charge | $0.026 |
| [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability), `gpt-6-luna` | $0.10 / no charge | $0.062 |
| [GPT-6 Luna API](https://developers.openai.com/api/docs/pricing), Standard short context | $0.10 / $0.50 | $0.075 |
| **Luna Decision Model**, at [GPT-6 Luna's credit rate](https://learn.chatgpt.com/docs/pricing#token-rates) | 2.5 / 12.5 credits | ≈ 1.9 credits |

Computation, once: per 1,000 requests the mix is 0.618M input + 0.0255M output tokens, so Luna API = 0.618 × $0.10 + 0.0255 × $0.50 = $0.075, and credits = 0.618 × 2.5 + 0.0255 × 12.5 ≈ 1.9. [Official Codex pricing](https://learn.chatgpt.com/docs/pricing) lists Plus at $20/month and Pro from $100/month. The credit figure applies the credit rate to this token mix; it is not a published conversion to included plan use, which you measure with [QUOTA.md](QUOTA.md).

## When to use it

Use it to reuse an existing Codex subscription and existing Jev callers. If latency or calibrated probability thresholds matter most, use the dedicated services; for known symbols, plain text search is faster still. The pinned [Jev application](https://github.com/BorisLeMeec/jev/blob/e81c1d006b8b23a616486610f311039088521d0c/README.md#results) reports fewer agent tokens for semantic find, ask and Read Hook narrowing but no wall-clock saving; those experiments have not been rerun with Luna.

## Sources

- Luna accuracy, latency and Brier: `acceptance-20261011/public-460-metrics.json` in the maintainer's local archive (not in the repository; SHA-256 recorded in [comparison-data.json](comparison-data.json)), produced by the retained `run_public.py` and `score_public.py` runners, which keep gold labels out of inference requests.
- [Tool-call risk benchmark](https://github.com/themsquared/jev-benchmark/tree/42a9123a4ef42488aa775effdecb2f34a0d66a5a), commit `42a9123`, published September 2026.
- [Toxicity and tool-selection benchmark](https://github.com/supreethbare/decision-model-bench/tree/f69fd43641ef756715e2238b36f0b40be36a3958), commit `f69fd43`, September 2026, one run with per-case predictions.
- [Decision Bench v4](https://github.com/atlanai/decision-bench/tree/8d1563c8740600367172f4bffd2858f400bdd32d/results/bench-v4), commit `8d1563c` (pull request 15), October 2026; file hashes in [comparison-data.json](comparison-data.json).
- Official OpenAI statements and prices: [OPENAI_SOURCES.md](OPENAI_SOURCES.md).

## Notes

Luna's figures come from an archived summary of an earlier build whose per-request traces are unavailable; they are not a measurement of 0.1.0 speed. Native Jev's figures are its benchmark authors' published results. None of these runs are paired, so they support no exact speed ratio or overall winner. Costs are rate-card arithmetic at one historical token mix, assuming uncached input and no regional, long-context or tier premiums, tax or retries; your mix will differ. Credit figures use OpenAI's published Luna credit rates, which OpenAI says do not by themselves determine included plan use. OpenAI's own Decisions speed claim is not a measurement of this project.
