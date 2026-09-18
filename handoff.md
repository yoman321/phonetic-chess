# Handoff
<!-- role: maintenance (not one of AGENTS.md §1's roles) | model: claude-opus-5 | base: 337410aa37dfda94fb92295144780e2a7581d82b | date: 2026-09-18 -->

Feature:  llm-model-outage      Plan: none — see plans/llm-token-optimization/09-model-and-max-tokens.md      Status: n/a

Phase:    2 of 2 — model default changed; max_tokens now set

State:    Both changes from `09-model-and-max-tokens.md` are now in the tree.
          `LLM_MODEL` defaults to `qwen/qwen3.8-27b`, and
          `pick_move_with_llm` now passes `max_tokens=LLM_MAX_TOKENS` in the
          `chat.completions.with_raw_response.create` call
          (`backend/controller_operations/llm.py:215`). `LLM_MAX_TOKENS` is a
          new module-level constant, `os.environ.get("LLM_MAX_TOKENS", "400")`.
          Documented in `.env.example:15-16`, `README.md:138-140` and
          `docs/architecture.md:399-402`.
          The value was set to 200 earlier in this session at the user's
          request, then changed to 400 by the user on 2026-09-18 — matching the
          researched value, which was verified live over 10 moves at one
          position family with no truncation. Measured visible replies run
          84-102 tokens, so 400 is roughly 4x headroom. A reply that exceeds
          the cap is truncated, is invalid JSON, logs `bad_json` and burns all
          three `LLM_MAX_RETRIES` attempts.
Next:     Gate the value. Run real late-game positions, where `tone_summary`
          and `rationale` run longest, and confirm no truncation at 400. It is
          an env var now, so a change needs no code edit.
Blocked:  none.
Gates:    102/102 backend, 6/6 frontend. Failing: none.
Verified: (cd backend && .venv/bin/python -c "import controller_operations.llm") →
          LLM_MODEL=qwen/qwen3.8-27b, LLM_MAX_TOKENS=400,
          LLM_REASONING_EFFORT=none
          backend/scripts/testdb.sh up → throwaway Postgres started
          (cd backend && .venv/bin/pytest -q) → 102 passed in 4.62s
          (cd frontend && npm test) → 6 passed in 1.05s
          (cd frontend && npm run lint) → clean, no output
          (cd frontend && npm run build) → built in 119ms
          backend/scripts/testdb.sh down → stopped and removed
          `max_tokens` confirmed a real named parameter of the installed SDK at
          backend/.venv/.../openai/resources/chat/completions/completions.py:97,
          not written from memory.
          NOT verified: any live Groq call from this tree, on either the model
          or the 400 cap. The 400 evidence comes from the 2026-09-16 research
          run, not from this tree.
          <typecheck> has no command and <lint> is frontend-only, so on this
          backend change both are vacuous, not passing. Read for what they
          would catch: one new `int(os.environ.get(...))` constant and one added
          keyword argument. The keyword name matches the SDK signature, the
          value is an `int` and never `None`, and no exception path changed.
