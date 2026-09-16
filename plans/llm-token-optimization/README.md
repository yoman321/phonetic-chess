<!-- role: research (not one of AGENTS.md §1's roles) | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# LLM token optimization — research material

**This is not a plan.** No `Status:` line, no phases, nothing downstream reads it.
It is the measured data behind the question "what is a move costing us, and why",
gathered in one session on 2026-09-16. A plan, if one is ever written, should cite
these numbers rather than re-derive them.

## Files

```
README.md                   this index, headline findings, how to reproduce
01-baseline.md              per-move tokens, calls and latency, from the call log
02-prompt-anatomy.md        what fills the input, measured by ablation
03-output-anatomy.md        what fills the output, per JSON field
04-retries-rate-limits.md   429s, SDK retries, the rate-limit headers
05-ab-lazy-explain.md       A/B: inline vs on-demand intent+rationale
06-lookback.md              cost of explaining an OLD move
07-pricing.md               paid-tier reference only — not applicable on free tier
08-reliability.md           what is solid, what is not, open questions
09-model-and-max-tokens.md  READ FIRST — model decision + the dead-model outage
10-kv-caching.md            open question: prefix caching, and shortening the prefix
scripts/                    the scripts that produced the numbers
```

## Decision on record

**2026-09-16 — staying on qwen (`qwen/qwen3.8-27b`), on free-tier reasoning.**
Dollar cost does not bind on the free tier; the per-minute token bucket does.
Verified working at `reasoning_effort=none` with `max_tokens=400`: zero reasoning
tokens, zero retries over 10 moves. Revisit if the project moves to a paid tier,
where qwen is ~8.4× the cost of `gpt-oss-20b`. Full record in
`09-model-and-max-tokens.md`.

**Everything in this directory is measured in tokens**, not currency. On the free
tier nothing is billed; the binding constraint is Groq's per-minute token bucket,
which counts input and output equally. Dollar figures survive only in
`07-pricing.md`, solely as the condition for revisiting the model decision if the
project ever leaves the free tier.

## Before anything else

**The game cannot currently make a move.** The configured model returns 404, so
every `say_move` fails. Fixing it needs two changes together — a live model and a
`max_tokens` the code has never set. Both verified; see
`09-model-and-max-tokens.md`. That is an outage, not an optimisation, and it
outranks every lever below.

## Headline findings

| Finding | Number | Confidence |
|---|---|---|
| Tokens per move | 903 (708 in, 194 out) | high — 53 logged calls |
| HTTP requests per move | 2.06 (1 logical call) | high |
| Moves per 8,000-token bucket | ~9 before throttling | high — 429 cliff seen twice |
| Input that is byte-identical every move | 76.4% of the prompt | high — deterministic |
| The player's message, as a share of input | 0.8% | high |
| The move itself, as a share of output JSON | 1.1% | high — 6 samples |
| Hidden reasoning, as a share of output | 47% | high — 53 calls |
| Moves that failed and delivered nothing | 5.7%, burning 14.6% of tokens | medium — 3 events |
| Break-even click rate for on-demand explanations | ~37% | low — n=1 per checkpoint |
| Cost of explaining a move from 19 plies later | same as explaining it now | high — input is deterministic |

## The three levers, with measured returns

These assume the outage above is fixed first.

1. **Accept standard chess notation (SAN).** Every observed failure was the model
   answering `fxe5` where the code demands `f4e5` — legal chess, wrong notation.
   Recovers 14.6% of tokens *and* converts three player-facing errors into three
   moves. Independently confirmed: a `board.parse_san()` fallback rescued 1 of 20
   moves in a later run. See `01-baseline.md`, `05-ab-lazy-explain.md`.
2. **Generate `intent` + `rationale` on demand.** They are ~48% of the output JSON
   and are only displayed behind the chat panel's `?` button. Uses fewer tokens
   below a ~37% click rate. Blocked on a number nobody has: the actual click rate.
   See `05-ab-lazy-explain.md`.
3. **KV / prefix caching, and shortening the prefix — THINK ABOUT THIS.**
   405 tokens of every prompt are identical to the last one. With reasoning off,
   input is ~91% of all tokens burned and this one block is ~58%, making it the
   largest remaining lever by a wide margin. Three routes (provider-side,
   self-hosting, fine-tuning), one unanswered question that decides between them:
   do cached tokens still count against the rate limit? **Not yet measured.**
   See `10-kv-caching.md`.

A fourth lever is already taken: `reasoning_effort=none` on qwen removes hidden
reasoning outright — ~92 tokens a move, about 16% of the total. Verified at zero
reasoning tokens over 10 consecutive moves. See `09-model-and-max-tokens.md`.

## Environment at time of measurement

The configured default model does not work. Recorded here because every number
below was taken on a substitute, and absolute values will move with the model.

- `qwen/qwen3.6-27b` (the `LLM_MODEL` default in `llm.py`) returns **404
  model_not_found** on this Groq account.
- `qwen/qwen3.8-27b`, its successor, returns **429**: the SDK's default
  `max_tokens` of 2048 exceeds the tier's output budget, and `llm.py` sets no
  `max_tokens`.
- `openai/gpt-oss-20b` was used instead, at `reasoning_effort=low` — it rejects
  `none` with a 400 (`must be one of low, medium, or high`), so this run carries
  reasoning cost that the shipped qwen config was specifically chosen to avoid.
- **`qwen/qwen3.8-27b` works once `max_tokens` is passed**, at
  `reasoning_effort=none` and zero reasoning tokens. Verified live. Full detail and
  the numbers in `09-model-and-max-tokens.md`.
- Both substitutions were made through environment variables only. No repo file
  was modified by any of this work.

## Reproducing

```bash
backend/scripts/testdb.sh up
LLM_MODEL=openai/gpt-oss-20b LLM_REASONING_EFFORT=low \
  backend/.venv/bin/python plans/llm-token-optimization/scripts/drive_game.py
backend/scripts/testdb.sh down
```

The A/B and lookback scripts need no database; they call Groq directly and print
their own tables. All three read `GROQ_API_KEY` from `backend/.env`.
