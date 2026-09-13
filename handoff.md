# Handoff
<!-- role: Review the build | model: claude-opus-5 | base: a2ac156bbf2936ffcc238d3e1cd750700345859c | date: 2026-09-13 -->

Feature:  llm-call-log          Plan: plans/llm-call-log.md      Status: frozen
Phase:    4 of 4 — all phases built; reopened phase 1 reviewed

State:    Reviewed; the plan's two library claims are now demonstrated against
          live Groq rather than against fakes; and all 8 `## Build review`
          findings are [accepted] on the user's explicit instruction of
          2026-09-13, recorded as such in the section preamble. None is high.
          1, 3 and 7 are satisfied in the tree. 2, 4, 5 and 8 name work nobody
          has done yet, and 6 plus the stale `## Open decisions` section are
          edits to a frozen plan.
          Finding 3 is done: at the user's instruction this session simplified
          `README.md`, adding a one-item high-level note on the call log to
          *How It Works* and replacing the re-run paragraph with wording that
          matches what the file does. The finding stays [accepted]; a review
          session does not change states.
Next:     A Build session on `sol` for findings 2, 4, 5 and 8 — a gate that
          derives the outcome list from `pg_get_constraintdef`,
          `getattr(_client, "max_retries", SDK_MAX_RETRIES)`, one shared retry
          tail, and two BACKLOG entries trimmed. Finding 6 and the
          `## Open decisions` section are the human's own edits to the plan.
Blocked:  none. This session could not do the Build work itself: §1 puts Build
          on `sol`, and §0 forbids one session taking two roles.
Gates:    59/59. Failing: none.
Verified: backend/scripts/testdb.sh up → throwaway Postgres started, schema applied
          (cd backend && .venv/bin/pytest -q) → 102 passed in 4.60s
          (cd frontend && npm test) → 6 passed in 1.01s
          (cd frontend && npm run lint) → clean, no output
          (cd frontend && npm run build) → built in 114ms
          backend/scripts/testdb.sh down → throwaway Postgres stopped and removed
          Migration probe, fresh database built to the pre-amendment shape
          (three-value CHECKs, three-class view, one existing call row):
          psql -f backend/schema.sql applied twice with ON_ERROR_STOP=1, both
          clean; the pre-existing row survived; outcome='unexpected' accepted;
          llm_call_metrics → calls=2, ok=1, exhausted=0, transport=0,
          unexpected=1. Probe database dropped.
          Constraint swap timed on 300k rows → DROP 0.4ms, ADD 21ms.
          Live Groq, openai 1.109.1, qwen/qwen3.6-27b, key from backend/.env:
          with_raw_response.create(...) → LegacyAPIResponse, HTTP 200,
          retries_taken=0 (int), .parse() → ChatCompletion, same type as a plain
          .create(). pick_move_with_llm end to end with a real CallLog →
          outcome='ok', attempts=1, latency_ms=630, chosen_uci='e2e4',
          off_list=False; attempt row sdk_retries=0, status_code=None,
          error_detail=None, prompt_tokens=555, completion_tokens=99,
          reasoning_tokens=None, raw_content 440 chars.
          Groq returns usage.completion_tokens_details=None under
          reasoning_effort=none, so reasoning_tokens is NULL on the ordinary
          path — the plan's refusal to write 0 there is load-bearing.
          <typecheck> has no command and <lint> is frontend-only; on this
          backend-only change both are vacuous, not passing. Read the diff for
          the classes they would catch: the `log=` keyword at the one real call
          site and ignored by all five monkeypatched fakes; `insert_call`
          returning an int via the new `FakeCursor` shape; `usage`,
          `completion_tokens_details` and `message.content` each guarded for
          None; `_persist_call_log` catching Exception so nothing raised in the
          `finally` replaces an ApiError in flight.
