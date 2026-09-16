<!-- role: research | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# Retries and rate limits

## Two different retry layers — do not conflate them

The codebase has two, and they behave oppositely on cost.

| | What failed | Model ran? | Tokens billed | Where |
|---|---|---|---|---|
| **SDK retry** | the HTTP request (429) | no | **none** | `_client(max_retries=3)`, `llm.py:30-35` |
| **Loop retry** | the answer's format | yes | **full, every time** | `LLM_MAX_RETRIES`, `llm.py:190` |

A 429 is rejected *before generation*. Nothing is produced, nothing is billed; the
SDK waits out a backoff and re-sends the identical request. It costs latency only.

A loop retry is the `fxe5` case: the request succeeded, the model answered, the
code rejected the notation and asked again. Full token cost per attempt.

## SDK retry distribution (59 attempts, baseline run)

| SDK retries | Attempts | Share |
|---|---|---|
| 0 | 18 | 30.5% |
| 1 | 33 | 55.9% |
| 2 | 7 | 11.9% |
| 3 | 1 | 1.7% |

**70% of attempts were throttled at least once.** 59 attempts became 109 HTTP
requests, so **45% of all requests are re-sends**.

Note the two figures are different denominators and were conflated once in an
earlier draft: 70% is *of attempts*, 45% is *of requests*.

## Cause: confirmed by replay, not inferred

`retries_taken` is per-request (the loop variable at
`openai/_base_client.py:963`) but records only *that* a retry happened, never why.
A replay with `httpx` debug logging captured the actual statuses:

```
CALL  9: sdk_retries=0
  >> NON-200: HTTP/1.1 429 Too Many Requests   (x3)
CALL 10: sdk_retries=1
  >> NON-200: HTTP/1.1 429 Too Many Requests   (x2)
CALL 11: sdk_retries=2
...
SUMMARY: 15 SDK retries across 20 calls
```

**Every retry is a 429.** The pattern is sharp: the first ~9 calls of a game go
through clean, then the per-minute budget runs out and almost every call after
that is rejected once or twice. A spaced-out 8-call test showed zero retries —
it stopped one call short of the cliff.

## The rate limits, from the response headers

```
x-ratelimit-limit-requests:     1000
x-ratelimit-limit-tokens:       8000
x-ratelimit-remaining-requests: 823
x-ratelimit-remaining-tokens:   7656
x-ratelimit-reset-requests:     4h14m52.8s
x-ratelimit-reset-tokens:       2.579s
```

**The token budget is 8,000, and the header does not say "output".** This fits the
observed cliff well: 8,000 ÷ ~900 tokens per move ≈ **8.9 moves**, and 429s began
at call 9-10 in both independent runs.

### Correction recorded

An earlier stage of this research used **1,000 output tokens per minute** as the
budget. That figure came from a 429 body returned by **`qwen/qwen3.8-27b`** —

```
output tokens per minute (OTPM): Limit 1000, Requested 2048
```

— a different model with a different budget, and it was carried over to `gpt-oss`
without checking. Throughput figures derived from it were too optimistic:

| | Tokens/move | Moves/min (using 1000 OTPM) | Moves/min (using 8000 total) |
|---|---|---|---|
| Full prompt | ~903 | 5.2 | ~8.9 |
| Lean prompt | ~686 | 10.4 | ~11.7 |

So trimming output buys roughly **31% more headroom, not 70%**. If the bucket
counts input as well, cutting 68 output tokens out of ~900 total is a far smaller
lever than cutting output from an output-only budget would be.

**Unverified:** whether `x-ratelimit-limit-tokens` counts input, output, or both.
Settling it means watching `remaining-tokens` decrement against calls of known
size. Until then treat the revised throughput figures as provisional. Cost
findings are unaffected — those come from `usage`, not from the limit.

## Gap in the log tables

`llm_call_attempts.status_code` is populated only when an attempt fails outright.
When the SDK retries and then succeeds, the 429s vanish: the row reads
`sdk_retries=2, status_code=NULL`. The volume of throttling is visible; the cause
is not. Recovering it needs either the retry reason or the rate-limit headers
persisted alongside the attempt.

Related: a **404 `model_not_found`** is caught by the same `APIStatusError` handler
as a genuine network fault, logged as `outcome='transport'` and surfaced as
`llm_unavailable`. A permanent configuration error is recorded as a transient one,
which will distort exactly this kind of analysis.

## How token counts are known at all

Not computed locally — the provider reports them in the response body:

```json
{
  "prompt_tokens": 106,
  "completion_tokens": 24,
  "total_tokens": 130,
  "completion_tokens_details": { "reasoning_tokens": 7 }
}
```

`llm.py:214` reads this into `CallLog.add_attempt`, which is why every number in
this research was available without new instrumentation.

**Gotcha for later:** under `stream=True` the `usage` block is `None` unless
`stream_options={"include_usage": True}` is passed. Switching to streaming would
silently destroy this telemetry.
