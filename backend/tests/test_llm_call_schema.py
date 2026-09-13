"""Phase 1 gates — the two tables, the view, and what they promise.

Everything here talks to Postgres in raw SQL. Nothing imports
`queries.llm_calls`: invariant 13 says no test file may, and a gate that asserts
what a row *is* survives the query layer being rewritten under it.

The column assertions name the columns the plan declares and check each one's
type and nullability. They deliberately do not assert that the table has *only*
those columns — two accepted review findings may add one — so "the declared
columns are there, typed as declared" is the invariant, not the column count.
"""
import pathlib

import pytest

pytestmark = pytest.mark.integration

SCHEMA_SQL = pathlib.Path(__file__).resolve().parents[1] / "schema.sql"

# name -> (data_type, is_nullable)
LLM_CALLS_COLUMNS = {
    "id": ("bigint", "NO"),
    "session_id": ("text", "NO"),
    "ply": ("integer", "NO"),
    "created_at": ("timestamp with time zone", "NO"),
    "model": ("text", "NO"),
    "reasoning_effort": ("text", "NO"),
    "max_retries": ("integer", "NO"),
    "sdk_max_retries": ("integer", "NO"),
    "fen": ("text", "NO"),
    "player_text": ("text", "NO"),
    "candidate_ucis": ("ARRAY", "NO"),
    "prior_tone": ("text", "YES"),
    "outcome": ("text", "NO"),
    "attempts": ("integer", "NO"),
    "latency_ms": ("integer", "NO"),
    "chosen_uci": ("text", "YES"),
    "off_list": ("boolean", "YES"),
    "intent": ("text", "YES"),
    "rationale": ("text", "YES"),
    "tone_summary": ("text", "YES"),
}

LLM_CALL_ATTEMPTS_COLUMNS = {
    "call_id": ("bigint", "NO"),
    "attempt": ("integer", "NO"),
    "outcome": ("text", "NO"),
    "latency_ms": ("integer", "NO"),
    "sdk_retries": ("integer", "YES"),
    "status_code": ("integer", "YES"),
    "raw_content": ("text", "YES"),
    "error_detail": ("text", "YES"),
    "prompt_tokens": ("integer", "YES"),
    "completion_tokens": ("integer", "YES"),
    "reasoning_tokens": ("integer", "YES"),
}

# `unexpected` is the catch-all both levels gained on 2026-09-13. At attempt
# level it is what an `except Exception` records; at call level it is the
# outcome a call carries until something sets a real one, so a call that ends in
# a way nothing anticipated is still insertable and takes its earlier, correctly
# classified attempts with it instead of rolling them back.
CALL_OUTCOMES = ("ok", "exhausted", "transport", "unexpected")
ATTEMPT_OUTCOMES = ("ok", "bad_json", "missing_key", "invalid_uci", "bad_shape",
                    "transport", "unexpected")


def _columns(pgdb, table):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        return {
            r["column_name"]: (r["data_type"], r["is_nullable"])
            for r in cur.fetchall()
        }


def _insert_call(pgdb, **overrides):
    """One llm_calls row, returning its generated id."""
    row = {
        "session_id": "no-such-game",
        "ply": 1,
        "model": "qwen/qwen3.6-27b",
        "reasoning_effort": "none",
        "max_retries": 3,
        "sdk_max_retries": 3,
        "fen": "startpos",
        "player_text": "attack",
        "candidate_ucis": ["e2e4", "d2d4"],
        "outcome": "ok",
        "attempts": 1,
        "latency_ms": 10,
    }
    row.update(overrides)
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with pgdb.cursor() as cur:
        cur.execute(
            f"INSERT INTO llm_calls ({cols}) VALUES ({marks}) RETURNING id",
            tuple(row.values()),
        )
        return cur.fetchone()["id"]


def _insert_attempt(pgdb, call_id, attempt, outcome="ok", latency_ms=5, **rest):
    row = {
        "call_id": call_id,
        "attempt": attempt,
        "outcome": outcome,
        "latency_ms": latency_ms,
    }
    row.update(rest)
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with pgdb.cursor() as cur:
        cur.execute(
            f"INSERT INTO llm_call_attempts ({cols}) VALUES ({marks})",
            tuple(row.values()),
        )


@pytest.fixture
def scrub(pgdb):
    """Delete whatever the test inserted, by session id.

    Tolerates the table not existing so that, before Phase 1 lands, each gate
    fails with what it actually asserts instead of a teardown error.
    """
    import psycopg

    sids = []
    yield sids
    for sid in sids:
        try:
            with pgdb.cursor() as cur:
                cur.execute("DELETE FROM llm_calls WHERE session_id = %s", (sid,))
        except psycopg.errors.UndefinedTable:
            pass


def test_llm_calls_has_the_declared_columns(pgdb):
    found = _columns(pgdb, "llm_calls")
    assert found, "llm_calls does not exist"
    for name, spec in LLM_CALLS_COLUMNS.items():
        assert found.get(name) == spec, f"llm_calls.{name} is {found.get(name)}"


def test_llm_call_attempts_has_the_declared_columns(pgdb):
    found = _columns(pgdb, "llm_call_attempts")
    assert found, "llm_call_attempts does not exist"
    for name, spec in LLM_CALL_ATTEMPTS_COLUMNS.items():
        assert found.get(name) == spec, (
            f"llm_call_attempts.{name} is {found.get(name)}"
        )


def test_the_indexes_the_readout_queries_need_exist(pgdb):
    """Every documented query filters on one of these three."""
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'llm_calls'"
        )
        names = {r["indexname"] for r in cur.fetchall()}
    assert {
        "idx_llm_calls_session",
        "idx_llm_calls_created",
        "idx_llm_calls_outcome",
    } <= names


def test_an_attempt_is_keyed_by_call_and_number(pgdb, scrub):
    """PRIMARY KEY (call_id, attempt): attempt 1 cannot be written twice."""
    import psycopg

    scrub.append("pk-probe")
    call_id = _insert_call(pgdb, session_id="pk-probe")
    _insert_attempt(pgdb, call_id, 1)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _insert_attempt(pgdb, call_id, 1)


def test_identity_insert_returns_the_new_id(pgdb, scrub):
    """`insert_call` reads `RETURNING id`; a NULL there is a TypeError upstream."""
    scrub.append("identity-probe")
    first = _insert_call(pgdb, session_id="identity-probe")
    second = _insert_call(pgdb, session_id="identity-probe")
    assert isinstance(first, int)
    assert second > first


def test_session_id_carries_no_foreign_key(pgdb, scrub):
    """Accepted finding: the warehouse record outlives its game.

    A call row names a session that does not exist and the insert succeeds. If
    this starts raising, the log has been made to die with the game.
    """
    scrub.append("game-that-never-existed")
    call_id = _insert_call(pgdb, session_id="game-that-never-existed")
    assert call_id > 0


def test_deleting_a_call_deletes_its_attempts(pgdb, scrub):
    scrub.append("cascade-probe")
    call_id = _insert_call(pgdb, session_id="cascade-probe")
    _insert_attempt(pgdb, call_id, 1)
    with pgdb.cursor() as cur:
        cur.execute("DELETE FROM llm_calls WHERE id = %s", (call_id,))
        cur.execute(
            "SELECT count(*) AS n FROM llm_call_attempts WHERE call_id = %s",
            (call_id,),
        )
        assert cur.fetchone()["n"] == 0


@pytest.mark.parametrize("outcome", CALL_OUTCOMES)
def test_every_declared_call_outcome_is_accepted(pgdb, scrub, outcome):
    scrub.append(f"outcome-{outcome}")
    assert _insert_call(pgdb, session_id=f"outcome-{outcome}", outcome=outcome) > 0


@pytest.mark.parametrize("outcome", ATTEMPT_OUTCOMES)
def test_every_declared_attempt_outcome_is_accepted(pgdb, scrub, outcome):
    scrub.append(f"attempt-outcome-{outcome}")
    call_id = _insert_call(pgdb, session_id=f"attempt-outcome-{outcome}")
    _insert_attempt(pgdb, call_id, 1, outcome=outcome)


def test_an_unnamed_call_outcome_is_rejected(pgdb, scrub):
    import psycopg

    scrub.append("bad-outcome")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_call(pgdb, session_id="bad-outcome", outcome="nonsense")


def test_an_unnamed_attempt_outcome_is_rejected(pgdb, scrub):
    import psycopg

    scrub.append("bad-attempt-outcome")
    call_id = _insert_call(pgdb, session_id="bad-attempt-outcome")
    with pytest.raises(psycopg.errors.CheckViolation):
        _insert_attempt(pgdb, call_id, 1, outcome="nonsense")


def test_applying_schema_sql_again_changes_nothing(pgdb, scrub):
    """Phase 1's own check: the file is how a live database is migrated.

    A row written before the re-apply is still there afterwards, which is the
    part that matters — `CREATE TABLE IF NOT EXISTS` is not the only statement
    in the file.
    """
    scrub.append("idempotence-probe")
    call_id = _insert_call(pgdb, session_id="idempotence-probe")

    sql = SCHEMA_SQL.read_text()
    with pgdb.cursor() as cur:
        cur.execute(sql)
        cur.execute(sql)
        cur.execute("SELECT count(*) AS n FROM llm_calls WHERE id = %s", (call_id,))
        assert cur.fetchone()["n"] == 1


def test_the_view_counts_both_retry_layers_separately(pgdb, scrub):
    """The two counts the plan says must never be confused.

    Three calls on one model: one clean, one that took two loop attempts with a
    known SDK retry count, one transport failure whose sdk_retries is unknown.

      loop_attempts  = 1 + 2 + 1                      = 4
      http_requests  = (1+0) + (1+1 + 1+0) + (1+3)    = 8
                                     ^ the NULL is filled in from sdk_max_retries

    The transport call is written with `max_retries = 1` and
    `sdk_max_retries = 3`, so the two limits cannot be confused: a view that
    still fills in from the loop's limit reports 6 here instead of 8.

    Token sums skip the NULLs rather than counting them as zero.
    """
    scrub.append("view-probe")
    model = "view-probe-model"
    ok = _insert_call(pgdb, session_id="view-probe", ply=1, outcome="ok",
                      model=model, attempts=1, latency_ms=100, off_list=True)
    _insert_attempt(pgdb, ok, 1, "ok", 90, sdk_retries=0,
                    prompt_tokens=10, completion_tokens=5, reasoning_tokens=2)

    retried = _insert_call(pgdb, session_id="view-probe", ply=2, outcome="ok",
                           model=model, attempts=2, latency_ms=300,
                           off_list=False)
    _insert_attempt(pgdb, retried, 1, "invalid_uci", 140, sdk_retries=1,
                    prompt_tokens=10, completion_tokens=5)
    _insert_attempt(pgdb, retried, 2, "ok", 140, sdk_retries=0,
                    prompt_tokens=10, completion_tokens=5, reasoning_tokens=3)

    gone = _insert_call(pgdb, session_id="view-probe", ply=3, model=model,
                        outcome="transport", attempts=1, latency_ms=50,
                        max_retries=1, sdk_max_retries=3)
    _insert_attempt(pgdb, gone, 1, "transport", 50)      # sdk_retries NULL

    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT * FROM llm_call_metrics WHERE model = %s", (model,),
        )
        rows = [r for r in cur.fetchall()]
    assert len(rows) == 1, "one model, one day, one row"
    m = rows[0]
    assert m["calls"] == 3
    assert m["ok"] == 2
    assert m["exhausted"] == 0
    assert m["transport"] == 1
    assert m["needed_a_retry"] == 1
    assert m["loop_attempts"] == 4
    assert m["http_requests"] == 8
    assert m["off_list"] == 1
    assert m["prompt_tokens"] == 30
    assert m["completion_tokens"] == 15
    assert m["reasoning_tokens"] == 5
    assert float(m["p50_ms"]) == 100.0


def test_the_four_classes_sum_to_the_calls(pgdb, scrub):
    """Invariant 19: every call is counted in exactly one class.

    A mixed set with one call of each of the four outcomes. The view's class
    columns must account for all of them:

        ok + exhausted + transport + unexpected = calls

    `unexpected` is the class the view was written without. A view that filters
    only the first three counts this set as calls = 4 with three classes
    summing to 3, and the call that ended in a way nobody predicted is the one
    the readout cannot show.
    """
    scrub.append("sum-probe")
    model = "sum-probe-model"
    for ply, outcome in enumerate(CALL_OUTCOMES, start=1):
        call_id = _insert_call(pgdb, session_id="sum-probe", ply=ply,
                               model=model, outcome=outcome, attempts=1,
                               latency_ms=10)
        _insert_attempt(pgdb, call_id, 1, "ok", 10, sdk_retries=0)

    with pgdb.cursor() as cur:
        cur.execute("SELECT * FROM llm_call_metrics WHERE model = %s", (model,))
        rows = cur.fetchall()
    assert len(rows) == 1, "one model, one day, one row"
    m = rows[0]
    assert m["calls"] == 4
    assert m["ok"] == 1
    assert m["exhausted"] == 1
    assert m["transport"] == 1
    assert m["unexpected"] == 1
    assert m["ok"] + m["exhausted"] + m["transport"] + m["unexpected"] == m["calls"]


def test_an_unexpected_call_increments_only_its_own_class(pgdb, scrub):
    """Invariant 19, second half: it lands in `unexpected` and nowhere else."""
    scrub.append("unexpected-probe")
    model = "unexpected-probe-model"
    call_id = _insert_call(pgdb, session_id="unexpected-probe", ply=1,
                           model=model, outcome="unexpected", attempts=1,
                           latency_ms=10)
    _insert_attempt(pgdb, call_id, 1, "unexpected", 10, error_detail="TypeError: x")

    with pgdb.cursor() as cur:
        cur.execute("SELECT * FROM llm_call_metrics WHERE model = %s", (model,))
        rows = cur.fetchall()
    assert len(rows) == 1
    m = rows[0]
    assert m["calls"] == 1
    assert m["unexpected"] == 1
    assert m["ok"] == 0
    assert m["exhausted"] == 0
    assert m["transport"] == 0
