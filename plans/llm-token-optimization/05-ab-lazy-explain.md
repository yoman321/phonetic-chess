<!-- role: research | model: claude-opus-5 | base: efd31634e52d891fac6dd4010b7f3351ba4641d0 | date: 2026-09-16 -->

# A/B: inline vs on-demand `intent` + `rationale`

All figures in **tokens**. The project is on Groq's free tier, where the binding
constraint is the per-minute token bucket and input and output count equally.

## The question

`intent` and `rationale` are ~48% of the output JSON, generated on every move, and
displayed only when a player presses the `?` button (`Chatbox.jsx:52-64`). Is it
cheaper to stop asking for them and generate an explanation on demand?

**Lazy *fetch* saves nothing** — they are already written to `llm_calls`, so
removing them from the socket payload only shrinks a websocket message. Only lazy
*generation* saves anything.

## Method

Three prompt shapes, run against an **identical position** at the start, middle and
end of one 20-move game. Same message, same candidate list, same settings.

- **FULL** — `uci`, `tone_summary`, `intent`, `rationale` (as shipped)
- **LEAN** — `uci`, `tone_summary` only
- **LAZY** — a separate explain call producing `intent` + `rationale` after the
  fact, given only the message, position, prior tone and the move played

Script: `scripts/lazy_test.py`. Prompt variants are built there from the same
strings `llm.py` uses; no repo file was modified.

## Results

| Checkpoint | Shape | Input | Output | Reasoning | Total tokens |
|---|---|---|---|---|---|
| Start (ply 1) | FULL | 616 | 89 | 11 | **705** |
| | LEAN | 554 | 64 | 23 | **618** |
| | LAZY | 266 | 70 | 20 | **336** |
| Middle (ply 10) | FULL | 648 | 192 | 98 | **840** |
| | LEAN | 586 | 131 | 82 | **717** |
| | LAZY | 291 | 62 | 4 | **353** |
| End (ply 20) | FULL | 661 | 212 | 133 | **873** |
| | LEAN | 599 | 92 | 42 | **691** |
| | LAZY | 301 | 77 | 9 | **378** |

## Break-even click rate

How often players must press `?` before on-demand costs *more* tokens than inline:

| Checkpoint | FULL | LEAN | Saved | LAZY call | Break-even |
|---|---|---|---|---|---|
| Start | 705 | 618 | 87 | 336 | 25.9% |
| Middle | 840 | 717 | 123 | 353 | 34.8% |
| End | 873 | 691 | 182 | 378 | 48.1% |
| **Pooled** | 2,418 | 2,026 | 392 | 1,067 | **36.7%** |

**Below ~37% click rate, on-demand uses fewer tokens.** Above it, the current
inline design is better.

### This number was previously stated as ~60% — that was the dollar answer

The earlier figure weighted output at 4x input, because output is priced 4x on
paid tiers. On the free tier the bucket counts every token the same, and the
explain call is **input-heavy** (266-301 in, 62-77 out). Removing the price
weighting makes the extra call relatively more expensive, so the break-even falls
from ~60% to ~37%.

**~37% is the operative number.** The margin for on-demand is thinner than the
cost analysis suggested.

## Whole-game figures (LEAN, 20 moves)

```
avg input 579   avg output 107   avg reasoning 58
686 tokens/move
```

Against the full-prompt baseline of 903 tokens/move, LEAN is ~24% fewer tokens.

## Quality

The lazy explanations reference the actual move and position — not filler:

```json
{"intent":"The player acknowledges defeat but refuses to give up, choosing to fight on.",
 "rationale":"By sacrificing a knight with f6h5 (Nxh5), the player launches a
  desperate counter-attack..."}
```

## Side finding: the SAN fallback

`board.parse_san()` was added to the test harness only to stop the game dying on
notation mismatches. It **rescued 1 of 20 moves (5%)** that the shipped code would
have failed after three attempts — independently reproducing the 5.7% failure rate
from `01-baseline.md`.

This lever stands on its own and is unrelated to the lazy/inline decision.

## Reliability

- **Solid — the input delta: exactly 62 tokens.** Measured 5 times out of 5
  (616-554, 648-586, 661-599, and 655-593, 616-554 in an earlier aborted run).
  Input tokenization is deterministic and the two system prompts differ by a fixed
  string, so the delta cannot vary.
- **Solid — the visible-output share: 48.0%**, over 20 calls (`03-output-anatomy.md`).
- **Directionally sound — total token delta ~16%/move** (392 saved over 2,418).
- **NOT established — the reasoning component.** See `08-reliability.md`.

## Blocked on

The `?` click rate. Nothing logs it. Below ~37% lazy wins; above it, the current
design does. One week of click instrumentation settles a question no further token
measurement can.

## If built

The per-ply context a lazy explanation needs — position before the move, and the
player's message — currently lives **only in `llm_calls`**, a log table whose
writer (`_persist_call_log`, `sessions_ops.py:33-45`) runs outside the move
transaction and swallows exceptions by design. Reading product features from it
turns a harmless logging failure into "the move worked but its explanation is gone
forever."

`moves` stores only `uci`, `san` and `tone_summary` (`sessions_ops.py:336`). The
position is recoverable by replaying the PGN; the player's message is not stored
there at all. If explanations become a product feature, the message belongs in
`moves`.
