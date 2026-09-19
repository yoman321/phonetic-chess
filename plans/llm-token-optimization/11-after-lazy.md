<!-- role: Build | model: gpt-5 | base: b2889abb7506c69214f86b6a2451d6a3bc59d61d | date: 2026-09-18 -->

# After lazy explanations and eight candidates

Run on 2026-09-18 using the shipped prompt, `qwen/qwen3.8-27b`,
`reasoning_effort=none`, `max_tokens=400`, and the throwaway Postgres at
`127.0.0.1:55432`. No development or production data was used.

Session: `costrun-fc62ab97`. `drive_game.py` played 20 committed moves.
All 20 succeeded on their first content-loop attempt. Every move has one
analysis call and one attempt with provider usage; no analysis writes are
missing from this run.

| Measured | Total | Mean per committed move |
|---|---:|---:|
| Input tokens | 8,967 | 448.35 |
| Output tokens | 943 | 47.15 |
| Input + output | 9,910 | 495.50 |

These are provider usage counts, summed across the attempts of each logged
committed move. Reasoning-token details were NULL, so no measured reasoning
count is claimed. Content-loop attempt counts do not measure SDK HTTP retries.

The historical **903 tokens per move (708 input, 194 output)** remains the
2026-09-16 baseline on `openai/gpt-oss-20b` with reasoning enabled. That model
and reasoning setting differ from this run. The change in absolute totals
cannot be assigned wholly to lazy explanations or the shorter candidate list.

## Fixed-input live gate

`test_the_lean_prompt_costs_at_least_55_fewer_input_tokens` passed live.
It compares FULL and LEAN using the same current model, eight candidates,
position, player message, prior tone, reasoning setting and output ceiling.
The provider reported at least 55 fewer input tokens for LEAN. This isolates
the explanation instructions; it does not measure the candidate-list saving.

## One real explanation and a cached reopen

The first move was explained using its saved game context. The answer was
checked in `moves.intent` and `moves.rationale`. Generation used **185 input +
66 output = 251 tokens**, with **1,400 ms** operation latency. A later request
returned the same answer while the LLM client was replaced with a trap that
raises on any access: **zero provider calls**. Both requests were counted,
giving `explain_requests=2`. The session FEN, PGN and status stayed unchanged.

This is one explanation sample, not a measured player click rate or break-even
rate. Locally cached panel reopens do not contact the server and do not raise
the analysis request count.

## Per-move provider usage

| Ply | Input | Output | Total | Content attempts |
|---:|---:|---:|---:|---:|
| 1 | 423 | 36 | 459 | 1 |
| 2 | 442 | 43 | 485 | 1 |
| 3 | 457 | 38 | 495 | 1 |
| 4 | 420 | 38 | 458 | 1 |
| 5 | 451 | 38 | 489 | 1 |
| 6 | 400 | 52 | 452 | 1 |
| 7 | 463 | 53 | 516 | 1 |
| 8 | 462 | 48 | 510 | 1 |
| 9 | 460 | 56 | 516 | 1 |
| 10 | 472 | 48 | 520 | 1 |
| 11 | 463 | 51 | 514 | 1 |
| 12 | 469 | 56 | 525 | 1 |
| 13 | 469 | 51 | 520 | 1 |
| 14 | 468 | 43 | 511 | 1 |
| 15 | 461 | 43 | 504 | 1 |
| 16 | 392 | 58 | 450 | 1 |
| 17 | 461 | 50 | 511 | 1 |
| 18 | 461 | 48 | 509 | 1 |
| 19 | 410 | 53 | 463 | 1 |
| 20 | 463 | 40 | 503 | 1 |

## Reproduce

```bash
backend/scripts/testdb.sh up
backend/.venv/bin/python plans/llm-token-optimization/scripts/drive_game.py
# Keep the printed SESSION_ID for the SQL below.
# Export GROQ_API_KEY from your local environment before the live gate.
(cd backend && LLM_LIVE=1 .venv/bin/pytest -q tests/test_move_prompt_shape.py::test_the_lean_prompt_costs_at_least_55_fewer_input_tokens)
backend/scripts/testdb.sh down
```

The script loads `backend/.env`; the live gate needs an exported key.
`lazy_test.py` and `lookback_test.py` snapshot the old prompt and are not the
shipped implementation.

## Analysis queries

Before stopping the throwaway database, replace `<SESSION_ID>` with the
printed run ID:

```sql
SELECT c.ply, c.attempts, SUM(a.prompt_tokens) AS input_tokens,
       SUM(a.completion_tokens) AS output_tokens,
       COUNT(a.prompt_tokens) AS attempts_with_usage
FROM llm_calls c
JOIN llm_call_attempts a ON a.call_id = c.id
WHERE c.session_id = '<SESSION_ID>' AND c.outcome = 'ok'
GROUP BY c.id ORDER BY c.ply;

SELECT COUNT(*) AS logged_committed_moves,
       SUM(explain_requests) AS server_explanation_requests,
       COUNT(explain_prompt_tokens) AS logged_successful_explanations,
       SUM(explain_prompt_tokens) AS explanation_input_tokens,
       SUM(explain_completion_tokens) AS explanation_output_tokens
FROM llm_calls
WHERE session_id = '<SESSION_ID>' AND outcome = 'ok';

SELECT ply, player_text, pre_move_fen, prior_tone, intent, rationale
FROM moves WHERE session_id = '<SESSION_ID>' ORDER BY ply;
```

Analysis writes can fail without affecting the game. A missing row means
incomplete telemetry, not zero cost. Success-only move logging excludes
entire invocations that failed or rolled back; it cannot measure their full
cost. Explanation usage copies reflect the final successful attempt and do
not capture earlier malformed attempts or failed generations. Historical
failed-call analysis rows remain. The game never reads these records.

## Verification

- Before implementation: Phase 1 checks reported `25 failed, 40 passed, 5 deselected`.
- After Phase 1: `65 passed, 5 deselected`.
- Full backend: `137 passed, 1 skipped`; the skipped live token gate was then run separately and passed.
- Frontend: `17 passed` across three files.
- Frontend lint and production build passed.
- No typecheck exists. Backend typecheck and lint are vacuous, not passing.
- Backend changes were read for return arity, optional context arguments, NULL context, and mapped exceptions.
- No manual browser smoke test was run; GameView gates exercise first open, cached reopen, prior tone, and card-local errors.
