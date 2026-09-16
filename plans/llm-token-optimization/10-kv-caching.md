<!-- role: research | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# KV / prefix caching — open question, not yet answered

**Status: to think about. Nothing here is measured.** This file exists to record
the options and the one test that would settle them.

## Why it matters now

405 tokens of every prompt are byte-identical on every move
(`02-prompt-anatomy.md`). With reasoning switched off on qwen, output falls to
~64 tokens a move, so the token split becomes:

| | Tokens/move | Share |
|---|---|---|
| Fixed prefix | ~405 | **58%** |
| Rest of input | ~228 | 33% |
| Output | ~64 | 9% |

**Roughly 9 of every 10 tokens burned are input**, and the single largest block is
text we already sent last move. On the free tier the constraint is a per-minute
token bucket (`09-model-and-max-tokens.md`), so this is the biggest remaining
lever by a wide margin — bigger than turning reasoning off was.

## What prefix caching is

The model computes intermediate state (the KV cache) for every token it reads. If
a prefix is identical across requests, that computation can be reused instead of
redone — "pre-running" the fixed part once and starting each move from there.

## Three routes

### 1. Provider-side (Groq) — closed by the qwen decision

Automatic, no code change, 50% discount on cached input, minimum prefix 128-1024
tokens.
**Supported only on GPT-OSS 20B / 120B / Safeguard 20B** (`07-pricing.md`). Not
available on `qwen/qwen3.8-27b`, which the 2026-09-16 decision selected.

So on the chosen model this route does not exist today. It is the cheapest route
if the decision is ever revisited.

### 2. Self-hosting — the "own the GPU" route

vLLM and SGLang both implement automatic prefix caching, and self-hosting would
give full control: pre-warm the prefix, pin it, never evict it. It also removes
the rate limit entirely, since the bucket is Groq's, not a law of physics.

Much larger operational commitment than an API key. Not costed here.

### 3. Fine-tuning — the only route that deletes the tokens

Train the tone-matching behaviour into the weights so the instructions need not be
sent at all. Every other route makes the 405 tokens *cheaper or faster*; this is
the only one that makes them *not exist*.

Most work of the three. **Unchecked:** whether Groq hosts custom fine-tunes.

## The critical unknown

**Caching reduces computation, not token count.** A cached request is still
counted as 633 tokens; the provider just does less work. The 50% saving is applied
to *billing* — and it is not established that it does anything for a *rate limit*.

Since the rate limit is the only thing that binds here, that distinction decides
whether caching is a major lever or no lever at all.

| If cached tokens... | Then |
|---|---|
| do **not** count against the bucket | caching is a large throughput win, and a real argument to revisit the qwen decision |
| **do** count against the bucket | caching was never going to help here; shortening the prompt is the only move |

## The test that settles it

Roughly one minute of API calls, on `gpt-oss-20b` (the only model where Groq
caches):

1. Send the same 400+ token prefix repeatedly, varying only the tail.
2. After each call read `x-ratelimit-remaining-tokens` from the response headers.
3. Compare the drop against `usage.prompt_tokens`.

If the bucket falls by the full prompt size, cached tokens count and caching is
irrelevant to throughput. If it falls by roughly half, they do not, and the whole
picture changes.

**Not run.** It is the cheapest unanswered question in this directory.

## Meanwhile, the thing that works regardless

Shortening the prefix helps under every outcome above, and needs no caching, no
model change and no new infrastructure. The system prompt is 237 of the 405 fixed
tokens and is the only block genuinely open to editing — the chat scaffolding
(117) is the format itself and the instruction block (51) is already small.

Halving the system prompt would save ~120 tokens a move, about 17% of total token
consumption — more than switching off reasoning bought (~16%). Untested: whether a
shorter prompt degrades move quality or raises the notation-failure rate.

**Caution:** if provider-side caching is ever back in play, cutting the prefix
below the 128-1024 minimum would *lose* the discount. Shorten and cache pull in
opposite directions; establish which one applies before doing either.
