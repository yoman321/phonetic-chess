<!-- role: research | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# Baseline: what one move costs

Source: four full games driven end to end through `sessions_ops.say_move` against
live Groq, writing to the existing `llm_calls` / `llm_call_attempts` tables. Every
figure here is a query against those rows — no estimation.

Model `openai/gpt-oss-20b`, `reasoning_effort=low`. 53 moves, 47,850 tokens.

## By outcome

| Outcome | Moves | Loop attempts | HTTP requests | Req/move | Tok/move | Avg latency |
|---|---|---|---|---|---|---|
| Move delivered (`ok`) | 50 | 50 | 89 | 1.78 | 817 | 4.1s |
| Gave up after 3 tries (`exhausted`) | 3 | 9 | 20 | 6.67 | 2,326 | 19.0s |
| **All** | **53** | **59** | **109** | **2.06** | **903** | **4.9s** |

Latency across all calls: p50 4,454ms, p95 14,662ms.

## Token totals

| | Prompt | Completion | of which reasoning |
|---|---|---|---|
| `ok` | 31,674 | 9,197 | 4,607 |
| `exhausted` | 5,871 | 1,108 | 276 |
| **Total** | **37,545** | **10,305** | **4,883** |

Input is 78.5% of tokens. Reasoning is 47.4% of all output.

**`completion_tokens` includes `reasoning_tokens`** — it is a subset, not an
addend. Verified empirically: a reply of `{"ok":1}` reported `completion_tokens=21`
with `reasoning_tokens=7`; if they were additive, an 8-character reply would have
cost 21 visible tokens. So visible output = completion − reasoning = 102 tok/move.

## Per attempt

| Attempt outcome | n | Avg prompt | Avg completion | Avg reasoning | Avg ms |
|---|---|---|---|---|---|
| `ok` | 50 | 633 | 184 | 92 | 4,068 |
| `invalid_uci` | 9 | 652 | 123 | 31 | 5,844 |

## Per game

| Session | Moves | HTTP requests | Tokens | Tok/move | Failed moves |
|---|---|---|---|---|---|
| `costrun-c482f917` | 20 | 42 | 16,096 | 805 | 0 |
| `costrun-cdfc2bf2` | 11 | 24 | 11,094 | 1,009 | 1 |
| `costrun-ec43bdf6` | 11 | 26 | 10,184 | 926 | 1 |
| `costrun-cf9e6d46` | 11 | 17 | 10,476 | 952 | 1 |

## The failures

All three `exhausted` moves were the same defect: the model replied with standard
algebraic notation (`fxe5`) where the code requires UCI (`f4e5`). `pick_move_with_llm`
rejects it, retries, and — because each retry is a stateless call with no knowledge
of the rejection — receives the identical answer three times, then raises.

Cost of that: 3 moves, 9 attempts, 20 HTTP requests, 6,979 tokens, **zero moves
delivered**. 5.7% of moves, 14.6% of tokens, 18.3% of requests.

A `board.parse_san()` fallback converts these. Confirmed independently in a later
20-move run where it rescued 1 move (5%) that would otherwise have failed.

## Scaling

Projected from 903 tokens and 2.06 requests per move, at 40 moves per game:

| Volume | Moves | Requests | Total tokens |
|---|---|---|---|
| One move | 1 | 2.1 | 903 |
| One game | 40 | 82 | 36,112 |
| 1,000 games | 40,000 | 82,340 | 36.1M |

Against the free tier's 8,000-token bucket, 903 tokens/move is roughly **9 moves
before throttling** — which matches the observed 429 cliff at call 9-10
(`04-retries-rate-limits.md`).
