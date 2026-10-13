# Estimate your subscription use

Measure a batch of your normal traffic, then scale it. OpenAI publishes no requests-per-percent rate for GPT-6 Luna, so your own reading is the estimate.

At the token mix measured in the archived Luna run (about 618 input + 26 output tokens per request), 1,000 requests come to about **1.9 credits at GPT-6 Luna's credit rate** ([computation](COMPARISON.md#cost-per-1000-requests)). That is not a published conversion to included plan use, and your plan meter shows percent, so measure as below.

## Measure

1. Note the **used** percentage for your current window in the [usage dashboard](https://chatgpt.com/codex/settings/usage) or with `/status` in the Codex CLI (if it shows remaining percent, subtract).
2. Run `N` comparable application requests, with no other Codex or ChatGPT activity on the account and no window reset in between.
3. Note the used percentage again. `Δ = after − before`, in percentage points (pp).

The use of `k` more similar requests is between

`k × (Δ − 1) / N` and `k × (Δ + 1) / N` pp, valid when `Δ > 1`.

The ±1 pp allows for whole-percent display rounding (assumed; the actual rule is unpublished). If `Δ ≤ 1`, measure a larger batch.

**Example (synthetic):** `N = 100` requests moved the meter `Δ = 3` pp, so 1,000 more similar requests use 20–40 pp (point estimate 30).

```python
def quota_interval(n, delta_pp, future_requests):
    if n <= 0 or future_requests < 0:
        raise ValueError("Use N > 0 and future requests >= 0")
    if delta_pp <= 1:
        raise ValueError("Not identified at whole-percent resolution; measure a larger batch")
    return (future_requests * (delta_pp - 1) / n, future_requests * (delta_pp + 1) / n)

print(quota_interval(100, 3, 1000))  # (20.0, 40.0)
```

## Record with each sample

| Record | Why |
| --- | --- |
| Plan, date, window and reset time | Five-hour and weekly windows are estimated separately; plan with the tighter one |
| Model, reasoning and speed tier, Codex and application version | Any change needs a new measurement; this service's default readout uses `none` / `medium` reasoning |
| Input / output / cached tokens, context and image size | Explain variation between batches |
| Every generation, retry, failure and unknown-usage attempt | One application request can cause several generations |

Repeat across several batches to see variation. Record numbers only; never share credentials or raw private input.

## Published OpenAI figures

| Figure | Value |
| --- | --- |
| [Paid-credit rates](https://learn.chatgpt.com/docs/pricing#token-rates), GPT-6 Luna Standard | 2.5 credits / 1M uncached input, 0.25 / 1M cached input, 12.5 / 1M output |
| [Usage limits](https://learn.chatgpt.com/docs/pricing#usage-limits), Plus and Standard Business | About 350–3,000 local Luna messages per five hours (task-dependent messages, not HTTP requests) |
| Pro | No five-hour limit currently; shared or weekly limits may apply |
| [Fast mode](https://learn.chatgpt.com/docs/agent-configuration/speed#fast-mode) | 2.5× Standard against included use; 2× in paid credits |

This service requests Fast mode only when started with `LDM_SERVICE_TIER=fast`, so measure Fast and default batches separately.

## Notes

OpenAI states that credit prices do not determine included subscription use, so the credit rates do not convert into quota percent. Purchased credits after a limit are a separate billing basis; nothing here switches to credits or a paid API. The interval is a local planning estimate that covers display rounding only, not meter lag or background activity. The project has no quota measurements of its own; unverified community reports are listed in [docs/internal/QUOTA_SOURCES.md](internal/QUOTA_SOURCES.md) and are not used as a Luna rate.
