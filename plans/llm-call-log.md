<!-- role: Plan | model: claude-opus-5 | base: a2ac156 | date: 2026-09-13 -->
Status: frozen
Phases: 4

# A record of every LLM call, and the metrics that come out of it

Plan session, 2026-09-13. Draft. Nothing downstream may read this until a human
resolves the open decisions at the end and sets `Status: frozen`.

## Context

`say_move` is the only path that calls an LLM (`sessions_ops.py:291`, the sole
caller of `pick_move_with_llm`). It retries up to `LLM_MAX_RETRIES` — env, hard
capped at 3 — and today the entire record of what happened is four
`logger.info` lines in `llm.py`.

That record is unusable for counting, for three reasons:

- **It is discarded.** `error_logger/logger.py` writes to `backend/error_file/`,
  a container path with no volume in `docker-compose.yml`. Every restart throws
  it away. (The file handler is `ERROR` and up, so in the container the `[llm]`
  `INFO` lines only ever reached stdout anyway.)
- **It is free text.** "How often does the model return an illegal UCI" is a
  grep, and no line carries a session id or a ply, so no line can be tied to the
  game it belongs to.
- **It cannot be joined.** The game state is in Postgres; the log is one
  process's stdout.

Two more things are computed and thrown away. `off_list` — whether the chosen
UCI was legal but not on the engine's candidate list — is formatted into a log
line and lost. `intent` and `rationale` are returned by the model, broadcast to
the client (`sessions_ops.py:329`) and rendered in the chatbox, and stored
nowhere; `moves` keeps only `tone_summary`.

### What this does not do

**It does not reduce retries, and nothing here should be described as if it
does.** It makes the retry rate a number you can query, per failure class, per
model, per day. Whether the rate then falls is a matter of prompt and model
changes this plan does not make.

**It does not judge whether a move matched the player's sentiment.** A legal,
in-set UCI is accepted on the spot and costs zero retries. That third failure
class is invisible today and stays invisible: detecting it needs a judge that
does not exist. **The user scoped this work to the detectable classes on
2026-09-13** — bad JSON and an out-of-set UCI, the two the retry loop already
raises on. The judge question stays in `BACKLOG.md`, undecided.

### Decisions the user made on 2026-09-13

| Decision | Chosen |
|---|---|
| Scope | the detectable failure classes only; no sentiment judge |
| The player's message | **stored in full**, along with the FEN and the raw model reply |
| Retention | **kept indefinitely** — "treat it as data-warehouse data" |
| Readout | **SQL only** — a view plus documented queries. No script, no endpoint |

The retention answer is the one that shapes the schema, and this plan reads it
as *the log outlives the game*. See *The foreign key is deliberately absent*,
and the open decision at the end.

### Amendment, 2026-09-13

<!-- role: Write the gates | model: claude-opus-5 | base: a2ac156bbf2936ffcc238d3e1cd750700345859c | date: 2026-09-13 -->

Written into this frozen plan by the gates session at the user's explicit
direction, after the session stopped on accepted findings 3 and 4 for want of a
decision. `Status` is untouched and no finding's state was changed. What the
user settled, and what moved:

| Question | Settled | Where it lands |
|---|---|---|
| Label for an HTTP error from the provider | `transport`, with the status in its own column | classifier table, invariant 14 |
| Label for a reply with no usable content | new `bad_shape`, retried like `bad_json` | `outcome` CHECK, classifier table, invariant 15 |
| What the player sees on a provider HTTP error | `llm_unavailable`, 502 — not today's bare 500 | classifier table, invariant 14 |
| Filling in an unknown SDK retry count | new `llm_calls.sdk_max_retries`, read from `_client.max_retries` | schema, view, invariant 14 |
| Is the HTTP status queryable | yes — new `llm_call_attempts.status_code`, NULL when there was no response | schema, invariants 14 and 16 |

Phase 1 gains the two columns and the widened CHECK; phase 2 gains the two
classifier branches. No phase is added.

## The two counts that disagree today

There are two retry layers and they count different things. A record that says
"retries: 2" without saying which layer is lying.

- **The SDK retries transport failures.** `llm.py:28` constructs the client with
  `max_retries=3`, so the `openai` SDK retries 429s, connection errors and
  timeouts with its own backoff before raising. Up to four HTTP requests before
  the loop in `pick_move_with_llm` sees anything.
- **The loop retries content failures.** Bad JSON and an out-of-set UCI, up to
  `LLM_MAX_RETRIES` passes, each of which is itself a fresh SDK call that may
  have retried three times internally.

So one `say_move` can issue up to twelve HTTP requests and the current log says
"attempt 3/3".

**Both numbers are recoverable.** Read from the installed `openai` 1.109.1:
`_legacy_response.py:67` declares `retries_taken: int` on the response wrapper,
and `_base_client.py:963-1058` passes the loop counter into it on every return
path. `chat.completions.with_raw_response.create(...)` returns that wrapper
(`resources/chat/completions/completions.py:66`, wired through
`_legacy_response.to_raw_response_wrapper`), so the call becomes:

```python
raw = _client.chat.completions.with_raw_response.create(...)
sdk_retries = raw.retries_taken
resp = raw.parse()
```

`raw.parse()` returns the same `ChatCompletion` the plain `.create()` returns,
so nothing downstream of that line changes.

**On the transport path `retries_taken` is not available.** The SDK raises
rather than returning a wrapper, and the exception does not carry the count. That
attempt records `sdk_retries = NULL`, which means *unknown, and by construction
equal to `sdk_max_retries`* — do not write 3 there and do not write 0. The
distinction is the whole point of recording it, and `sdk_max_retries` is stored
per call so the fill-in at read time is the SDK's limit rather than the loop's.

## Schema

Two tables. One row per `pick_move_with_llm` call, N rows per attempt within it.
Splitting them is not normalisation for its own sake: latency, token counts, the
raw reply and the failure class are all per-attempt facts that differ between
the attempts of one call, and flattening them into `attempt_1_*` columns makes
every aggregate a `UNION`.

Appended to `backend/schema.sql`, idempotent like everything already in it:

```sql
CREATE TABLE IF NOT EXISTS llm_calls (
    id                BIGINT      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id        TEXT        NOT NULL,
    ply               INTEGER     NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    -- what was asked
    model             TEXT        NOT NULL,
    reasoning_effort  TEXT        NOT NULL,
    max_retries       INTEGER     NOT NULL,
    sdk_max_retries   INTEGER     NOT NULL,
    fen               TEXT        NOT NULL,
    player_text       TEXT        NOT NULL,
    candidate_ucis    TEXT[]      NOT NULL,
    prior_tone        TEXT,
    -- what happened
    outcome           TEXT        NOT NULL
                                  CHECK (outcome IN ('ok', 'exhausted', 'transport')),
    attempts          INTEGER     NOT NULL,
    latency_ms        INTEGER     NOT NULL,
    -- what came back, on success
    chosen_uci        TEXT,
    off_list          BOOLEAN,
    intent            TEXT,
    rationale         TEXT,
    tone_summary      TEXT
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_session  ON llm_calls(session_id, ply);
CREATE INDEX IF NOT EXISTS idx_llm_calls_created  ON llm_calls(created_at);
CREATE INDEX IF NOT EXISTS idx_llm_calls_outcome  ON llm_calls(outcome);

CREATE TABLE IF NOT EXISTS llm_call_attempts (
    call_id            BIGINT      NOT NULL
                                   REFERENCES llm_calls(id) ON DELETE CASCADE,
    attempt            INTEGER     NOT NULL,
    outcome            TEXT        NOT NULL
                                   CHECK (outcome IN ('ok', 'bad_json',
                                                      'missing_key', 'invalid_uci',
                                                      'bad_shape', 'transport')),
    latency_ms         INTEGER     NOT NULL,
    sdk_retries        INTEGER,
    status_code        INTEGER,
    raw_content        TEXT,
    error_detail       TEXT,
    prompt_tokens      INTEGER,
    completion_tokens  INTEGER,
    reasoning_tokens   INTEGER,
    PRIMARY KEY (call_id, attempt)
);
```

Column notes that are load-bearing:

- **`ply` is the ply this call was trying to produce**, i.e. `board.ply() + 1`
  measured before the push — equal to `moves.ply` on success, and on failure the
  ply that never happened. `NOT NULL` because it is always known: the board is
  read before the LLM is called. This is what makes `llm_calls JOIN moves USING
  (session_id, ply)` work.
- **`candidate_ucis` is `TEXT[]`, not the `(uci, san)` pairs.** The SAN is
  derivable from the FEN, which is stored; the array is what `off_list` is a
  claim about.
- **`chosen_uci`, `off_list`, `intent`, `rationale`, `tone_summary` are NULL
  unless `outcome = 'ok'`.** No CHECK enforcing that — it would fire on nothing
  and cost a constraint to read past.
- **`sdk_retries` is nullable and NULL means unknown.** See above. `0` means the
  SDK got its answer first try.
- **`sdk_max_retries` is the SDK's limit, not the loop's.** It is
  `_client.max_retries` — `3`, set at `llm.py:32` — and it is a separate column
  from `max_retries` because the two limits govern different layers and are not
  the same number. The view fills an unknown transport count in from this one;
  filling it in from `max_retries` undercounts every call whenever
  `LLM_MAX_RETRIES` is 1 or 2, which is the whole of accepted finding 4. Read it
  off the client rather than writing the literal `3`, so the column stays true
  if the client is ever constructed differently.
- **`status_code` is the HTTP status of a failed response, and NULL when there
  was none.** `APIStatusError.status_code` (`_exceptions.py:90`) carries it, so a
  429 or a 503 is a number in a column rather than a substring of
  `error_detail`. NULL on a dropped connection or a timeout — no response, no
  status — and NULL on any attempt whose HTTP call succeeded, including a
  `bad_json` or a `bad_shape` reply that arrived with a perfectly good 200. "How
  often is the provider rate-limiting us" is then a `GROUP BY`, not a `LIKE`.
- **The token columns are nullable and NULL means the provider returned no
  usage.** `ChatCompletion.usage` is `Optional` (`types/chat/chat_completion.py:88`),
  and `reasoning_tokens` sits under `usage.completion_tokens_details`
  (`types/completion_usage.py:20`), which is itself optional. Never write `0`
  for a missing count — an average over zeros is a silently wrong number, and
  reasoning tokens bill as output.
- **`raw_content` and `error_detail` are for failed attempts.** `raw_content` is
  the exact `message.content` string; on the successful attempt it is redundant
  with the stored fields, and storing it anyway costs a duplicate of every reply.
  Write it only when the attempt failed.

### The foreign key is deliberately absent

`session_id` is a plain `TEXT` column with no `REFERENCES sessions(id)`, which is
the opposite of what `moves` and `session_connections` do.

The reason is the user's retention decision: this is warehouse data, kept for
analysis over a long window, and an FK with `ON DELETE CASCADE` means the record
of every call a game ever made dies with the game. Nothing deletes sessions today
— the presence work removed the only path — but the whole point of the choice is
that the log must not depend on that staying true.

Two consequences, both accepted rather than overlooked:

- **Nothing enforces that `session_id` names a real game.** A typo in a query
  produces zero joined rows rather than an error at insert time.
- **`INSERT` takes no `FOR KEY SHARE` on the `sessions` row.** Incidentally
  useful — it removes any interaction with the `FOR NO KEY UPDATE` lock the move
  paths hold, which `plans/presence-table.md` spent a phase on. The write is
  outside that transaction anyway (below), so this is a second reason and not the
  first.

**This reading of "forever" is an inference and is listed as an open decision.**
If the user meant cascade-and-never-delete instead, the column gains a
`REFERENCES sessions(id) ON DELETE CASCADE` and nothing else in this plan moves.

## Where the row is written

Three constraints decide this, and together they leave one answer.

**1. The write must not happen inside the move transaction.** `say_move` holds
`SELECT ... FOR NO KEY UPDATE` on the session row across the whole Groq
round-trip. An `INSERT` on that cursor extends the hold, and — decisively — a
failed call raises `ApiError`, the transaction rolls back, and the log rolls back
with it. **Logging the failures is most of the point**, so a write that
disappears on failure is not a design, it is the bug.

**2. The write must not open a second connection while the first is open.**
`tests/test_connection_isolation.py:170` asserts
`_backend_count(pgdb) == baseline + CONCURRENT_OPS` **while the LLM is parked** —
an exact count of Postgres backends, N concurrent `say_move`s, one connection
each. A connection opened during the call makes that `baseline + 2N` and breaks
a passing gate that has nothing to do with this feature. That gate is right and
must not be edited (`AGENTS.md` §2).

**3. It must be written exactly once, on every path out of `say_move`** —
success, `llm_bad_response`, `llm_unavailable`, and an exception from the
persistence of the move itself.

So: **accumulate in memory during the call, write after the transaction has
closed, on a fresh connection, in a `finally`.**

```python
call_log = CallLog(sid=sid, ...)          # before the `with db()` block
try:
    with db() as pg, pg.transaction(), pg.cursor() as cur:
        ...                                # unchanged; pick_move_with_llm(log=call_log)
finally:
    _persist_call_log(db, call_log)        # own connection, opened after the first closed
```

`db` is the zero-argument factory the operation already takes, so this needs no
new wiring and the fast tier's `op_db` fake keeps working unchanged.

### The record is an out-parameter, not a return value

`pick_move_with_llm` gains one keyword argument, `log=None`, a `CallLog` the
caller owns and the function appends attempt records to. It does **not** return
the record, and its `(uci, tone_summary, intent, rationale)` return is unchanged.

Two reasons, and the first is not a style preference:

- **A return value is lost when the function raises**, and it raises on exactly
  the two paths worth logging — `raise last_err` after exhaustion, and
  `raise TimeoutError` on transport. A mutable object the caller already holds
  survives the raise.
- **Five test files monkeypatch `pick_move_with_llm` with a fake returning a
  4-tuple** (`conftest.py:171`, `test_move_gating.py:83`,
  `test_say_move_indicator.py:32`, `test_connection_isolation.py:50,154`,
  `test_fk_check_passes_a_locked_session.py:49`). An unchanged return signature
  and a keyword the fakes ignore leaves every one of them working. A fake that
  fills nothing produces a `CallLog` with no attempts, which writes no row — see
  invariant 8's note.

`CallLog` lives in `llm.py` and knows nothing about a database: a small class
holding the header fields, a list of attempt dicts, and a monotonic start time.
`llm.py` keeps importing only `logger`.

### Persistence must never fail a move

`_persist_call_log` catches `Exception`, logs it, and returns. A full disk, a
missing table, a bad column name — none of them may turn a completed move into a
500, and none of them may replace the `ApiError` already travelling up the stack
with a different one raised from a `finally`. This is invariant 7 and it has a
gate.

## Classifying a failed attempt

The loop's `except` clause is `(ValueError, json.JSONDecodeError, KeyError)`, and
**`json.JSONDecodeError` is a subclass of `ValueError`**. Any classifier that
tests `isinstance(e, ValueError)` first labels every parse failure `invalid_uci`
and the whole table becomes a lie. Test the subclass first:

| Condition | `outcome` |
|---|---|
| `isinstance(e, json.JSONDecodeError)` | `bad_json` |
| `isinstance(e, KeyError)` | `missing_key` |
| `isinstance(e, ValueError)` | `invalid_uci` |
| the reply carries no usable content | `bad_shape` |
| `APIConnectionError`, `APITimeoutError` | `transport` |
| any `APIStatusError` — `RateLimitError`, 408, 409, 5xx, anything else | `transport` |
| no exception | `ok` |

`bad_shape` and the `APIStatusError` row are accepted finding 3, and they are
not symmetric:

- **`bad_shape` is a content failure and the loop retries it**, exactly like
  `bad_json`. The HTTP call succeeded; what came back is unusable — `choices` is
  empty, or `message.content` is `None`, which is not a string and so reaches
  `json.loads` as a `TypeError` today and escapes the `except` clause entirely.
  Catch it, record the attempt, retry. Exhausting on it is `exhausted` and
  `llm_bad_response`, 502, like any other content failure.
- **An `APIStatusError` is a transport failure and the loop does not retry it.**
  The SDK has already retried it up to `sdk_max_retries` times with backoff, so a
  loop retry multiplies requests against a provider that is already failing or
  rate-limiting. Record the attempt with its `status_code`, then raise
  `TimeoutError` the way the two connection errors already do: call outcome
  `transport`, caller sees `llm_unavailable`, 502. `RateLimitError` moves under
  this row rather than keeping its own — it is an `APIStatusError` subclass
  (`_exceptions.py:80`) and it already behaved this way; what it gains is a
  `status_code` of 429.

This is the one place the feature changes what the player sees: a Groq 500 is an
unmapped exception and a bare HTTP 500 today, and becomes `llm_unavailable`, 502
— the response the frontend already knows how to display, and the one the other
provider failures already produce.

Two honest notes about that table:

- **`missing_key` is close to unreachable today.** `llm.py:112-115` reads every
  field with `parsed.get(...) or ""`, so a missing key yields an empty string and
  the empty UCI then fails the `valid_ucis` test as `invalid_uci`. The `KeyError`
  in the `except` clause catches a case the code no longer produces. Keep the
  classification anyway — it costs one branch and it stops being a lie the moment
  someone writes `parsed["uci"]` — but do not expect the count to be non-zero,
  and do not "fix" `.get()` to make it so. That is a behaviour change and out of
  scope.
- **`invalid_uci` therefore covers two different things**: a syntactically fine
  UCI that is not legal in this position, and an empty or absent one. They are
  distinguishable — `raw_content` is stored — and splitting the class is a
  judgement call this plan leaves to whoever reads the first month of data.

## The metrics

SQL only, per the user's decision. One view in `schema.sql`, defined so the
counts the backlog complained about are both visible and never confused:

```sql
CREATE OR REPLACE VIEW llm_call_metrics AS
SELECT
    date_trunc('day', c.created_at)                          AS day,
    c.model,
    count(*)                                                 AS calls,
    count(*) FILTER (WHERE c.outcome = 'ok')                 AS ok,
    count(*) FILTER (WHERE c.outcome = 'exhausted')          AS exhausted,
    count(*) FILTER (WHERE c.outcome = 'transport')          AS transport,
    count(*) FILTER (WHERE c.attempts > 1)                   AS needed_a_retry,
    sum(c.attempts)                                          AS loop_attempts,
    sum(a.http_requests)                                     AS http_requests,
    count(*) FILTER (WHERE c.off_list)                       AS off_list,
    percentile_cont(0.5) WITHIN GROUP (ORDER BY c.latency_ms) AS p50_ms,
    percentile_cont(0.95) WITHIN GROUP (ORDER BY c.latency_ms) AS p95_ms,
    sum(a.prompt_tokens)                                     AS prompt_tokens,
    sum(a.completion_tokens)                                 AS completion_tokens,
    sum(a.reasoning_tokens)                                  AS reasoning_tokens
FROM llm_calls c
JOIN LATERAL (
    SELECT sum(1 + COALESCE(t.sdk_retries, c.sdk_max_retries)) AS http_requests,
           sum(t.prompt_tokens)     AS prompt_tokens,
           sum(t.completion_tokens) AS completion_tokens,
           sum(t.reasoning_tokens)  AS reasoning_tokens
    FROM llm_call_attempts t WHERE t.call_id = c.id
) a ON TRUE
GROUP BY 1, 2;
```

`loop_attempts` and `http_requests` are both present and named for the layer they
describe. `COALESCE(t.sdk_retries, c.sdk_max_retries)` is where the unknown transport
count is filled in, at read time, with the only value it can be — and because it
is `COALESCE` and not a stored `3`, the row still says "unknown" to anyone
looking at it directly.

`docs/architecture.md` gets the failure-class breakdown query alongside it — one
`GROUP BY outcome` over `llm_call_attempts` — rather than a second view for a
one-line query.

## Backend code

**New — `backend/queries/llm_calls.py`**, matching the `queries/` idiom exactly
(plain functions taking a cursor, no connection handling, docstrings that argue
the non-obvious choice):

- `insert_call(cur, record) -> int` — one `INSERT ... RETURNING id`.
- `insert_attempts(cur, call_id, attempts)` — one `executemany`, or a single
  multi-row `INSERT`. Not one statement per attempt: at most three rows, one
  round trip.

**`backend/controller_operations/llm.py`:**

- `CallLog` — the in-memory record. No database, no import beyond what is there.
- `pick_move_with_llm(..., log=None)` — every `log` write guarded by
  `if log is not None`, so the signature stays usable without one.
- `.create(...)` becomes `.with_raw_response.create(...)` plus `.parse()`, for
  `retries_taken`.
- `time.monotonic()` around each attempt for `latency_ms`. `time` is already
  imported.
- The four `logger.info` lines **stay**. They are how a developer watches a move
  happen in the terminal, the table is not, and deleting them is a drive-by
  change (`AGENTS.md` §8).

**`backend/controller_operations/sessions_ops.py`** — `say_move` only. Build the
`CallLog` before the `with db()` block, pass it, persist it in a `finally` around
that block. `_persist_call_log` is a module-level helper in this file, not in
`llm.py`: `llm.py` has no database dependency and must not gain one.

**`make_move` is untouched.** It calls no LLM and writes no row. Invariant 8.

**No new configuration.** `LLM_MODEL`, `LLM_REASONING_EFFORT` and
`LLM_MAX_RETRIES` are read where they already are and copied into the row.

**Nothing runs in the background** — no thread, no timer, no batching. One insert
pair per move, after the transaction, on the request's own thread. The repo's
standing rule, and it costs a round trip to a local database on a path that just
spent up to 30 seconds on a network call.

## Invariants

Gates derive from these, not from the implementation.

1. A `say_move` whose first attempt succeeds writes **exactly one** `llm_calls`
   row and **exactly one** `llm_call_attempts` row. The call row has
   `outcome = 'ok'`, `attempts = 1`; the attempt row has `attempt = 1`,
   `outcome = 'ok'`. The call row's `session_id` and `ply` equal those of the
   `moves` row the same request wrote.
2. A call whose first attempt returns an out-of-set UCI and whose second
   succeeds writes one call row with `attempts = 2, outcome = 'ok'`, and two
   attempt rows: `(1, 'invalid_uci')` and `(2, 'ok')`. The first attempt's
   `raw_content` is byte-identical to the string the model returned; the
   second's `raw_content` is NULL.
3. A call that exhausts `LLM_MAX_RETRIES` writes one call row with
   `outcome = 'exhausted'` and `attempts = LLM_MAX_RETRIES`, one attempt row per
   attempt, **and no `moves` row and no change to `sessions.fen`**. The caller
   still receives `llm_bad_response`, 502 — unchanged from today.
4. A transport failure writes one call row with `outcome = 'transport'` and one
   attempt row whose `sdk_retries IS NULL`. The caller still receives
   `llm_unavailable`, 502.
5. `json.JSONDecodeError` is recorded as `bad_json` and never as `invalid_uci`,
   despite being a `ValueError` subclass.
6. The row survives the rollback: a `say_move` whose move write fails after a
   successful LLM call still leaves its `llm_calls` row, with `outcome = 'ok'`,
   while the board is unchanged.
7. A failing log write never fails a move. With the `llm_calls` insert forced to
   raise, `say_move` returns 200, the `moves` row is present, and the board
   advanced.
8. Logging opens no connection while the move transaction is open. With N
   concurrent `say_move`s parked inside the LLM call, the backend count in
   `pg_stat_activity` is `baseline + N` — the number
   `test_connection_isolation.py` already asserts, unchanged by this work.
9. `make_move` writes zero `llm_calls` rows.
10. `off_list` is recorded and correct: a chosen UCI that is legal but absent
    from `candidate_ucis` records `off_list = true`; one present records `false`.
11. Token counts are NULL, never `0`, when the response carries no `usage`. Every
    attempt row has `latency_ms >= 0`, and the call row's `latency_ms` is at
    least the sum of its attempts'.
12. Nothing runs in the background: no thread and no timer is created by this
    feature, and `pg_stat_activity` shows no connection belonging to it between
    requests.
13. The fast tier still runs with no database. No test file imports
    `queries.llm_calls`.
14. An HTTP error from the provider is recorded, not lost. A call whose request
    raises `APIStatusError` — 429, 408, 409, 500, any status — writes one call
    row with `outcome = 'transport'` and one attempt row with
    `outcome = 'transport'`, `status_code` equal to that status, and
    `sdk_retries IS NULL`. The loop does not retry it, so `attempts = 1`, and
    the caller receives `llm_unavailable`, 502 — where today it receives an
    unmapped 500 and no row at all.
15. A reply that arrives with no usable content is recorded as `bad_shape` and
    retried like any other content failure: `message.content` of `None`, or an
    empty `choices`, writes an attempt row with `outcome = 'bad_shape'` and
    `status_code IS NULL`, and a following good attempt still produces
    `outcome = 'ok'`. `raw_content` is NULL there and only there — it is the one
    case where no content came back to store.
16. `status_code` is NULL on every attempt whose HTTP call succeeded, including
    `ok`, `bad_json` and `bad_shape`. A status is recorded only where there was
    a failed HTTP response to read one from.

## Tests that must change

- **`tests/conftest.py` — `FakeCursor` needs a sixth query shape.** It answers by
  SQL text, and `INSERT INTO llm_calls ... RETURNING id` will fall through to
  `self._result = None`, so `insert_call` reads `None["id"]` and every fast-tier
  `say_move` test raises `TypeError`. This is exactly deviation 2 of the presence
  build repeating itself. Match `INSERT INTO llm_calls` and answer
  `{"id": 1}`. Add `executemany` to `FakeCursor` if `insert_attempts` uses it —
  it has only `execute` today.
- **`tests/test_connection_isolation.py` — re-run, do not edit.** Invariant 8 is
  its existing assertion. If the count moves, this feature opened a connection
  where it must not.
- **`tests/test_say_move_indicator.py`, `test_move_gating.py`,
  `test_fk_check_passes_a_locked_session.py`** — should need no change; their
  fakes ignore the new keyword. Confirm rather than assume.

## Verification

Per `AGENTS.md` §7: gates observed failing for missing behaviour first, then
`<test-full>`, `<lint>`, `<build>` green. `<typecheck>` has no command and
`<lint>` is frontend-only, so **neither covers the backend — vacuous, not
passing**, and this is a backend-only change. In their place, read the diff for:

- the new `log=` keyword at the one real call site and at all five monkeypatched
  fakes;
- `insert_call` returning `None` where an `int` is expected, which is what a
  missed `FakeCursor` shape produces;
- `usage`, `completion_tokens_details` and `message.content` each being
  `Optional` — three separate `None`s reaching arithmetic or a column;
- an exception raised inside the `finally` replacing the `ApiError` in flight.

Two claims are about libraries rather than this code and **must be demonstrated
before anything depends on them**:

1. **`with_raw_response.create(...).parse()` returns what `.create(...)`
   returned, and `.retries_taken` is populated.** Read from the installed source
   above; run it against Groq once and print both.
2. **`GENERATED ALWAYS AS IDENTITY ... RETURNING id` works on the Postgres 16
   this repo runs.** One `psql` round trip.

By hand:

1. Play a normal tone move. Confirm one call row, one attempt row, `off_list`
   correct against the candidate array, tokens non-NULL, and
   `llm_calls JOIN moves USING (session_id, ply)` returning the move.
2. Force a retry — a monkeypatched client returning a legal-looking but illegal
   UCI once, then a good reply. Confirm `attempts = 2` and that the raw first
   reply is readable in the table.
3. Force exhaustion. Confirm the row exists, `outcome = 'exhausted'`, the board
   is unchanged, and the client still sees `llm_bad_response`.
4. Force transport failure — point `GROQ_BASE_URL` at a black-hole port, the
   technique the presence session used. Confirm `outcome = 'transport'` and
   `sdk_retries IS NULL`.
5. Restart the backend and re-run `SELECT * FROM llm_call_metrics` — the numbers
   from steps 1-4 are still there. This is the thing the current log cannot do.

## Out of scope — goes to `BACKLOG.md`

- **The sentiment judge.** The third failure class stays undetected. Scoped out
  by the user on 2026-09-13; the existing backlog entry stands.
- **`LLM_MAX_RETRIES`' hard cap of 3.** The backlog asks whether it should stay
  once the cost of a retry is measurable. It is not measurable until this ships,
  so the cap does not move here.
- **`error_file/` has no volume.** Unchanged. This work routes the countable
  facts to Postgres and leaves the text log where it is.
- **`.get()` masking `KeyError`.** Named above; not changed.
- **`game_over` cannot construct its error.** Still true, still a backlog entry,
  still untouched.
- **`moves` has six live columns `schema.sql` does not declare.** Noted in
  `plans/presence-table.md`; this plan adds no columns to `moves` and does not
  reconcile that drift.

## Docs to update before the session ends

- `docs/architecture.md` — the data model (`:90`) gains two tables and a view;
  the move pipeline's retry step (`:198`) gains what is now recorded and the
  two-layer count; a short metrics section with the documented queries; the
  configuration list (`:329`) is unchanged and should be confirmed so.
- `README.md` — the schema change reaches an existing database only through
  `psql "$DATABASE_URL" -f backend/schema.sql`. The section exists; confirm it
  covers a view.
- `BACKLOG.md` — the "Next — log every LLM call" entry closes; the sentiment
  judge and the retry cap stay open under their own headings.
- `handoff.md` — rewritten whole.

## Phases

Build one at a time; stop at each boundary.

1. **Schema and queries.** The two tables, the three indexes, the view, and
   `queries/llm_calls.py`. Nothing calls them. Verifiable on its own: apply the
   file twice and insert a row by hand.
2. **The record in `llm.py`.** `CallLog`, the `log=` keyword, the classifier,
   `with_raw_response` and `retries_taken`, latency and token capture. No
   persistence yet — the record is built and dropped.
3. **Persistence in `say_move`.** The `finally`, the second connection, the
   `FakeCursor` shape. This is the phase invariants 1-11 land in.
4. **Docs and backlog.**

## Open decisions — a human must settle these before `Status: frozen`

1. **The absent foreign key.** "Keep it forever, treat it as data-warehouse data"
   is read here as *the log outlives the game*, so `session_id` carries no
   `REFERENCES`. If the intent was instead cascade-and-never-delete, say so and
   the column gains `REFERENCES sessions(id) ON DELETE CASCADE`. Nothing else in
   this plan changes either way.
2. **Whether `raw_content` is stored on successful attempts too.** This plan says
   no — it duplicates fields already stored and doubles the table's text volume.
   The argument for yes is that the exact bytes of a *successful* reply are what
   you would want when the sentiment-judge question is eventually answered
   offline, and by then the replies are gone. A product call about future work,
   not an implementation detail.

## Amendment, 2026-09-13 — the unrecognised ending

<!-- role: Write the gates | model: claude-opus-5 | base: a2ac156bbf2936ffcc238d3e1cd750700345859c | date: 2026-09-13 -->

Written into this frozen plan by the gates session at the user's explicit
direction, following the precedent of the amendment above. `Status` is
untouched and no finding's state was changed; the open Build review findings
this answers stay `[open]` for a human to mark.

The classifier records an attempt only from clauses that name an exception type,
so an ending it does not name is unrecorded rather than merely unclassified. The
reply that reaches it is valid JSON that is not an object — `"e2e4"`, `[]`,
`null`, `123` — where `json.loads` returns a str, list, None or int and
`parsed.get` raises `AttributeError`, which no clause in `llm.py` and neither
clause in `say_move` catches. Today that abandons the loop on attempt 1 with no
row, no retry, and an unmapped 500.

What the user settled:

| Question | Settled |
|---|---|
| How the ending is caught | a last-clause `except Exception` around the attempt, after the named clauses |
| Attempt label | new `unexpected` — widens the `llm_call_attempts` CHECK |
| Is it retried | yes, like any content failure |
| What goes in `raw_content` | nothing. NULL, always. The reply is not known to be safe to keep |
| Where the detail goes | `error_detail`, as `type(e).__name__: e` |
| Call label | new `unexpected` — widens the `llm_calls` CHECK |
| When the call carries it | from construction, until a real outcome replaces it |
| What the player sees | unchanged: exhausting on it is `exhausted` and `llm_bad_response`, 502 |

The call-level value is the half that is easy to skip. `llm_calls.outcome` is
`NOT NULL` and both inserts share one transaction, so a call whose outcome was
never set does not merely lose its own row — it rolls back the attempt rows that
were classified correctly on the way there. `unexpected` as the constructed
default makes the record insertable whatever happens next, which is the point.

Two invariants follow, numbered on from 16:

17. An attempt that ends in a way the classifier does not name is recorded, not
    lost: it writes an attempt row with `outcome = 'unexpected'`,
    `raw_content IS NULL`, a non-empty `error_detail`, and
    `status_code IS NULL`, and the loop retries it. A following good attempt
    still produces `outcome = 'ok'`. Exhausting on it is `outcome = 'exhausted'`
    and `llm_bad_response`, 502, with no `moves` row and no change to
    `sessions.fen`.
18. An unrecognised ending never discards the history before it. A call whose
    attempt 1 was classified `bad_json` and whose attempt 2 ended unrecognised
    keeps both rows, and a call carries `outcome = 'unexpected'` from
    construction so its row is insertable before anything finishes it.

Phase 1 gains the two widened CHECKs; phase 2 gains the catch-all clause and the
constructed default. No phase is added.

## Amendment, 2026-09-13 — the fourth outcome is not counted

<!-- role: Review the build | model: claude-opus-5 | base: a2ac156bbf2936ffcc238d3e1cd750700345859c | date: 2026-09-13 -->

Written into this frozen plan by the build-review session at the user's explicit
direction, following the precedent of the two amendments above. `Status` is
untouched and no finding's state was changed; the Build review finding this
answers stays `[open]` for a human to mark.

The amendment above gave `llm_calls.outcome` a fourth value and did not give the
view a column for it. `llm_call_metrics` filters `ok`, `exhausted` and
`transport` only, so a call that ends `unexpected` is counted in `calls` and in
no class column: the classes stop summing to the calls, and the one outcome that
by definition names an ending nobody predicted is the one the readout cannot
show. Confirmed against the throwaway Postgres — one `unexpected` call row reads
back as `calls = 1, ok = 0, exhausted = 0, transport = 0`.

What the user settled:

| Question | Settled |
|---|---|
| Is `unexpected` a counted class | yes — its own column in `llm_call_metrics` |
| Column name and position | `unexpected`, immediately after `transport`, so the four classes read in order |
| Does anything else in the view move | no. `calls`, the attempt aggregates and the percentiles are unchanged |
| Does the documented query move | no. `docs/architecture.md` selects `*`; the prose at `:135` names classes generically and stays true |

The view gains one line, between `transport` and `needed_a_retry`:

```sql
    count(*) FILTER (WHERE c.outcome = 'unexpected')          AS unexpected,
```

`CREATE OR REPLACE VIEW` already re-runs idempotently, so an existing database
picks the column up from `psql "$DATABASE_URL" -f backend/schema.sql` with no
migration. Note that `CREATE OR REPLACE VIEW` may only add columns at the end of
the select list; on a database where the view already exists this one must be
`DROP VIEW IF EXISTS llm_call_metrics;` followed by the `CREATE`, or the replace
fails with *cannot change name of view column*. Keep both statements in
`schema.sql` so re-running stays safe.

One invariant follows, numbered on from 18:

19. Every call is counted in exactly one class. For any `(day, model)` row of
    `llm_call_metrics`, `ok + exhausted + transport + unexpected = calls`, and a
    call whose `outcome` is `unexpected` increments the `unexpected` column and
    no other. The identity holds over a mixed set containing at least one call
    of each of the four outcomes.

Phase 1 gains the view column and the `DROP VIEW` that lets it replace. No other
phase moves and no phase is added.


## Plan review

- [accepted] high — plans/llm-call-log.md:238 — `CallLog` cannot be fully constructed before the database block because FEN, ply, candidates, and prior tone are discovered inside it, and starting its timer there would include lock acquisition and validation — initialize `call_log = None` before the outer `try`, then construct and start it immediately before `pick_move_with_llm`
- [accepted] high — plans/llm-call-log.md:275 — the logging connection is autocommit and the plan does not require one transaction around the call and attempt inserts, so an attempt-insert failure can leave a committed partial call row — wrap both inserts in one explicit transaction and gate that a forced attempt-insert failure leaves neither row
- [accepted] high — plans/llm-call-log.md:293 — the classifier omits retryable SDK failures including HTTP 408, 409, and 5xx responses, and malformed response shapes can escape without an attempt row, so the plan does not record every LLM call — define outcomes and mappings for every SDK and response-shape failure in scope and add a gate for each class
- [accepted] med — plans/llm-call-log.md:336 — `c.max_retries` is the content-loop limit rather than the SDK retry limit, so `http_requests` is undercounted whenever `LLM_MAX_RETRIES` is 1 or 2 — record the SDK retry limit separately or recover the final retry count from the exception request
- [accepted] med — plans/llm-call-log.md:54 — the decisions table says the raw model reply is stored while lines 181–184 omit successful replies and line 545 reopens the decision, so the data contract is contradictory — store readable `raw_content TEXT` for every attempt that returned content, including successful attempts; use NULL only when no content was returned
- [accepted] med — plans/llm-call-log.md:230 — “exactly once on every path” contradicts invariant 7, which deliberately permits a logging failure to leave no row, so the completeness guarantee cannot hold — qualify persistence as best-effort under database failure and state how incomplete metrics are surfaced
- [accepted] low — plans/llm-call-log.md:7 — forbidding every downstream read until `frozen` conflicts with the required workflow because Grade the plan must read a draft — exempt the grading session and state the order as draft → reviewed → human-resolved frozen
- [accepted] low — plans/llm-call-log.md:540 — the retention decision required a choice about whether logs outlive deleted games — keep `llm_calls.session_id` without a foreign key so the warehouse record outlives its game

## Build review

<!-- role: Review the build | model: claude-opus-5 | base: a2ac156bbf2936ffcc238d3e1cd750700345859c | date: 2026-09-13 -->

Replaces the section whole, per the role. Five of the eight findings are carried
forward unchanged in substance from the previous build review, with three stale
file anchors corrected (`llm.py:246` → `:268`, `plans/llm-call-log.md:325` →
`:470`, `:588` → `:550`); two are new, from the reopened phase-1 diff.

**All eight are marked `[accepted]` on the user's explicit instruction, given in
this session on 2026-09-13 after the constraint was stated twice.** §4 and §11
reserve a finding's state for a human; the human set it, this session typed it,
and the finding bodies below are otherwise byte-identical to what the review
wrote. Accepting them records that each is a real defect, not that each is
fixed — findings 1 and 7 are satisfied in the tree, and 2, 3, 4, 5, 8 and the
stale *Open decisions* section still name work nobody has done.

The first finding — `llm_call_metrics` not counting `unexpected` — is kept
verbatim below, but it is no longer true of the tree: the reopened phase-1 build
added the column, and the identity now holds.
Verified on the throwaway Postgres against a database carrying the
pre-amendment three-value CHECK and three-class view: `schema.sql` applied twice
in a row, the pre-existing call row survived, an `outcome = 'unexpected'` insert
was accepted, and `llm_call_metrics` read back `calls = 2, ok = 1, exhausted = 0,
transport = 0, unexpected = 1`.

The seventh finding's live-Groq demonstration has since been run, from
`backend/.env`, so its "that run still has not happened" clause is stale and the
body is kept verbatim only so the review reads as written. `openai` 1.109.1 against
`qwen/qwen3.6-27b`: `chat.completions.with_raw_response.create(...)` returned an
`openai._legacy_response.LegacyAPIResponse` with HTTP 200, `retries_taken` was
present and populated as `0` (`int`), `.parse()` returned an
`openai.types.chat.chat_completion.ChatCompletion` of the same type a plain
`.create()` returns, and `json.loads(message.content)` parsed. Then
`pick_move_with_llm` end to end with a real `CallLog` and a real 15-move
candidate list: `outcome = 'ok'`, `attempts = 1`, `latency_ms = 630`,
`chosen_uci = 'e2e4'`, `off_list = False`, and one attempt row with
`sdk_retries = 0`, `status_code = None`, `error_detail = None`,
`prompt_tokens = 555`, `completion_tokens = 99`, `reasoning_tokens = None`,
`raw_content` 440 characters. Two things the run settles that the fakes could
not: `retries_taken` is populated on a real success rather than only on the
hand-written wrapper, and Groq returns `usage.completion_tokens_details = None`
under `reasoning_effort=none`, so `reasoning_tokens` is NULL on the ordinary
path and the plan's refusal to write `0` there is load-bearing rather than
theoretical.

- [accepted] med — backend/schema.sql:131 — `llm_call_metrics` filters `ok`, `exhausted` and `transport` but not `unexpected`, which the 2026-09-13 amendment made a real terminal value of `llm_calls.outcome` — the CHECK accepts it, `CallLog` carries it from construction, and `tests/test_llm_call_log_fast_tier.py:148` gates on it reaching the record — so a call that ends in an unrecognised way is counted in `calls` and in no class column and the day's classes no longer sum to its calls; demonstrated against the throwaway Postgres by inserting one `outcome = 'unexpected'` call row, which reads back as `calls = 1, ok = 0, exhausted = 0, transport = 0` — add `count(*) FILTER (WHERE c.outcome = 'unexpected') AS unexpected` to the view, which also means amending the frozen plan's metrics section, a human's edit
- [accepted] low — backend/schema.sql:140 — the view's class columns are a hand-written list of outcome literals and nothing ties them to the CHECK that defines the set, so the drift just repaired can recur silently: a sixth `llm_calls.outcome` value widens the CHECK, is accepted at insert, and lands in `calls` with no class column, and no gate notices — `tests/test_llm_call_schema.py`'s `CALL_OUTCOMES` is a third hand-written copy of the same list, so it drifts with the view rather than against it — read the admitted values from `pg_get_constraintdef(oid)` for `llm_calls_outcome_check` and assert the view exposes one column per value and that they sum to `calls`, which turns invariant 19 into a structural gate instead of a four-element enumeration
- [accepted] low — README.md:92 — the re-run note says the `llm_call_metrics` view is "replaced idempotently" alongside the functions and triggers, but the file does `DROP VIEW IF EXISTS` then `CREATE VIEW`, which is not a replace: between the two statements the view does not exist, so a concurrent analytics query against a live database errors with *relation "llm_call_metrics" does not exist* rather than blocking, and any grant made on the view is discarded with it — the mechanism is what the frozen plan's third amendment directs, so the code is right and the sentence is not; say that the view is dropped and recreated, and that the readout is briefly unavailable during a schema apply
- [accepted] low — backend/controller_operations/llm.py:50 — `CallLog.__init__` writes `_client.max_retries` onto the shared module-level client when the attribute is absent, so constructing a record mutates global state on every tone move, and the branch is dead against the real SDK (`OpenAI` always carries `max_retries`); it exists only to satisfy a gate whose fake client does not declare the attribute — read it as `getattr(_client, "max_retries", SDK_MAX_RETRIES)`, which requires that fake to declare `max_retries` and so is a human's call to make
- [accepted] low — backend/controller_operations/llm.py:268 — the retry tail duplicates the content-failure clause verbatim in the catch-all clause: the same `attempt + 1 < LLM_MAX_RETRIES` guard, the same `on_retry` call wrapped in the same `except Exception`, the same `time.sleep(LLM_BACKOFF_BASE * (2 ** attempt))`, so the backoff policy now lives in two places and a change to one drifts from the other — lift the tail into one helper called from both clauses, or let both clauses fall through to a single tail after the `try`
- [accepted] low — plans/llm-call-log.md:470 — invariant 2 requires the successful attempt's `raw_content` to be NULL and invariant 15 says NULL applies to `bad_shape` "and only there", while the build stores content on every attempt that returned any and leaves NULL on `bad_shape`, `transport` and `unexpected` alike, following the accepted plan-review finding on `raw_content` instead; the frozen invariants and the green gates disagree in writing — restate invariants 2 and 15 to match the accepted finding, a human edit to a frozen plan
- [accepted] low — plans/llm-call-log.md:550 — the plan required `with_raw_response.create(...).parse()` and `retries_taken` to be demonstrated against Groq once before anything depended on them, and that run still has not happened (no `GROQ_API_KEY` in the environment), so every gate exercises a hand-written fake whose raw wrapper the build also wrote; the installed 1.109.1 does expose `CompletionsWithRawResponse.create` and `LegacyAPIResponse.retries_taken`, which bounds the risk to whether a real response populates the count — run one live call and print both, or accept `sdk_retries` and every `http_requests` derived from it as unproven
- [accepted] low — BACKLOG.md:271 — two entries under "`GROQ_API_KEY` is documented as required" and "Two unmapped crash paths" describe behaviour this build changed: `message.content is None` is now `bad_shape`, recorded and retried, and `AuthenticationError`, as an `APIStatusError`, is now caught and mapped to `llm_unavailable` 502 rather than escaping unmapped — leaving them listed as open work makes the backlog claim crashes that no longer happen — trim both entries to what survives (the `LLM_MAX_RETRIES=0` `raise None` path, and the fail-fast-at-import question)
