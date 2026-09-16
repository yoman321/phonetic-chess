<!-- role: research | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# Model: decision and the two changes it needs

## DECISION (2026-09-16, user): stay on qwen

`LLM_MODEL=qwen/qwen3.8-27b`. Taken knowing it gets no prompt caching and, on a
paid tier, would cost substantially more than `openai/gpt-oss-20b`.

**Rationale as stated:** the project is on Groq's free tier, where the binding
constraint is the per-minute token bucket, not price. On that basis qwen's support
for `reasoning_effort=none` — which removes ~92 tokens a move — is the deciding
factor.

**Revisit if the project ever moves to a paid tier**, where the comparison
reverses. The one number needed for that revisit is in `07-pricing.md`.

Note this means the `qwen3.6` → `qwen3.8` move, not staying on the configured
model: `qwen/qwen3.6-27b` is dead (404) and cannot be kept.

### Verified after the decision

10 consecutive moves on `qwen/qwen3.8-27b`, `reasoning_effort=none`,
`max_tokens=400`:

```
move  1 in=280 out=64 details=None retries=0 | tok_left=7320/8000
move  5 in=288 out=68 details=None retries=0 | tok_left=6134/8000
move 10 in=294 out=32 details=None retries=0 | tok_left=4872/8000
TOTALS in=2660 out=625 sdk_retries=0
```

Four things this settles:

1. **Zero reasoning tokens, 10 out of 10.** `completion_tokens_details=None`
   throughout. The hidden-reasoning cost is gone, which was the point.
2. **`max_tokens=400` held.** No truncation, no `bad_json`, across 10 moves.
3. **The rate limit is 8,000 tokens — the same as gpt-oss, not the 1,000 feared.**
   The earlier 1,000 figure came from a 429 raised *before* `max_tokens` was set;
   with it set, the ordinary bucket applies.
4. **The bucket counts input + output, and refills continuously.**
   `x-ratelimit-remaining-tokens` fell roughly in step with total tokens used, and
   `reset` grew from 5.1s to 56.1s as it drained — a refilling token bucket, not a
   fixed per-minute window. This answers the open question left in
   `04-retries-rate-limits.md`.
5. **Zero SDK retries across 10 moves**, where `gpt-oss` began 429ing at call 9.

### Caveat on that run

It used a **shortened system prompt**, not the real 237-token one from `llm.py`, so
`in=280` is not comparable to the live game's `in=633`. Do not read a
lower input cost into this.

The honest comparison, holding the prompt constant, is output only:

| | Output tok/move | Est. total/move on the real prompt |
|---|---|---|
| `gpt-oss-20b` @ `low` | ~194 | ~827 |
| `qwen3.8-27b` @ `none` | ~64 | ~697 |

Roughly **16% fewer tokens per move**, all of it from removing reasoning — about
11.5 moves per bucket instead of 9.7. Worth having, smaller than the raw
output-token drop suggests.

## Background: the configured model is dead

**This is the most urgent finding in this directory.** Everything else here is an
optimisation. This one is an outage.

## The game currently cannot make a move

`llm.py:13` defaults to `LLM_MODEL=qwen/qwen3.6-27b`. `backend/.env` does not
override it. That model **returns 404 on this Groq account**:

```
openai.NotFoundError: Error code: 404 - The model `qwen/qwen3.6-27b`
does not exist or you do not have access to it.
```

Every `say_move` therefore fails with `llm_unavailable`. This is not a token
problem; it is a total failure. It was working as of the 2026-09-13 handoff, so
the model was withdrawn between then and 2026-09-16.

## The successor needs `max_tokens`, which the code never sets

`qwen/qwen3.8-27b` is available on the account, but fails on its own:

```
openai.RateLimitError: Error code: 429 - Request too large for model
`qwen/qwen3.8-27b` ... output tokens per minute (OTPM): Limit 1000,
Requested 2048. The request's expected output tokens exceed the enforced
limit; reduce max_tokens (or the request's expected output).
```

`pick_move_with_llm` passes no `max_tokens` (`llm.py:196-215`), so the SDK sends
its default of **2048**. Groq evaluates that figure *before generating anything*
and rejects the call pre-emptively. Actual replies are ~90 tokens; the request is
refused on a declared ceiling nobody intended to declare.

## Why qwen, in token terms

Qwen is the only model on this account accepting `reasoning_effort=none`, which
removes hidden reasoning entirely. Holding the prompt constant:

| | Input | Output | Total/move |
|---|---|---|---|
| `gpt-oss-20b` @ `low` (measured) | ~633 | ~194 | **~827** |
| `qwen3.8-27b` @ `none` (projected) | ~633 | ~64 | **~697** |

**~16% fewer tokens per move**, all of it from removing reasoning — roughly 11.5
moves per 8,000-token bucket instead of 9.7.

The qwen projection assumes input size carries across models. Unverified: the
10-move qwen run used a shortened prompt (see the caveat below).

### What this decision gives up

`gpt-oss` supports Groq's automatic prompt caching and qwen does not
(`07-pricing.md`). Whether that matters depends on an unanswered question —
do cached tokens still count against the rate limit? If they do not, gpt-oss has a
throughput advantage this decision forfeits. **Not yet measured**
(`10-kv-caching.md`).

There is also a cost multiple, recorded in `07-pricing.md` purely as the condition
for revisiting this decision should the project ever leave the free tier.

## The qwen configuration

Both changes are required. Neither works alone.

| Change | Why |
|---|---|
| `LLM_MODEL=qwen/qwen3.8-27b` | 3.6 is gone. Also the only family on this account accepting `reasoning_effort=none`. |
| pass `max_tokens` (400 tested) | without it the SDK's 2048 default triggers a pre-emptive 429 |

`LLM_REASONING_EFFORT=none` is already the code default (`llm.py:21`) and needs no
change — it simply never had a model that honoured it.

### Measured, live

```
FAIL  max_tokens=None, effort=none : 429 Request too large
OK    max_tokens=400,  effort=none : in=285 out=92 details=None  437ms
```

`completion_tokens_details=None` is the point: **zero reasoning tokens**. All 92
output tokens are the answer itself.

## What the qwen route recovers

| | gpt-oss-20b @ `low` | qwen3.8-27b @ `none` |
|---|---|---|
| Reasoning tokens/move | 92 | **0** |
| Output tokens/move | ~194 | ~92 |
| Latency, single call | 2,000-7,000ms typical | **437ms** |

Removes the hidden-reasoning line (`03-output-anatomy.md`) outright and roughly
halves output tokens. Since the free-tier bucket counts every token equally, that
reduction lands directly on throughput.

This restores the design the code already intended: `llm.py:16-20` documents
choosing a non-thinking mode because "move picking is latency-sensitive and
reasoning tokens bill as output." That reasoning was always right; it just lost
its model.

## Risk of setting `max_tokens` too low

A truncated reply is **invalid JSON**, so `json.loads` raises, the attempt is
logged `bad_json`, and all three loop retries burn — trading a cheap failure for
an expensive one (`01-baseline.md`).

- Measured visible output: 84-102 tokens across samples
- 400 gives roughly 4× headroom and cleared a real prompt comfortably
- **Tested at one position only, not across a full game.** Confirm against a run
  of real positions, especially late-game where `tone_summary` and `rationale`
  run longest, before settling on a value.

A cap that truncates is worse than no cap at all, so this value deserves a gate of
its own rather than being chosen by eye.

## Not done here

Both changes are writes this research session must not make:

- `max_tokens` is a code change to `llm.py` — a Build-role write (AGENTS.md §1).
- Choosing the model was a product decision (AGENTS.md §2), taken by the user on
  2026-09-16 and recorded at the top of this file. `openai/gpt-oss-20b` supports
  prompt caching but rejects `reasoning_effort=none`, so it carries ~92 reasoning
  tokens per move permanently.

Note `max_tokens` is worth setting on **any** model, not only qwen: it bounds the
worst case, and the code currently declares 2048 by omission.

## Related defect

A 404 `model_not_found` is caught by the same `APIStatusError` handler as a genuine
network fault (`llm.py:240-252`), logged as `outcome='transport'`, and surfaced as
`llm_unavailable`. A permanent configuration error is recorded as a transient one —
which is precisely why a dead model reads in the logs as flaky connectivity. See
`04-retries-rate-limits.md`.
