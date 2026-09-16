<!-- role: research | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# Reliability: what to trust, what not to

Read this before citing any number in this directory.

## The governing rule

**Input tokens are deterministic; output tokens are sampled.**

The same prompt text always tokenizes to the same count, so a single observation of
an input-side figure is conclusive. Output is generated at `temperature=0.7`, so
two calls with byte-identical inputs produce different amounts of text and
different amounts of reasoning — a single observation of an output-side figure is
close to worthless.

Every reliability judgement below follows from that one distinction.

## Solid

| Finding | Why |
|---|---|
| Prompt anatomy shares (`02`) | ablation against the API's own `prompt_tokens`; deterministic |
| Input delta for `intent`+`rationale` = **62 tokens** | measured 5/5 identical |
| `completion_tokens` includes `reasoning_tokens` | verified against a known-short reply |
| Baseline per-move tokens and requests (`01`) | 53 logged calls |
| Output field shares: 48.0% for `intent`+`rationale` (`03`) | 20 calls |
| Retries are 429s (`04`) | replay with HTTP status logging; reproduced twice |
| `retries_taken` semantics | read from `openai/_base_client.py:963` |
| Lookback is flat (`06`) | input-side; spread of 30 tokens over 19 plies |
| Caching availability, gpt-oss only (`07`, `10`) | fetched from Groq docs this session |

## Directionally sound, imprecise

| Finding | Caveat |
|---|---|
| Failure rate 5.7%, 14.6% of tokens (`01`) | only 3 events; reproduced at 5% in a separate 20-move run |
| Full-vs-lean total token delta ~16%/move (`05`) | n=1 per checkpoint, noisy output |
| Break-even click rate ~37% (`05`) | pooled figure is usable; the per-checkpoint trend (25.9 → 48.1) is suggestive only |
| Per-field token estimate from char share (`03`) | assumes tokens scale with characters inside one JSON string — good to a few percent, but an approximation, not a count |

## NOT established

### The reasoning share of `intent` + `rationale`

Reported at one stage as a firm per-move figure. **Do not cite it.** The
underlying measurement failed:

| Checkpoint | FULL reasoning | LEAN reasoning | Difference |
|---|---|---|---|
| Start | 11 | 23 | **−12** |
| Middle | 98 | 82 | +16 |
| End | 133 | 42 | +91 |

One of three samples came out **backwards** — the lean prompt did more thinking
than the full one — and the differences span −12 to +91, wider than the effect
being measured. Two routes to the same number disagree — derived as a residual it
comes out ~25% lower than computing it directly from the reasoning deltas. That
disagreement is itself the tell.

`reasoning_tokens` is one opaque total per call; nothing attributes thinking to a
field, so A/B subtraction is the only route and it needs enough samples to beat the
variance.

**To fix:** 20+ samples per arm at a fixed checkpoint, or `temperature=0` to remove
the variance at source. ~40 calls.

### Whether the rate-limit bucket counts input

`x-ratelimit-limit-tokens: 8000` does not say input, output or both. All revised
throughput figures in `04` depend on the answer. Settle by watching
`remaining-tokens` decrement against calls of known size.

### The `?` click rate

Not logged anywhere. Decides the lazy-generation question outright
(`05-ab-lazy-explain.md`) and cannot be derived from any existing data.

### Whether prefix caching is hitting

The fixed prefix (405 tokens) falls inside Groq's 128-1024 minimum range, so it may
or may not be cached. Untested. See `07-pricing.md`.

## Corrections made during this research

Recorded so they are not silently re-introduced.

1. **"70% of requests are retries"** — wrong. 70% is of *attempts*; 45% is of
   *requests*.
2. **Break-even estimated at 22-42%** — wrong. Measured ~60%. The estimate assumed
   the explain prompt would cost as much as a move prompt; it costs less than half.
3. **"1,000 output tokens per minute"** — wrong model. That came from a
   `qwen/qwen3.8-27b` 429 body and was carried to `gpt-oss` unchecked. Headers say
   8,000 tokens. Throughput gain from going lean is ~31%, not ~70%.
4. **"Input dominates by four to one"** — true in tokens, false in dollars. Output
   is 4× the rate, making the cost split roughly even.
5. **62-token input delta labelled "reconstructable"** — it was directly measured.
   Reconstruction from stored data is possible but was not the method used.
6. **"Switch to qwen to remove hidden reasoning"** — reversed, then reinstated.
   Qwen is ~8.4× the per-move *cost* of gpt-oss, which looked decisive until the
   project's free-tier status made cost irrelevant. Decision: stay on qwen
   (`09-model-and-max-tokens.md`). Root cause of the churn: reasoning about price
   before establishing which constraint actually binds.
7. **Every figure originally reported in dollars.** Wrong frame for a free-tier
   project, where the bucket counts input and output equally. Rewritten in tokens
   throughout. This moved a real finding: the lazy-explanation break-even fell
   from ~60% (dollar-weighted, output priced 4×) to **~37%** (token-weighted).
   Dollar figures survive only in `07-pricing.md`, as the condition for revisiting
   the model decision.

## Method notes

- No repo file was modified by any of this research. Model and reasoning-effort
  substitutions were made through environment variables only.
- `tiktoken` was deliberately not installed (AGENTS.md §8). Every token figure comes
  from the API's own `usage` block or from ablation against it.
- Library behaviour was read from installed source before being relied on
  (`openai/_base_client.py`, `openai/_legacy_response.py`), per AGENTS.md §8.
- The throwaway Postgres (`backend/scripts/testdb.sh`) was used for all logged runs
  and torn down afterwards.
