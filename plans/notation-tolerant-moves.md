<!-- role: Grade the plan | model: gpt-5.6-sol | base: f3980c5998ba3954d5af77c2cd3967a87c89070c | date: 2026-09-21 -->
Status: frozen
Phases: 4

# Notation-tolerant move parsing

Accept a legal move the model wrote in a notation we did not ask for, instead
of throwing it away and paying for another attempt.

This recovers wasted tokens and converts player-facing errors into moves. It
does not make the model pick better moves.

The original draft was written by Plan / claude-opus-5 on 2026-09-21,
at base `f3980c5998ba3954d5af77c2cd3967a87c89070c`.

## Why

`llm.py:208` is the whole problem:

```python
if uci not in valid_ucis:
    raise ValueError(f"llm returned invalid uci: {uci!r}")
```

`valid_ucis` is a set of UCI strings. The model answers `fxe5`; that string is
not in the set; the move is discarded and a full attempt is re-billed. `fxe5`
was legal chess written the ordinary way.

Measured, in `plans/llm-token-optimization/`:

- Every observed content failure was this notation mismatch (`01-baseline.md`).
- A `parse_san()` fallback rescued **1 move in 20** in an independent run
  (`05-ab-lazy-explain.md`).
- The failure class burns **14.6% of tokens** and produced three player-facing
  errors in the baseline run (`README.md`, lever 1).

Lever 1 of three. Levers 2 and 4 shipped; lever 3 is half shipped. This is the
only one never started.

## Decision: the parser is the library's, the cleanup is ours

`chess.Board.parse_san` already accepts far more than SAN. Verified against the
installed `python-chess` at `backend/.venv/lib/python3.14/site-packages/chess/`
on 2026-09-21, on a real board:

| Accepted | Example |
|---|---|
| SAN | `exd5`, `Nf3` |
| SAN with check or mate mark | `exd5+`, `Nf3#` |
| Long algebraic | `e4-d5`, `Ng1-f3` |
| Overspecified | `Ng1f3`, `e4xd5` |
| **UCI** | `g1f3`, `a7a8q` |
| Castling, letters or zeros | `O-O`, `0-0` |
| Castling, UCI | `e1g1`, `e1h1` |
| Promotion, any spelling | `a8=Q`, `a8Q`, `a7a8Q` |

| Rejected | Example |
|---|---|
| Annotation marks | `exd5!`, `Nf3?!` |
| Surrounding whitespace | `  e4  ` |
| Wrong case | `NF3`, `nf3` |
| Figurine | `♘f3` |
| Descriptive | `N-KB3` |
| Prose | `knight to f3` |

Because UCI is in the accepted column, one call replaces the current check
rather than sitting beside it. There is no two-branch fallback.

We add exactly two things the library does not do: strip surrounding
whitespace, and strip trailing `!` and `?`. Both are decoration that does not
change which move is meant.

We do **not** fix case. `b4` is a pawn move and `B4` is not the same thing;
correcting case means guessing at meaning, and a wrong guess puts a wrong move
on the board. A retry is cheaper than that.

We do **not** edit `python-chess`. It is a dependency under `.venv`; an edit
there is erased by the next install and is forbidden by AGENTS.md section 11.

## Trap: parse_san returns a null move for four inputs

Verified 2026-09-21 against the installed source and a live board: `--`, `Z0`,
`0000` and `@@@@` all parse successfully and return `chess.Move.null()`, whose
uci is `"0000"`. The docstring says the result is "guaranteed to be either
legal or a null move" — null is a success, not an error.

`"0000"` is never in `valid_ucis`, so it would be rejected downstream anyway.
The normalizer must still reject it explicitly, because a parser that returns
success for `Z0` is a trap for whoever reads this next.

## Scope

In scope:

1. One normalizer function, built from a board, that turns a written move into
   a canonical UCI string or `None`.
2. Threading it into the existing validation point in `llm.py`.
3. The `say_move` call site.
4. A post-rollout query that can measure rescues from columns that already
   exist.

Out of scope:

- `explain_move_with_llm` — it returns prose, not a move.
- The prompt text. No wording changes; see invariant I8.
- `valid_ucis` as the authority. It stays the gate.
- The candidate list, the model, the retry cap, `max_tokens`.
- Any database schema change.
- The natural-language and figurine rows above. Rare, and risky to guess at.

## Current code facts

- `llm.py` imports `json`, `os`, `time`, the OpenAI SDK, and `error_logger`.
  It imports no chess module and knows no chess rules. This is worth keeping.
- `_pick_move_from_messages` (`llm.py:169`) receives `valid_ucis` and does the
  membership test at line 208. It has no board and no FEN.
- `pick_move_with_llm` (`llm.py:281`) takes `valid_ucis=None`, defaulting to
  the candidate UCIs.
- `sessions_ops.py:279` builds `all_legal_ucis` from `board.legal_moves` and
  passes it at line 319, so the candidate list is advisory today.
- `sessions_ops.py:330` does `chess.Move.from_uci(chosen_uci)` then
  `board.san(move)`. Both require a canonical UCI.
- `engine.py` already imports `chess` and already computes `board.san(m)` for
  every legal move at lines 98, 109 and 111.
- `_content_outcome` (`llm.py:135`) maps a bare `ValueError` to the string
  `invalid_uci`. `json.JSONDecodeError` is checked first.
- `CallLog.add_attempt` stores `raw_content` on the successful attempt. This is
  what makes phase 4 need no schema change.

## Invariants

- **I1** Every answer accepted today is still accepted, and yields the same
  chosen UCI. No regression on the existing path.
- **I2** A legal move written in any notation in the Accepted table above,
  optionally surrounded by whitespace and optionally followed by `!` or `?`
  marks, is converted to its canonical UCI and accepted on the first attempt.
- **I3** `valid_ucis` remains the authority. A normalized UCI that is not in
  `valid_ucis` is rejected exactly as today.
- **I4** The normalizer never raises. Anything it cannot resolve returns
  `None`, including empty string, prose, wrong case, and an illegal or
  ambiguous move.
- **I5** The normalizer never returns `"0000"` or any null move, for any input,
  including `--`, `Z0`, `0000` and `@@@@`.
- **I6** Character case is never modified before parsing.
- **I7** A rejected answer still raises `ValueError` and still retries. The
  logged outcome is still `invalid_uci`. No new outcome value, no schema change.
- **I8** `build_move_prompt` returns byte-identical system and user messages to
  those it returns at base. Input token cost per move is unchanged: this
  feature saves retries, never prompt tokens.
- **I9** `llm.py` imports no chess module. The chess knowledge stays behind the
  callable it is handed.
- **I10** The canonical UCI is what is returned, stored in `moves.uci`, used
  for `board.san`, stored in `llm_calls.chosen_uci`, appended to the PGN, and
  put in the response payload. The model's spelling is never persisted as the
  move. It remains only in `llm_call_attempts.raw_content` for analysis.
- **I11** With no normalizer supplied, `pick_move_with_llm` behaves exactly as
  it does at base. Every existing caller and test that passes only
  `valid_ucis` keeps its current behavior.
- **I12** `explain_move_with_llm` is untouched.
- **I13** An ambiguous move is rejected, not guessed. `parse_san` raises
  `AmbiguousMoveError`; the normalizer returns `None` for it like any other
  failure.

## Phase 1 — the normalizer

New function in `backend/controller_operations/engine.py`. It goes here, not in
`sessions_ops.py`, because `engine.py` is already the module that holds chess
knowledge and already derives SAN from a board. `sessions_ops.py` is request
orchestration.

```
make_move_normalizer(board) -> callable(str) -> str | None
```

The returned callable, for one input string:

1. Not a `str` → return `None`.
2. Strip surrounding whitespace.
3. Strip trailing `!` and `?` characters, then strip whitespace again.
4. Empty after that → return `None`.
5. `board.parse_san(cleaned)`, catching `ValueError` — the documented base
   class of `InvalidMoveError`, `IllegalMoveError` and `AmbiguousMoveError`.
   On any of them, return `None`.
6. Result equal to `chess.Move.null()` → return `None`. (I5)
7. Return `move.uci()`.

The board is read, never pushed to. The closure holds the board for the
lifetime of one move only.

Nothing in this phase imports `llm.py` or touches the network.

## Phase 2 — thread it into llm.py

Add an optional `normalize=None` parameter to `_pick_move_from_messages` and to
`pick_move_with_llm`, passed through positionally-safe by keyword.

Replace the read at `llm.py:206-209`:

```
raw = (parsed.get("uci") or "")
uci = normalize(raw) if normalize is not None else raw.strip()
if uci is None or uci not in valid_ucis:
    raise ValueError(f"llm returned invalid uci: {raw!r}")
```

Three things this preserves on purpose:

- `normalize is None` keeps the exact current behavior, `.strip()` included.
  This is I11, and it is why no existing gate needs to change.
- The raised `ValueError` still carries the **raw** spelling, so
  `error_detail` still records what the model actually said.
- The exception type is unchanged, so `_content_outcome` still returns
  `invalid_uci` and `sessions_ops.py:327` still maps it to `llm_bad_response`.

`off_list` is computed from the normalized UCI against the candidate UCIs,
unchanged in meaning.

Do not add `normalize` to the two trailing underscore-prefixed test seams.
Place it before them in the signature.

Update `pick_move_with_llm`'s docstring with the callable contract: the
normalizer receives the raw `uci` JSON value and returns a canonical UCI or
`None`. State that `valid_ucis` is checked after normalization.

## Phase 3 — the call site

In `sessions_ops.py`, after `board` is built and `all_legal_ucis` is computed
(line 279), build the normalizer from the same board and pass it to
`pick_move_with_llm` by keyword alongside `valid_ucis`.

`board` is not pushed to until line 332, after the call returns, so the
normalizer sees the correct pre-move position throughout.

Nothing downstream of line 330 changes: `chosen_uci` is already canonical by
I10, so `chess.Move.from_uci` and `board.san` keep working as they do.

## Phase 4 — document the post-rollout rescue query

No schema change, and no new column. `llm_call_attempts.raw_content` is already
stored for every attempt of a committed move, and `llm_calls.chosen_uci` holds
the canonical result.

A rescue is an `ok` attempt whose parsed raw `uci`, after surrounding whitespace
only is removed, differs from `chosen_uci`. This counts SAN, annotation marks,
uppercase promotion pieces and alternate castling UCI. It does not count
whitespace alone, because the base code already accepts that with `.strip()`.

The query must:

1. Join `llm_calls` to `llm_call_attempts` by call id.
2. Restrict both rows to `outcome = 'ok'`.
3. Restrict `llm_calls.created_at` to a caller-supplied rollout timestamp or
   later.
4. Extract `raw_content::jsonb ->> 'uci'`; never compare the whole JSON string
   and never use substring containment.
5. Report accepted moves, rescued moves, and `rescued / accepted`, with a zero
   denominator returning `NULL` rather than division by zero.

Filtering the attempt row to `ok` is required. A committed call may contain
failed attempts before its winning attempt, and those retries are not rescues.
Comparing the JSON field is also required: the whole response can mention the
canonical UCI elsewhere, while `e2e4!` contains `e2e4` as a substring and is
still a real rescue.

Write the SQL into `plans/llm-token-optimization/12-notation-rescues.md`. Add
one line to `docs/architecture.md` at the `say_move` walkthrough noting that a
written move is normalized before validation.

Do not query production in Build. A meaningful count needs traffic created
after rollout, so the research file must say the query is for a later,
human-run, read-only measurement. A local empty result is not a rescue-rate
measurement and must not be presented as one.

Also record the honest limit: the existing logs only retain **successful**
committed moves, so historical failures that exhausted all three attempts are
not in the table and the true pre-change rate cannot be recovered from it.

## Verification

The gates, derived from the invariants above, are offline and need no provider:

| Invariant | What the gate asserts |
|---|---|
| I1, I11 | With `normalize=None`, a UCI answer and a bad answer behave exactly as at base |
| I2 | Each row of the Accepted table, on a board where that move is legal, returns the expected canonical UCI on attempt 1 |
| I2 | `"  exd5  "`, `"exd5!"`, `"exd5?!"` all normalize to the same UCI as `"exd5"` |
| I3 | A move that normalizes to a legal UCI absent from `valid_ucis` is still rejected |
| I4 | `""`, `"knight to f3"`, `"♘f3"`, `"N-KB3"`, `"NF3"`, `None`, `123` each return `None` and raise nothing |
| I5 | `"--"`, `"Z0"`, `"0000"`, `"@@@@"` each return `None` |
| I6 | `"nf3"` and `"NF3"` return `None` on a board where `Nf3` is legal |
| I7 | A rejected answer logs outcome `invalid_uci` and retries; attempt rows read `[(1,"invalid_uci"),(2,"ok")]` |
| I8 | `build_move_prompt` output is byte-identical to the strings pinned at base |
| I9 | The parsed AST for `llm.py` has no `import chess` or `from chess ...` node |
| I10 | After a SAN answer, `moves.uci` and `llm_calls.chosen_uci` hold the canonical UCI, the PGN holds the SAN the board derived, and the payload `uci` matches them |
| I13 | An ambiguous SAN on a board with two knights reaching one square returns `None` |

Then section 7 in full: `<test-full>`, `<typecheck>`, `<lint>`, `<build>`.
Backend typecheck and lint are vacuous, not passing — say so, do not report them
as green. Read the backend diff for the classes of error a type checker would
catch: the new `normalize` parameter's position in both signatures, the
`None`-vs-`str` return reaching the membership test, and the `ValueError`
catch not swallowing an exception the caller needs.

## Expected effect

Retries on notation mismatch should fall toward zero. Prompt tokens per move do
not move at all (I8); the saving is entirely attempts not taken. On the
baseline numbers that is the 14.6% of tokens that failed moves consumed, plus
three errors the player would have seen.

One thing this plan cannot promise: the research measured exactly one failure
class, on one model, on a prompt two generations old. Phase 4 exists because
the real rate on the shipped prompt is unknown. It prepares the query; it does
not claim data that cannot exist before rollout.

## Plan review

No open findings.

## Build review

<!-- role: Review the build | model: claude-opus-5 | base: f3980c5998ba3954d5af77c2cd3967a87c89070c | date: 2026-09-21 -->

Section 7 rerun at this commit: 254 passed and 11 skipped on the backend (the
11 skips are the live-provider tests in `test_machine_readable_move_prompt_live.py`),
35 of 35 gates in `tests/test_notation_tolerant_moves.py`, 17 frontend tests,
`npm run lint` clean, `npm run build` clean. Backend typecheck and lint are
vacuous, not passing. The SQL in
`plans/llm-token-optimization/12-notation-rescues.md` was run against a
throwaway Postgres with four seeded rows; it executes and its whitespace
handling matches `str.strip()`.

Carried forward from the previous build review. The
working tree already contains the fix each one asks for: `btrim` now trims the
full `str.strip()` character set (line 27), and `docs/architecture.md:225`
now reads `llm.py:282`. The human rejected all five findings on 2026-09-21: none is high, and none blocks
the ship. The stale `engine.py` pointers were still fixed in `docs/architecture.md`
at the human's direction, and that step names `make_move_normalizer` now.

- [rejected] med — plans/llm-token-optimization/12-notation-rescues.md:24 — `btrim(raw_uci)` removes ordinary spaces only, while the old validation path uses Python `str.strip()`, so a move wrapped only in tabs or newlines was already accepted but the query counts it as a rescue — trim the same whitespace set as `str.strip()` before comparing
- [rejected] low — docs/architecture.md:225 — the walkthrough still points to `llm.py:281`, but this build moved `pick_move_with_llm` to line 282, so the source pointer is stale — change the pointer to `llm.py:282`

New findings:

- [rejected] low — docs/architecture.md:219 — `make_move_normalizer` added 22 lines to the top of `engine.py`, so `evaluate` moved from line 67 to 89 and `rank_moves` from 88 to 110, but the walkthrough still points to `engine.py:88` here and `engine.py:67` at line 317 — change the two pointers to `engine.py:110` and `engine.py:89`
- [rejected] low — plans/llm-token-optimization/12-notation-rescues.md:21 — `llm_call_attempts.raw_content` is nullable, and a NULL makes `raw_uci` NULL, which `IS DISTINCT FROM chosen_uci` counts as a rescue; seeding one such row returned 4 accepted and 2 rescued when only 1 was a real rescue — add `AND a.raw_content IS NOT NULL` to the `accepted` CTE
- [rejected] low — backend/controller_operations/llm.py:205 — `raw` is rebound from the raw HTTP response object, read at lines 196 and 197 for `retries_taken` and `parse()`, to the model's uci string, so any later read of `raw` inside the same try block silently gets a string instead of the response — rename the new variable `raw_uci`

Checked and clean, recorded so the next reader does not redo it:

- I1 and I11 hold on the widest input I could construct: every legal UCI,
  including castling, en passant and promotion, round-trips through
  `board.parse_san` to the identical UCI, so no answer accepted at base is
  rejected or renamed now.
- I4 holds for non-`str` input, embedded NUL, a 5000-character string, a
  comma-separated multi-leg move, and bare annotation marks; none raise.
- The `find_move` default-to-queen path in `python-chess` does not leak a
  guessed promotion: `parse_san` rejects `a7a8` and `a8` with
  `IllegalMoveError`, so an unspecified promotion is retried, not invented.
- The board is read and never pushed to: its FEN is unchanged after every
  probe, and `say_move` pushes only at line 332, after the call returns.
- `make_move_normalizer` is wired into the only caller of
  `pick_move_with_llm`, and `build_move_prompt` is byte-identical to base.
