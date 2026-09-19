# Handoff
<!-- role: Review the build | model: claude-opus-5 | base: b2889abb7506c69214f86b6a2451d6a3bc59d61d | date: 2026-09-18 -->

Feature:  input-tokens-lazy-explanations      Plan: plans/input-tokens-lazy-explanations.md      Status: frozen
Phase:    4 of 4 — done

State:    The feature is done. All four phases are built, every gate is green, and the
          human ran the manual browser test on 2026-09-18 and reported it passing.
          Human decision on 2026-09-18: disregard every Build review finding. The two
          findings in "## Build review" are still written as [open] in the plan file,
          because only a human may change a finding's state. Their text is the record;
          the decision is here. Flip them to [rejected] if you want the file to match.
Next:     Nothing for this feature. Start the next token-optimization feature, the
          output-token work named in plans/llm-token-optimization/. The deferred items
          are the board.parse_san() retry fix and shortening the system prompt.
Blocked:  none.
Gates:    Backend 137 passed, 1 skipped (the live-token gate; needs LLM_LIVE=1 and a key).
          Frontend 17/17 passed across 3 files. Failing: none.
Verified: backend/scripts/testdb.sh up -> throwaway database ready.
          (cd backend && .venv/bin/pytest -q) -> 137 passed, 1 skipped in 4.95s.
          (cd frontend && npm test) -> 17 passed (3 files).
          (cd frontend && npm run lint) -> clean.
          (cd frontend && npm run build) -> built in 114ms.
          <typecheck> has no command. Backend typecheck and lint are vacuous, not passing.
          Read the whole backend diff vs base for the classes of error a type checker
          would catch: return arity of pick_move_with_llm and explain_move_with_llm,
          insert_move's new keyword arguments, NULL context columns reaching
          explain_move, optional usage in the analysis copy, and the mapped
          llm_unavailable / llm_bad_response paths. No defect of those classes found.
          Checked against the invariants: I1, I3, I5, I6, I12 and I13 all hold. No
          product path reads llm_calls; every llm_calls_q call is an insert or update.
          Manual browser test: run by the human against the dev servers below, passing.
          Explain route smoke, no LLM call spent: wrong token -> 403 not_a_player,
          no token -> 401 missing_token.

          Dev servers started this session and possibly still running:
            backend  127.0.0.1:5001, backend/application.py, DATABASE_URL overridden
                     to the throwaway Postgres at 127.0.0.1:55432.
            frontend localhost:5173, vite dev.
          Stop them, then backend/scripts/testdb.sh down. Docker was not running on
          this machine and nothing listened on 5432, so no development database was
          touched and none was changed. Apply backend/schema.sql to a real development
          database before running this code against one.

          Nothing is committed. No commit, push, tag or dependency change was made.
