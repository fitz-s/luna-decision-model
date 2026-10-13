# Luna compared with native Jev

![Jev, GPT-6 Luna through the Decisions API and through this project: accuracy, Brier and median latency by task group on Decision Bench v4.](assets/public-comparison.svg)

[Decision Bench v4](https://github.com/atlanai/decision-bench/pull/15) (harness commit `8d1563c`): 1,071 rows in 35 tasks. Native Jev 1.13 and `gpt-6-luna` through the Decisions API are the benchmark's published runs; the third series is `gpt-6-luna` through this project, run on the same rows on 2026-10-13 with the benchmark's own scoring code.

## Overall

| Series | Correct | Accuracy (95% Wilson) | Brier (lower is better) | ECE | p50 latency | p95 latency | Error rows |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Jev native | 990 / 1,071 | 92.4% (90.7% to 93.9%) | 0.112 | 0.013 | 0.44 s | 0.55 s | 0 |
| GPT-6 Luna, Decisions API | 969 / 1,071 | 90.5% (88.6% to 92.1%) | 0.138 | 0.021 | 0.33 s | 0.59 s | 0 |
| GPT-6 Luna, this project | 973 / 1,071 | 90.8% (89.0% to 92.4%) | 0.167 | 0.074 | 1.74 s | 3.43 s | 1 |

| Rows | Correct: Jev / Decisions API / this project | Brier: Jev / Decisions API / this project |
| --- | --- | --- |
| Text (949) | 884 / 849 / 852 | 0.105 / 0.153 / 0.187 |
| Image (122) | 106 / 120 / 121 | 0.166 / 0.025 / 0.016 |

This project minus Jev: -1.6 points on all rows (interval -4.5 to +2.2), -3.4 on text rows (-5.2 to -1.9), +12.3 on image rows (-1.6 to +40.0); intervals are task-clustered bootstrap, 500 repetitions. Jev answers more of the text rows correctly; the overall gap is smaller because Jev, without vision, got fewer of the image rows right.

## Per task

Each cell lists Jev / Decisions API / this project. An asterisk marks tasks whose rows are images.

| Task | Question | Rows | Correct | Brier | p50 latency (s) |
| --- | --- | --- | --- | --- | --- |
| AGT-1 | Which tool to call | 36 | 35 / 35 / 36 | 0.049 / 0.052 / 0.000 | 0.44 / 0.32 / 1.96 |
| AGT-2 | Did the injection succeed? | 30 | 30 / 28 / 27 | 0.007 / 0.110 / 0.200 | 0.44 / 0.33 / 1.84 |
| AGT-3 | Did the agent complete the task? | 30 | 23 / 18 / 22 | 0.301 / 0.596 / 0.533 | 0.45 / 0.33 / 2.80 |
| AGT-4 | Is the answer backed by sources? | 30 | 21 / 23 / 20 | 0.399 / 0.357 / 0.654 | 0.44 / 0.32 / 1.64 |
| AGT-5 | Which reply is better? | 28 | 26 / 22 / 26 | 0.098 / 0.260 / 0.159 | 0.44 / 0.34 / 1.73 |
| COM-1 | Does this product match the query? | 30 | 24 / 20 / 21 | 0.321 / 0.406 / 0.589 | 0.44 / 0.33 / 2.14 |
| COM-2 | What type of product is this? | 30 | 30 / 30 / 30 | 0.000 / 0.000 / 0.000 | 0.44 / 0.33 / 1.76 |
| COM-3 | Which industry is this company in? | 30 | 30 / 30 / 30 | 0.005 / 0.011 / 0.000 | 0.42 / 0.32 / 1.82 |
| DAT-1 | Which SQL answers the question? | 30 | 30 / 30 / 30 | 0.001 / 0.000 / 0.000 | 0.42 / 0.32 / 1.45 |
| DAT-2 | Which cell answers the question? | 30 | 29 / 28 / 26 | 0.040 / 0.126 / 0.271 | 0.43 / 0.32 / 1.63 |
| DAT-3* | Does the chart support the claim? | 32 | 32 / 32 / 31 | 0.000 / 0.019 / 0.062 | 0.44 / 0.39 / 1.72 |
| DAT-4 | What does this column hold? | 34 | 33 / 33 / 31 | 0.032 / 0.057 / 0.166 | 0.46 / 0.31 / 1.50 |
| DOC-1* | Where is this page from? | 30 | 30 / 30 / 30 | 0.000 / 0.000 / 0.000 | 0.49 / 0.46 / 1.71 |
| DOC-2 | Decision, action item, or neither? | 30 | 21 / 17 / 18 | 0.491 / 0.558 / 0.691 | 0.47 / 0.32 / 1.47 |
| DOC-3 | Which summary is this meeting's? | 27 | 25 / 23 / 21 | 0.173 / 0.236 / 0.444 | 0.46 / 0.32 / 1.45 |
| DSN-1* | What does this icon mean? | 30 | 14 / 28 / 30 | 0.676 / 0.083 / 0.000 | 0.46 / 0.32 / 1.62 |
| ENG-1 | Which file must change? | 30 | 30 / 30 / 29 | 0.013 / 0.013 / 0.069 | 0.45 / 0.34 / 1.64 |
| ENG-2 | What kind of change is this? | 30 | 28 / 24 / 26 | 0.097 / 0.294 / 0.230 | 0.44 / 0.33 / 1.60 |
| ENG-3 | Which weakness class is this? | 30 | 29 / 27 / 28 | 0.080 / 0.167 / 0.126 | 0.43 / 0.32 / 1.73 |
| ENG-4 | Live secret or placeholder? | 30 | 29 / 27 / 29 | 0.134 / 0.207 / 0.074 | 0.42 / 0.34 / 1.68 |
| ENG-5 | Which message describes this diff? | 30 | 30 / 30 / 30 | 0.003 / 0.000 / 0.000 | 0.43 / 0.33 / 1.81 |
| FIN-1 | Which part of the 10-K? | 30 | 29 / 29 / 29 | 0.081 / 0.088 / 0.072 | 0.43 / 0.32 / 1.87 |
| FIN-2 | What does this 8-K report? | 30 | 30 / 30 / 30 | 0.002 / 0.003 / 0.001 | 0.42 / 0.31 / 2.01 |
| FIN-3* | Which figure is the total? | 30 | 30 / 30 / 30 | 0.000 / 0.000 / 0.000 | 0.42 / 0.48 / 2.30 |
| FIN-4 | What does this number report? | 31 | 31 / 31 / 31 | 0.000 / 0.007 / 0.000 | 0.43 / 0.32 / 1.97 |
| LEG-1 | Does the NDA say this? | 33 | 28 / 24 / 25 | 0.272 / 0.351 / 0.482 | 0.43 / 0.32 / 2.02 |
| LEG-2 | What kind of clause is this? | 30 | 30 / 30 / 30 | 0.000 / 0.004 / 0.000 | 0.43 / 0.32 / 1.56 |
| LEG-3 | Is this term unfair, and how? | 33 | 33 / 30 / 32 | 0.030 / 0.139 / 0.056 | 0.45 / 0.32 / 1.78 |
| LEG-4 | Does the contract cover this? | 30 | 30 / 30 / 30 | 0.014 / 0.004 / 0.000 | 0.45 / 0.32 / 1.33 |
| PRD-1 | Which headline won? | 30 | 17 / 17 / 13 | 0.445 / 0.489 / 0.774 | 0.44 / 0.32 / 1.55 |
| PRD-2 | Major, minor or patch? | 30 | 30 / 30 / 29 | 0.009 / 0.006 / 0.057 | 0.42 / 0.32 / 1.42 |
| SAF-1 | Attack on the assistant | 30 | 29 / 29 / 28 | 0.052 / 0.105 / 0.114 | 0.42 / 0.32 / 1.90 |
| SAF-2 | Toxic comment or not | 30 | 29 / 29 / 30 | 0.038 / 0.045 / 0.007 | 0.43 / 0.32 / 1.77 |
| SUP-1 | Which team owns this complaint? | 35 | 35 / 35 / 35 | 0.001 / 0.000 / 0.004 | 0.43 / 0.33 / 1.85 |
| SUP-2 | What is the complaint about? | 32 | 30 / 30 / 30 | 0.105 / 0.107 / 0.131 | 0.42 / 0.32 / 1.94 |

## Run

| | Native Jev | Decisions API | This project |
| --- | --- | --- | --- |
| Run date (UTC) | 2026-09-23 | 2026-10-08 | 2026-10-13 |
| Harness commit | `4b919ae` (working tree dirty) | `9cac8f8` | `8d1563c` |
| Model / route | `jev-1.13.0`, TypeSafe cloud | `gpt-6-luna` Decisions API via OpenRouter | `gpt-6-luna`, Codex ChatGPT login, build `3a86e9e` |
| Scores file SHA-256 | `11b05411b307` | `5f36077296a0` | local run `20261013T160021Z` |

This project's run: build `3a86e9e`, `build_sha256` `fbd8ac27b9c1`, codex-cli 0.159.0, corpus SHA-256 `3599baea0d9c`. Wire `auto`: 949 text rows on `/v1/systemone`, byte-identical to the published request except the model, and 122 image rows on `/v1/decisions`. 3 parallel jobs, 180 s timeout, up to 3 attempts per row, default service tier, reasoning effort left on auto. Brier is the benchmark's: per row the sum of squared error over all classes, so a binary task scores twice a single squared error. All figures: [comparison-data.json](comparison-data.json).

Series differ in date, harness commit and machine, so none of this is a paired test. Jev has no vision input and scored the 122 image rows from text renderings; both Luna series were sent the images. Latency for the Decisions API was timed by the benchmark authors through OpenRouter, and Jev by its own harness; ours is wall time on one machine through the Codex app-server. One PRD-1 row ended in `invalid_output` after three attempts and counts as wrong.

## Probability shrink

On this run the service's probabilities are overconfident: 82.6% of rows return a one-hot vector, and 66 wrong answers carried confidence of 0.99 or more. The optional `LDM_PROBABILITY_SHRINK` ([formula and how to fit it](../TECHNICAL_REFERENCE.md#environment-variables)) moves every returned vector toward uniform without changing any answer. With `a = 0.10`, fit on these 1,070 rows and scored out-of-fold with whole tasks held out (35-fold by task, 11-fold by category), Brier goes from 0.1675 to 0.161 (0.1614 and 0.1617), log loss from 1.63 to 0.35, ECE from 0.074 to 0.011 (0.012 and 0.010), and wrong answers at confidence 0.99 or more from 66 to 0; accuracy does not change. The Brier gain is small (paired 95% interval -0.013 to +0.0004, task-clustered bootstrap); log loss, ECE and the confident-error count move clearly. It beats the raw Brier on 14 of 35 tasks: it helps the low-accuracy tasks and costs the near-perfect ones (the finance and design tasks). Applied to the published Jev and Decisions API predictions it makes them worse, Brier 0.1115 to 0.1171 and 0.1376 to 0.1419, because they are already calibrated (ECE 0.014 and 0.020), so it is off by default.

## Reasoning effort

With reasoning effort on auto, the service chose medium for 174 rows (99 image, 75 text) and none for the other 897. Those 174 rows were run again with `LDM_REASONING_EFFORT=none` (run `20261013T160021Z-slice-none`).

| Rows | Effort | Correct | Brier | p50 | p95 | Reasoning tokens | Output tokens incl. reasoning | Credits |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| All (174) | medium | 156 | 0.207 | 1.98 s | 4.83 s | 4,718 | 9,433 | 1.215 |
| All (174) | none | 147 | 0.313 | 1.61 s | 2.69 s | 0 | 4,664 | 1.165 |
| Text (75) | medium | 58 | 0.454 | 2.34 s | 5.36 s | 3,822 | 5,752 | 0.617 |
| Text (75) | none | 51 | 0.645 | 1.45 s | 2.70 s | 0 | 1,923 | 0.579 |
| Image (99) | medium | 98 | 0.020 | 1.90 s | 3.26 s | 896 | 3,681 | 0.598 |
| Image (99) | none | 96 | 0.061 | 1.70 s | 2.58 s | 0 | 2,741 | 0.586 |

Medium reasoning earns its cost on long text: 58 against 51 of 75 correct and Brier 0.454 against 0.645, with 11 rows fixed and 4 broken. The gain sits in `AGT-3` (22 against 17 of 30) and `DOC-3` (19 against 16 of 24). On image rows it changes little: 98 against 96 of 99. Medium costs 4% more credits over the 174 rows (7% on text) and raises p95 from 2.69 s to 4.83 s, 1.8 times. One run per arm; the none arm's credits include one `DAT-2` request that made three upstream calls.

## Earlier run: 460 public tasks

Archived run of an earlier build against published Jev results on three other suites; kept for the calibration detail and the Wilson intervals.

| Suite | Correct: Luna / Jev | p50 latency: Luna / Jev | p95 latency: Luna / Jev | Brier (lower is better): Luna / Jev |
| --- | --- | --- | --- | --- |
| Tool-call risk (60) | 55 / 55 | 1.20 s / 0.42 s | 2.03 s / 0.54 s | 0.164 / 0.116 (multiclass) |
| Toxicity (200) | 160 / 157 | 1.09 s / 0.15 s | 1.85 s / 0.20 s | 0.164 / 0.149 (binary) |
| Tool selection (200) | 198 / 199 | 1.12 s / 0.15 s | 2.21 s / 0.22 s | 0.016 / 0.005 (multiclass) |

Accuracy is similar on all three suites. Latency is end-to-end wall time including network, as recorded in the archived run. Luna's Brier score and selected-class (max-p) ECE were worse on all three suites; binary and multiclass Brier use different definitions and are not averaged, and neither is comparable with the Decision Bench Brier above. Luna's figures come from one archived summary (`acceptance-20261011/public-460-metrics.json` in the maintainer's local archive, SHA-256 in [comparison-data.json](comparison-data.json)); native Jev figures are its benchmark authors' published results, `jev-latest` for risk and `jev-1.13.0` for the other two. Risk uses 60 author-labelled agent tool calls; toxicity binarizes Civil Comments crowd scores at 0.5; tool selection uses BFCL function-selection gold (function choice only, not arguments or execution).

## Cost per 1,000 requests

![Per 1,000 requests: $0.026 to $0.075 on API rate cards, about 1.9 credits at GPT-6 Luna's credit rate.](assets/cost.svg)

At the token mix measured in the archived 460-task run (284,370 input and 11,742 output tokens over 460 requests, about **618 input + 26 output tokens per request**):

| Route | Rate per 1M tokens (input / output) | Per 1,000 requests |
| --- | --- | --- |
| [Native TypeSafe Jev](https://typesafe.ai/blog/introducing-system-one-models-and-jev), same token mix | $0.042 / no charge | $0.026 |
| [OpenAI Decisions](https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability), `gpt-6-luna` | $0.10 / no charge | $0.062 |
| [GPT-6 Luna API](https://developers.openai.com/api/docs/pricing), Standard short context | $0.10 / $0.50 | $0.075 |
| **Luna Decision Model**, at [GPT-6 Luna's credit rate](https://learn.chatgpt.com/docs/pricing#token-rates) | 2.5 / 12.5 credits | ≈ 1.9 credits |

Computation, once: per 1,000 requests the mix is 0.618M input + 0.0255M output tokens, so Luna API = 0.618 × $0.10 + 0.0255 × $0.50 = $0.075, and credits = 0.618 × 2.5 + 0.0255 × 12.5 ≈ 1.9. [Official Codex pricing](https://learn.chatgpt.com/docs/pricing) lists Plus at $20/month and Pro from $100/month. The credit figure applies the credit rate to this token mix; it is not a published conversion to included plan use, which you measure with [QUOTA.md](QUOTA.md). Costs are rate-card arithmetic at one historical token mix, assuming uncached input and no regional, long-context or tier premiums, tax or retries; your mix will differ.

## When to use it

Use it to reuse an existing Codex subscription and existing Jev callers. If latency or calibrated probability thresholds matter most, use the dedicated services; for known symbols, plain text search is faster still. The pinned [Jev application](https://github.com/BorisLeMeec/jev/blob/e81c1d006b8b23a616486610f311039088521d0c/README.md#results) reports fewer agent tokens for semantic find, ask and Read Hook narrowing but no wall-clock saving; those experiments have not been rerun with Luna.

## Sources

- Decision Bench v4: [published results](https://github.com/atlanai/decision-bench/tree/8d1563c8740600367172f4bffd2858f400bdd32d/results/bench-v4) at commit `8d1563c` (pull request 15), October 2026; file hashes in [comparison-data.json](comparison-data.json). This project's run is kept in the maintainer's local archive (`evidence/decision-bench-v4-20261013`, not in the repository).
- [Tool-call risk benchmark](https://github.com/themsquared/jev-benchmark/tree/42a9123a4ef42488aa775effdecb2f34a0d66a5a), commit `42a9123`, published September 2026.
- [Toxicity and tool-selection benchmark](https://github.com/supreethbare/decision-model-bench/tree/f69fd43641ef756715e2238b36f0b40be36a3958), commit `f69fd43`, September 2026, one run with per-case predictions.
- Official OpenAI statements and prices: [OPENAI_SOURCES.md](OPENAI_SOURCES.md).

Credit figures use OpenAI's published Luna credit rates, which OpenAI says do not by themselves determine included plan use. OpenAI's own Decisions speed claim is not a measurement of this project.
