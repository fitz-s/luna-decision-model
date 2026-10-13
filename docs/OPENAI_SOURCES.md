# Official sources and inference path

Sources behind this project's public statements.

## Model and access

| Official page | Excerpt | Supports |
| --- | --- | --- |
| [Models](https://learn.chatgpt.com/docs/models#recommended-models) | “GPT-6 Luna” · “gpt-6-luna” | OpenAI's public name and identifier; availability depends on account, client and rollout |
| [Decisions](https://developers.openai.com/api/docs/guides/decisions) | “The Decisions API is in public beta” · “gpt-6-luna” | The dedicated API uses the same identifier at `POST /v1/decisions` |
| [Codex authentication](https://developers.openai.com/codex/auth) | “Sign in with ChatGPT for subscription access” · “Sign in with an API key for usage-based access” | Subscription sign-in and API-key access are separate bases |

## Route this project uses

The service sends existing Codex-managed ChatGPT OAuth to ordinary Codex Responses: `jev_luna/subscription_auth.py` defines the ChatGPT origin and `/backend-api/codex/responses`, `jev_luna/transport.py` prepares the native account route, and `jev_luna/core.py` compiles a structured readout and renders compatible answers. Native Codex RPC supplies authorization and workspace routing. It never calls Platform `/v1/decisions`; the local `/v1/decisions` is this project's compatibility endpoint. Same model identifier, different inference path: identical probabilities or latency do not follow.

## Prices and published results

| Source | Excerpt / fact | Scope |
| --- | --- | --- |
| [OpenAI Decisions pricing](https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability) | “$0.10 per 1M tokens”; input only | No cache-read/write or output charge; regional and long-context premiums apply |
| [OpenAI API pricing](https://developers.openai.com/api/docs/pricing) | Standard short-context `gpt-6-luna`: input $0.10, output $0.50 per 1M | Cached input $0.01 and cache writes $0.125 per 1M, excluded from the no-cache examples |
| [Codex / ChatGPT pricing](https://learn.chatgpt.com/docs/pricing) | “API token prices are separate from subscription usage” | Plus $20/month, Pro from $100/month; shared limits and optional credits apply |
| [Codex token rates](https://learn.chatgpt.com/docs/pricing#token-rates) | Luna Standard paid credits 2.5 / 0.25 / 12.5 per 1M uncached input / cached input / output | Credit prices alone do not determine included use |
| [Fast mode](https://learn.chatgpt.com/docs/agent-configuration/speed#fast-mode) | Separate included-use and purchased-credit multipliers | See [QUOTA.md](QUOTA.md) |
| [TypeSafe Jev introduction](https://typesafe.ai/blog/introducing-system-one-models-and-jev), [homepage](https://typesafe.ai/) | Input $0.042/MTok; “Output tokens: FREE” | TypeSafe cloud Jev; the homepage shows $42 per billion input tokens |
| [Pinned Jev application README](https://github.com/BorisLeMeec/jev/blob/e81c1d006b8b23a616486610f311039088521d0c/README.md#results) | “These tools save tokens, not time.” | Commit `e81c1d0`; the author's experiments, not rerun with Luna |
| [Risk benchmark](https://github.com/themsquared/jev-benchmark/tree/42a9123a4ef42488aa775effdecb2f34a0d66a5a) | 55/60 correct, `jev-latest` | Author's historical cloud results; the alias is not a fixed checkpoint |
| [Decision benchmark](https://github.com/supreethbare/decision-model-bench/tree/f69fd43641ef756715e2238b36f0b40be36a3958) | Toxicity 157/200; tool selection 199/200 | Jev 1.13.0, September 2026; function choice only, not execution |
| [Decision Bench v4](https://github.com/atlanai/decision-bench/tree/8d1563c8740600367172f4bffd2858f400bdd32d/results/bench-v4) | `gpt-6-luna-decisions` 969/1071, p50 326 ms; `jev-1.13` 990/1071, p50 439 ms | Commit `8d1563c`; Luna through the Decisions API via OpenRouter, not this project's route |

The [comparison](COMPARISON.md) and [comparison-data.json](comparison-data.json) use these figures. Community quota reports are kept in [docs/internal/QUOTA_SOURCES.md](internal/QUOTA_SOURCES.md) and are not used as rates.

## Notes

These pages describe OpenAI's products; they do not document or endorse this independent service, which is not an official subscription Decisions API, is not affiliated with OpenAI and has no guaranteed access for every account. They say nothing about the dedicated Decisions implementation's training, probability head or hardware, and this project makes no claim about them. The Decisions guide's roughly tenfold speed claim against Responses is OpenAI's statement, not a measurement of this project, and is not plotted. Prices and availability can change.
