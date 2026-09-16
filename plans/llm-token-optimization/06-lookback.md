<!-- role: research | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# Explaining an old move: does looking back cost more?

All figures in **tokens** — the free tier's binding constraint.

## The question

If a lazy explanation is generated on demand, what does it cost to explain move 3
when the game has reached move 40? Does reconstructing that far back get expensive?

## Method

A 20-move game was played on the lean schema, keeping each ply's context. Then,
**from the end of the game**, explanations were requested for plies 1, 5, 10, 15
and 20, in two shapes:

- **FLAT** — only that ply's stored context (position before the move, the
  message, the prior tone, the move played)
- **HISTORY** — the same, plus the moves played since, so the explanation can
  reflect how it turned out

Script: `scripts/lookback_test.py`.

## Results

| Target ply | Plies back | Shape | Input | Output | Total |
|---|---|---|---|---|---|
| 1 | 19 | flat | 266 | 64 | 330 |
| 1 | 19 | history | 351 | 121 | 472 |
| 5 | 15 | flat | 296 | 122 | 418 |
| 5 | 15 | history | 372 | 70 | 442 |
| 10 | 10 | flat | 294 | 108 | 402 |
| 10 | 10 | history | 355 | 155 | 510 |
| 15 | 5 | flat | 290 | 78 | 368 |
| 15 | 5 | history | 336 | 154 | 490 |
| 20 | 0 | flat | 288 | 71 | 359 |
| 20 | 0 | history | 324 | 74 | 398 |

```
FLAT    input 266-296, spread 30    avg 375 tokens/call
HISTORY input 324-372, spread 48    avg 462 tokens/call  (1.23x flat)
```

## The finding: lookback is free

**Input tokens span 266 to 296 across a 19-ply lookback — a 30-token spread.** And
that spread is not lookback at all: ply 1 is cheapest because `prior_tone` is empty
at the start of a game. From ply 5 onward it sits flat at ~290 regardless of
distance.

The reason is structural, and follows from the model being stateless
(`02-prompt-anatomy.md`). The explain call does not replay the game — there is no
game inside the model to replay. It assembles a fresh, self-contained prompt from
four stored facts about that ply. That prompt is the same size whether built at
move 4 or move 400.

**Cost: ~375 tokens per explanation, roughly half a move call.** A player who
browsed an entire 20-move game and pressed `?` on every move would spend ~7,500
tokens — most of one 8,000-token bucket, in one burst. On the free tier that is
the figure that matters: a single curious player browsing their game history could
throttle themselves.

## Adding game history is nearly free too

History adds **~1.4 input tokens per move of game**: 19 plies of SAN cost 27 extra
input tokens (351 vs 324). The history version also reads better — it knew ply 1
was a gambit that had been accepted.

**The headline "history is 1.23x flat" should not be trusted.** That ratio is
driven almost entirely by output variance (history output swung 70-155 across five
samples; flat swung 64-122) with n=1 per cell. The input-side figures are
deterministic and reliable; the output-side difference is noise.

## Prerequisite

Same as `05-ab-lazy-explain.md`: this only works if the per-ply context is stored.
It currently lives only in `llm_calls`, and the player's message is absent from
`moves` entirely.
