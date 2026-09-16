# Scripts

Each reads `GROQ_API_KEY` from `backend/.env` and is run from the repo root with
the backend venv. None modifies any repo file.

| Script | Needs a database? | Produces |
|---|---|---|
| `drive_game.py` | yes | plays a full game through `say_move`, filling `llm_calls` / `llm_call_attempts` — the source for `01-baseline.md` |
| `lazy_test.py` | no | the inline-vs-lazy A/B table in `05-ab-lazy-explain.md` |
| `lookback_test.py` | no | the old-move explanation table in `06-lookback.md` |

```bash
# with a database
backend/scripts/testdb.sh up
LLM_MODEL=openai/gpt-oss-20b LLM_REASONING_EFFORT=low \
  backend/.venv/bin/python plans/llm-token-optimization/scripts/drive_game.py
backend/scripts/testdb.sh down

# without
backend/.venv/bin/python plans/llm-token-optimization/scripts/lazy_test.py
backend/.venv/bin/python plans/llm-token-optimization/scripts/lookback_test.py
```

`drive_game.py` hardcodes the throwaway database URL
(`127.0.0.1:55432`) so it can never touch development data.

Both no-database scripts build their prompt variants from the same strings
`controller_operations/llm.py` uses. If that file's prompts change, these copies
go stale — they are a snapshot of the 2026-09-16 wording, not an import.
