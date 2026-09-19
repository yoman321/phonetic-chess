<!-- role: Grade the plan | model: gpt-5 | base: b2889abb7506c69214f86b6a2451d6a3bc59d61d | date: 2026-09-18 -->
Status: frozen
Phases: 4

# Input tokens, part 1 — lazy explanations and a shorter move list

## Context

The research in `plans/llm-token-optimization/` measured one move at
**903 tokens (708 in, 194 out)**. These are historical measurements.
The original spec was written by Plan / claude-opus-5 on 2026-09-18,
at base `b2889abb7506c69214f86b6a2451d6a3bc59d61d`.

Two levers are in scope, chosen by the user on 2026-09-18:

1. Move `intent` and `rationale` to on-demand generation when a player presses
   `?`, and reuse the answer once it is stored. The research measured a
   62-input-token reduction and ~48% of visible output JSON for these fields.
2. Cut the default engine candidate list from 15 to 8 moves. The research
   estimated ~25 fewer input tokens per move.

The research estimated ~633 → ~546 input tokens per move (~14%).
The `board.parse_san()` retry fix and shortening the system prompt remain
out of scope. Both come after the output work.

The research's ~37% break-even rate is an accepted, uncertain estimate.
Analysis counters are optional telemetry. Missing logs must never change
the game or be treated as required product data.

## Human decisions — 2026-09-18

The user approved incorporating the reviewed high-priority decisions into
this spec and freezing it. This approval permits this review session to edit
the spec, apply the human's finding decisions, and set `Status: frozen`.

- Analysis logs are only for personal use and data analysis. The LLM system,
  game, and `?` feature must never read them.
- Persist analysis logs only for LLM moves whose game transaction committed.
  A successful LLM reply alone is not a successful move.
- Analysis writes can always fail. They must never fail, undo, or change the
  game, move delivery, explanation behavior, or a successful response.
- Store explanation context and cached answers with the committed move.
- Updating the superseded tests and fakes is mandatory in `Write the gates`,
  before implementation. It is not optional.
- Concurrent requests for the same explanation are accepted as rare.
  Preventing duplicate concurrent LLM calls is out of scope.
- The three remaining high findings are accepted. The five medium findings
  are rejected for this plan. Their proposed extra work is not required.

## Invariants

**Move prompt**

- I1 — The move prompt asks for exactly two JSON fields: `uci` and
  `tone_summary`. Its instructions contain neither `intent` nor `rationale`.
- I2 — At one fixed position, message and tone summary, the new move prompt's
  `usage.prompt_tokens` is at least 55 tokens lower than the old one's.
- I3 — The default `rank_moves` returns at most 8 pairs, and every legal move
  when fewer than 8 exist.
- I4 — A move still completes with chosen `uci` in `valid_ucis`. Preserve the
  existing tone-summary handling, retry and backoff behavior. Drop the two
  inline explanation fields. Analysis persistence changes as stated below;
  no extra tone-summary validation is added in this plan.

**On-demand explanation**

- I5 — Opening `?` for an eligible committed move without a cached answer
  invokes explanation generation and stores `intent` and `rationale` with
  that move. Normal LLM retries are allowed. There is no guarantee of one
  shared invocation across concurrent cache misses.
- I6 — Once an explanation is successfully cached, a later request for that
  move makes zero LLM calls and returns the stored answer.
- I7 — Each server explanation request attempts to increment the analysis
  request counter, including cache hits. Missing analysis rows or failed
  writes do not affect the request's result. Locally cached panel reopens
  need no server request.
- I8 — An explanation failure shows an error in its panel. It does not change
  the board, existing move history, or session state, and it does not enter
  the move path. Successful explanation caching may update only the move's
  explanation fields.
- I9 — Prior tone renders from the `move` payload without a fetch for prior tone.

**Storage and analysis**

- I10 — No new dependency.
- I11 — Explanation usage and latency are copied to analysis storage on a
  best-effort basis. Missing telemetry must not change product behavior.
- I12 — New LLM moves store their player message, pre-move FEN and prior tone
  atomically with the move in game storage. Explanation context and cache
  lookups use `moves`, keyed by its existing `(session_id, ply)` primary key.
  They never use `llm_calls`, `llm_call_attempts`, or `llm_call_metrics`.
- I13 — Write a move's analysis call and attempt rows only after the game
  transaction commits. LLM failure, move-write failure, or commit failure
  produces no new analysis rows for that invocation. Logging failure leaves
  the successful move, emitted move payload, and response intact.
- I14 — The test-case phase must update the superseded inline-explanation,
  return-arity, failed-call and rolled-back-move expectations before Build.
  Build sessions cannot edit gates to reach green.

## Required test-case phase — before Phase 1 implementation

Start a `Write the gates` session against this frozen plan. Updating existing
test cases and fakes for the approved behavior is mandatory, not optional.

- Update four-value `pick_move_with_llm` fakes to the new two-value contract.
- Replace assertions that every move logs inline `intent` and `rationale`.
- Replace gates requiring logs for exhausted LLM calls, transport failures,
  and rolled-back moves with zero-new-log assertions for those paths.
- Keep and run the guarantee that a failed analysis write never fails a move.
- Add gates for committed-success logging, atomic game-owned explanation
  context, cache reuse, explanation failure isolation, and operation when
  analysis logging is unavailable.
- Gate that product explanation reads never use analysis tables. Optional
  analysis writes remain allowed.
- Observe each new behavior gate failing for missing behavior before Build.
  Fakes and fixtures must be usable so failures are not import or arity errors.

These targeted updates are explicitly authorized by the user. Preserve all
unrelated invariants. No single-flight or duplicate-call-prevention gate is
required. Implement one numbered phase at a time and stop at its boundary.

## Phase 1 — shrink the move prompt and log committed moves only

`backend/controller_operations/llm.py`

- Delete the instructions and JSON fields for `intent` and `rationale`.
- Return `(uci, tone_summary)`; remove the two parsed fields and their
  `log.finish` arguments. Update the docstring.
- Keep `CallLog.finish`'s optional explanation parameters for compatibility;
  they are analysis fields, not the product's explanation cache.

`backend/controller_operations/engine.py`

- Change `ENGINE_TOPN_DEFAULT` from 15 to 8.

`backend/controller_operations/sessions_ops.py`

- Unpack two values and remove `intent` and `rationale` from the move payload.
- Remove analysis persistence from the outer unconditional `finally`.
  Persist only after the game transaction has exited successfully and committed.
- Keep the existing best-effort, independently atomic analysis write and its
  exception handling. A logging failure must not suppress the move event or
  replace the successful response.
- Preserve the thinking-indicator cleanup on every move path.

## Phase 2 — store and serve an explanation from game data

`backend/schema.sql` — add nullable product columns in the existing
`ALTER TABLE ... ADD COLUMN IF NOT EXISTS` idiom:

```sql
ALTER TABLE moves ADD COLUMN IF NOT EXISTS player_text TEXT;
ALTER TABLE moves ADD COLUMN IF NOT EXISTS pre_move_fen TEXT;
ALTER TABLE moves ADD COLUMN IF NOT EXISTS prior_tone TEXT;
ALTER TABLE moves ADD COLUMN IF NOT EXISTS intent TEXT;
ALTER TABLE moves ADD COLUMN IF NOT EXISTS rationale TEXT;
```

New LLM moves fill the three context columns in the same transaction as the
move. Manual moves leave them NULL. Old moves are not backfilled from analysis
logs. If required product context is missing, return an explanation-unavailable
error rather than consulting analysis storage.

`backend/queries/moves.py`

- Extend `insert_move` with optional context arguments so manual moves retain
  their existing behavior.
- Add `get_for_explain(cur, session_id, ply)` returning the committed move's
  UCI, SAN, player message, pre-move FEN, prior tone, `intent`, and `rationale`.
- Add `save_explanation(cur, session_id, ply, intent, rationale)` to cache the
  answer on that move. Do not hold the session row lock during generation.
  No prevention of duplicate concurrent generation is required.

`backend/controller_operations/sessions_ops.py`

- Pass the player message, original FEN and prior tone into the LLM move insert
  before committing. These values are product data, not analysis records.
- Add `explain_move(db, sid, ply, player_token)`: validate the session and player
  token, fetch the move from `moves`, and return its cached answer if present.
  Otherwise generate and save the answer in game storage, then return it.
- Use the requested move's saved context, never the current board state and
  never a row from an analysis table.
- Map LLM failures to `llm_unavailable` or `llm_bad_response` as before.
  No socket emit, board update, session write, or new move insertion occurs.
- Attempt analysis copies and counters separately as best-effort writes.
  Do not branch on their success, affected-row count, or availability.

`backend/controller_operations/llm.py`

- Add `explain_move_with_llm(text, fen, prior_tone, uci, san)` using the LAZY
  prompt shape in `plans/llm-token-optimization/scripts/lazy_test.py`.
- Reuse the current client, JSON response format, `LLM_MAX_TOKENS`, reasoning
  settings and retry-loop shape. Return `(intent, rationale, usage)`.

`backend/controller/sessions.py` — add:

```python
@bp.post("/sessions/<sid>/moves/<int:ply>/explain")
```

Take `playerToken` in the JSON body.

**Best-effort analysis copies**

Add these analysis columns to `llm_calls`:

```sql
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS explain_requests INTEGER NOT NULL DEFAULT 0;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS explain_prompt_tokens INTEGER;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS explain_completion_tokens INTEGER;
ALTER TABLE llm_calls ADD COLUMN IF NOT EXISTS explain_latency_ms INTEGER;
```

`backend/queries/llm_calls.py` provides best-effort write helpers for these
counters, usage and explanation copies. They are only for personal analysis.
Do not add a product `get_for_explain` there. A missing log row is harmless.
Historical failed-call rows remain analysis records and never enter the game.

## Phase 3 — frontend fetches on `?`

`frontend/src/api.js`

- Add `explainMove(sessionId, ply, playerToken)`, following `postSay`.

`frontend/src/modules/GameView/GameView.jsx`

- Attach `explanation` to LLM move messages even when prior tone is empty.
  Carry `ply` and the existing prior tone; leave inline explanation fields
  undefined.
- Provide the session ID, player token and message-update callback needed by
  the explanation handler.

`frontend/src/modules/Chatbox/Chatbox.jsx`

- When opening an uncached panel, call `explainMove` and show a loading line.
- Store the returned answer in the relevant message. Cached reopens use it
  without another network request.
- Show failures inside the card. Prior tone renders immediately.
- Do not add duplicate-call prevention as a requirement.

## Phase 4 — measure

- Re-run `plans/llm-token-optimization/scripts/drive_game.py` with the throwaway
  database and report observed tokens per logged successful move.
- Retain the 903-token figure as a historical comparison. Success-only logging
  no longer captures the full cost of failed move invocations.
- Write results and the analysis request-count query in
  `plans/llm-token-optimization/11-after-lazy.md`. Do not edit prior research.
- Note in that directory's `README.md` that `lazy_test.py` and
  `lookback_test.py` snapshot the old prompt.
- Missing analysis writes can leave incomplete measurements. The product must
  never read these measurements or use them to decide moves or explanations.

## Verification (AGENTS.md §7)

```bash
backend/scripts/testdb.sh up
(cd backend && .venv/bin/pytest -q)
(cd frontend && npm test)
(cd frontend && npm run lint)
(cd frontend && npm run build)
backend/scripts/testdb.sh down
```

No typecheck exists; backend lint and typecheck are vacuous, not passing.
Read the backend diff for changed return arity, optional context arguments,
nullable old-move columns, and mapped exception paths.

By hand: play an LLM move, open `?`, check the answer fills, then close and
reopen it with no second request. Check context and cache in `moves`, not logs:

```sql
SELECT ply, player_text, pre_move_fen, prior_tone, intent, rationale
FROM moves WHERE session_id = '<test session>' ORDER BY ply;
```

Use gates to prove an analysis failure leaves successful moves and explanations
working, and a failed or rolled-back move creates no new analysis rows.

Update `README.md` and `docs/architecture.md` for the route, product columns,
analysis-only storage boundary, and success-only logging.

## Plan review

<!-- role: Grade the plan | model: gpt-5 | base: b2889abb7506c69214f86b6a2451d6a3bc59d61d | date: 2026-09-18 -->

- [accepted] high — plans/input-tokens-lazy-explanations.md:98 — Phase 2 reads move context and cached explanations from `llm_calls`, but the user requires logs to serve personal use and data analysis only, never the LLM system or game; best-effort log writes can also leave no row, breaking I5 and I6 — store the player message, pre-move context, and cached explanation in game-owned storage keyed to the committed move; remove all product-path reads of analysis logs, and make analysis writes optional copies whose failure cannot affect moves, prompts, explanation generation, cache reuse, or successful responses; gate the product path with unavailable analysis logs.
- [accepted] high — plans/input-tokens-lazy-explanations.md:47 — I4 preserves existing logging behavior, but the user requires logging only successful committed LLM moves; the current outer `finally` also logs LLM failures and successful replies whose move transaction rolled back — state that success means the move transaction committed, call the best-effort analysis persister only after that commit, write no call or attempt rows for failed or rolled-back moves, and keep any log write failure from changing game state, move delivery, or the successful response; historical analysis rows must never be consulted by the product.
- [accepted] high — plans/input-tokens-lazy-explanations.md:72 — Phase 1 changes the return arity and removes inline explanation fields, while old fakes return four values and old gates require those fields; the user's success-only logging rule also conflicts with gates requiring logs for exhausted calls, transport failures, and rolled-back moves — the user explicitly requires these test cases and fakes to be updated in the test-case phase (`Write the gates`), before implementation; this update is mandatory, not optional, and authorizes replacing the superseded expectations for the removed inline fields and failed-move logs; include gates for committed-success logging, zero logs on failed or rolled-back moves, analysis logs never being read by the product, and unchanged game success when logging fails; no Build session may edit or weaken gates to reach green.
- [rejected] med — plans/input-tokens-lazy-explanations.md:114 — validating “the way `say_move` does” leaves turn, last-mover, game-status and locking rules unspecified; copying them rejects a player's own move explanation just after that move and can hold the session lock throughout Groq, affecting the move path — list the exact explanation guards and error responses, authenticate either player without turn or last-mover checks, and keep generation off the session row lock.
- [rejected] med — plans/input-tokens-lazy-explanations.md:55 — `explain_requests` counts server requests, but Phase 3 skips requests on locally cached reopens, and repeated requests from both players can exceed one per ply; this is not the fraction of moves needing a generated explanation used by the 37% break-even calculation, and I7's guaranteed log increment conflicts with optional analysis logging — define separate best-effort analysis counts for explanation generations and panel opens, use distinct explained committed plies over eligible committed plies for the generation rate, and label incomplete telemetry without using log counters to control product behavior.
- [rejected] med — plans/input-tokens-lazy-explanations.md:109 — returning one `usage` and saving it only with a successful explanation omits tokens spent on malformed replies before a retry succeeds and on requests that exhaust retries, so Phase 4 understates explanation cost when those analysis records are available — collect per-attempt usage for explanations of committed moves and persist it as best-effort analysis, independently of the product's cached answer; log failures must never block or fail the explanation, and missing telemetry must be reported as incomplete rather than zero.
- [rejected] med — plans/input-tokens-lazy-explanations.md:47 — I4 requires a non-empty `tone_summary`, but the existing parser accepts a legal UCI with a missing or blank summary and Phase 1 only removes fields; the prescribed change does not enforce this invariant — add explicit non-empty string validation and state its retry/error outcome, with gates for missing and whitespace-only summaries.
- [rejected] med — plans/input-tokens-lazy-explanations.md:145 — the 903-token baseline and 62-token ablation were measured on `openai/gpt-oss-20b` with `reasoning_effort=low`, while the current defaults are `qwen/qwen3.8-27b`, `none`, and a 400-token cap; comparing those runs attributes model and reasoning changes to this feature, and I2 leaves its model and baseline fixture undefined — define a fixed before/after prompt fixture and measure both arms using identical current model, reasoning, cap and inputs; report move and explanation attempt totals separately and retain 903 only as a dated historical comparison.

## Build review

<!-- role: Review the build | model: claude-opus-5 | base: b2889abb7506c69214f86b6a2451d6a3bc59d61d | date: 2026-09-18 -->

- [open] med — backend/controller_operations/sessions_ops.py:373 — `explain_move` increments the analysis counter before it checks the player token, the session, or the move, so any anonymous caller writes to `llm_calls` for any guessed session id and every rejected 401/403/404 request is counted as an explanation request — move the best-effort `increment_explain_requests` block below the token, session and move lookups, and gate that a stranger's and a token-less request leave `explain_requests` unchanged.
- [open] low — frontend/src/api.js:79 and frontend/src/modules/Chatbox/Chatbox.jsx:77 — `explainMove` throws `body.error` and the card renders it verbatim, so a player sees the raw codes `llm_unavailable`, `llm_bad_response` or `explanation_unavailable` instead of a sentence, and an old or dragged move gives no hint why it has no explanation — map the known codes to short player-facing text in the card and keep the raw code only in the thrown error.
