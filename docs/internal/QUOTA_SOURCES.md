# Subscription-use research notes

Maintainer material behind [docs/QUOTA.md](../QUOTA.md), checked 2026-10-13 UTC. Machine-readable assumptions are in [comparison-data.json](../comparison-data.json) under `subscription_quota`.

## Paid-credit illustration

After-limit purchased credits are **a separate billing basis**. Illustrative credit-billed Standard traffic of 1,000 requests, each with 1,000 uncached input and 100 output tokens, costs **3.75 credits** under the published rates:

`(2.5 × uncached_input + 0.25 × cached_input + 12.5 × output) / 1_000_000`

This is a **conditional paid-credit reference, not included allowance %, entitlement or a measured bill for this project**. It does not activate credits or another backend. Use full upstream token counts, not just user text; if input totals include cached tokens, subtract cached input to obtain uncached input. [Output token counts include reasoning tokens](https://developers.openai.com/api/docs/guides/token-counting): do not add reasoning twice. No API dollar rate is used. For the same hypothetical token counts, paid-credit Fast would be 7.5 credits; included Fast's 2.5× is a different rule.

## Community evidence: methods, not Luna conversion factors

These are **unverified user reports**, not OpenAI policy or this project's measurements. Only a few method-bearing sources were retained:

| Original report | Sample / conditions | Useful conclusion and limitations |
| --- | --- | --- |
| [Codex #41541](https://github.com/openai/codex/issues/41541), opened 2026-08-29, updated 2026-09-03 | 5 sessions Aug 9–14 vs 5 Aug 28–29; ProLite, VS Code, high reasoning, no Ultra/manual subagents; clients 0.147/0.148 vs 0.150. Author excluded resets, idle and overlapping main sessions. Totals 118.73M / 12 displayed pp vs 142.44M / 17 pp; cached input 98.076% / 97.731%. | Publishes token/session tables and warns the meter is account-global and whole-percent. Model not pinned as this project's Luna workload; cached coding-agent context and aggregate pp cannot become our coefficient. We did not execute its logs or validate full-account isolation. |
| [Codex #41220, Sept 16 update](https://github.com/openai/codex/issues/41220), thread opened 2026-08-27 | Windows Pro report: Aug 31–Sept 2 vs Sept 12–16, 3,821,936,804 tokens / 97 pp vs 479,026,711 / 80 pp; each period's endpoint readings share a reset ID. Cached input 97.5% / 97.3%. | Model mix changed Sol 94.5% → Astra 98.8%; local records may omit other clients/background use. This is evidence for recording reset/model/cache conditions, **not** a verified entitlement reduction or Luna fee factor. |
| [Luna allowance post](https://www.reddit.com/r/codex/comments/1wp0v6x/allowance_for_gpt_6_luna_sol_and_astra_measured/), checked 2026-10-13; exact publication date unavailable from fetched page | Author says Plus, fresh five-hour windows, Pi and no long context; reports API-equivalent dollars and assumes uninterrupted windows. Complete raw token/cache/reasoning samples are not provided in the fetched post. | Relevant Luna claim, but low confidence for quota calibration. API-cost normalization and speculative Pro multipliers do **not** establish included token capacity; excluded from our estimates. |


The project has historical observed token summaries but **no paired quota readings or included-quota calibration**. This research did not inspect any local account, credential or usage meter and made zero model calls. Community token/percentage observations must not be advertised as a measured Luna quota discount.
