"""Invariants 1-7 and 9-12 — what a say_move leaves behind in the log.

These drive the real HTTP route against a real Postgres and then read the rows
back on a second connection. The seam is the OpenAI client object, not
`pick_move_with_llm`: the failure classifier, the attempt loop and the retry
count all live inside that function, so a fake that replaces it tests nothing
this feature is about.

The fake answers on both `.create(...)` and `.with_raw_response.create(...)`, so
a gate that fails does so because a row is missing or wrong — never because the
call shape has not been changed yet. `retries_taken` is only reachable through
the raw wrapper, so the one gate that asserts it is the one that pins the shape.

Nothing here imports `queries.llm_calls` (invariant 13).

Amended by plans/input-tokens-lazy-explanations.md, frozen 2026-09-18: the log
is a personal analysis record, written only for an LLM move whose game
transaction committed. A failed call, a transport error and a rolled-back move
each leave no row at all. The gates that used to require a row on those paths
now require none — that is the same invariant read from the other side, not a
relaxation: each still drives the whole path and still asserts the board did
not move.
"""
import contextlib
import json
import os
import threading

import chess
import httpx
import psycopg
import pytest
from openai import APIConnectionError, APIStatusError
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice
from openai.types.completion_usage import CompletionTokensDetails, CompletionUsage

from controller_operations import llm
from controller_operations.engine import rank_moves
from tests.conftest import START_FEN

pytestmark = pytest.mark.integration


# --- the model, scripted ----------------------------------------------------

class _Step:
    """One scripted HTTP round trip: a reply, or the error the SDK raises."""

    def __init__(self, completion=None, error=None, sdk_retries=0):
        self.completion = completion
        self.error = error
        self.sdk_retries = sdk_retries


def _completion(content, usage=True, prompt_tokens=11, completion_tokens=7,
                reasoning_tokens=3, choices=True):
    """A real ChatCompletion, so `usage` and `content` are Optional the way the
    SDK's are: `content=None` and `choices=[]` are both shapes Groq can return
    and neither is a string `json.loads` can read."""
    return ChatCompletion(
        id="fake",
        model=llm.LLM_MODEL,
        object="chat.completion",
        created=0,
        choices=[Choice(
            finish_reason="stop",
            index=0,
            message=ChatCompletionMessage(role="assistant", content=content),
        )] if choices else [],
        usage=CompletionUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            completion_tokens_details=CompletionTokensDetails(
                reasoning_tokens=reasoning_tokens
            ),
        ) if usage else None,
    )


def _says(content, sdk_retries=0, **kwargs):
    return _Step(completion=_completion(content, **kwargs), sdk_retries=sdk_retries)


def _raises(error):
    return _Step(error=error)


def _json_reply(uci, tone="wary"):
    """The lean move reply: two fields, and nothing the `?` panel would use."""
    return json.dumps({"uci": uci, "tone_summary": tone})


def _explain_reply(intent="an intent", rationale="a rationale"):
    """What the on-demand explanation call answers with."""
    return json.dumps({"intent": intent, "rationale": rationale})


def _connection_error():
    return APIConnectionError(request=httpx.Request("POST", llm.GROQ_BASE_URL))


def _status_error(status):
    """What the SDK raises once its own retries are spent on an HTTP error."""
    request = httpx.Request("POST", llm.GROQ_BASE_URL)
    response = httpx.Response(status, request=request)
    return APIStatusError(f"Error code: {status}", response=response, body=None)


class _RawResponse:
    def __init__(self, completion, retries_taken):
        self.retries_taken = retries_taken
        self._completion = completion

    def parse(self):
        return self._completion


class _Completions:
    """The scripted client. `kwargs` keeps every request it was handed, so a
    gate can assert what was actually asked of the model — which prompt shape
    is half of this plan."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.kwargs = []

    def _step(self, kwargs):
        self.calls += 1
        self.kwargs.append(kwargs)
        assert self._script, "the client was called more times than the script allows"
        step = self._script.pop(0)
        if step.error is not None:
            raise step.error
        return step

    def create(self, **kwargs):
        return self._step(kwargs).completion

    @property
    def with_raw_response(self):
        return _RawCompletions(self)


class _RawCompletions:
    def __init__(self, outer):
        self._outer = outer

    def create(self, **kwargs):
        step = self._outer._step(kwargs)
        return _RawResponse(step.completion, step.sdk_retries)


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, script):
        self.completions = _Completions(script)
        self.chat = _Chat(self.completions)


def _script(monkeypatch, *steps, max_retries=None):
    """Replace the module-level client. Returns it, for the call count."""
    client = _FakeClient(steps)
    monkeypatch.setattr(llm, "_client", client)
    monkeypatch.setattr(llm, "LLM_BACKOFF_BASE", 0.0)
    if max_retries is not None:
        monkeypatch.setattr(llm, "LLM_MAX_RETRIES", max_retries)
    return client


# --- reading the log back ---------------------------------------------------

def _calls(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT * FROM llm_calls WHERE session_id = %s ORDER BY id", (sid,)
        )
        return cur.fetchall()


def _attempts(pgdb, call_id):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT * FROM llm_call_attempts WHERE call_id = %s ORDER BY attempt",
            (call_id,),
        )
        return cur.fetchall()


def _moves(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT * FROM moves WHERE session_id = %s ORDER BY ply", (sid,)
        )
        return cur.fetchall()


def _fen(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute("SELECT fen FROM sessions WHERE id = %s", (sid,))
        return cur.fetchone()["fen"]


@contextlib.contextmanager
def _inserts_fail(pgdb, table):
    """Make every INSERT on `table` raise, from inside Postgres.

    A trigger rather than a monkeypatched query function: the failure has to
    reach the code under test the way a full disk or a dropped column would,
    and the test must not have to know which function issues the statement.
    """
    fn = f"pc_test_fail_{table}"
    with pgdb.cursor() as cur:
        cur.execute(
            f"CREATE OR REPLACE FUNCTION {fn}() RETURNS TRIGGER AS $$ "
            f"BEGIN RAISE EXCEPTION 'forced failure inserting into {table}'; "
            f"END; $$ LANGUAGE plpgsql"
        )
        cur.execute(
            f"CREATE TRIGGER trg_{fn} BEFORE INSERT ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {fn}()"
        )
    try:
        yield
    finally:
        with pgdb.cursor() as cur:
            cur.execute(f"DROP TRIGGER IF EXISTS trg_{fn} ON {table}")
            cur.execute(f"DROP FUNCTION IF EXISTS {fn}()")


@pytest.fixture
def game(make_session, pgdb):
    """A session whose log rows are cleaned up too.

    `llm_calls.session_id` carries no foreign key, so deleting the session does
    not take the log with it — that is the point of the column, and it makes
    cleaning up this table the test's own job.
    """
    made = []

    def _make(name, fen=START_FEN):
        session = make_session(name, fen)
        made.append(session["sid"])
        return session

    yield _make

    for sid in made:
        try:
            with pgdb.cursor() as cur:
                cur.execute("DELETE FROM llm_calls WHERE session_id = %s", (sid,))
        except psycopg.errors.UndefinedTable:
            pass


def _say(app_module, session, text="press the attack"):
    client = app_module.app.test_client()
    return client.post(
        f"/sessions/{session['sid']}/say",
        json={"text": text, "playerToken": session["white_token"]},
    )


def _move(app_module, session, uci="e2e4"):
    client = app_module.app.test_client()
    return client.post(
        f"/sessions/{session['sid']}/move",
        json={"uci": uci, "playerToken": session["white_token"]},
    )


def _candidates():
    return [uci for uci, _san in rank_moves(chess.Board(START_FEN))]


def _a_legal_move_off_the_list():
    listed = set(_candidates())
    off = [m.uci() for m in chess.Board(START_FEN).legal_moves if m.uci() not in listed]
    assert off, "the engine listed every legal move; off_list is untestable here"
    return off[0]


# --- invariant 1 ------------------------------------------------------------

def test_a_clean_call_writes_one_call_row_and_one_attempt(
    app_module, pgdb, game, monkeypatch
):
    """Invariants 1 and 12, and the header fields that make the row worth having."""
    session = game("log-clean")
    _script(monkeypatch, _says(_json_reply("e2e4")))

    # Invariant 12 rides along here rather than standing as its own gate: on
    # its own it passes before the work exists, which makes it no gate at all.
    threads_before = set(threading.enumerate())
    assert _say(app_module, session, "come out swinging").status_code == 200
    assert set(threading.enumerate()) - threads_before == set(), (
        "the log write started a thread; nothing in this feature runs in the "
        "background"
    )

    calls = _calls(pgdb, session["sid"])
    assert len(calls) == 1
    call = calls[0]
    assert call["outcome"] == "ok"
    assert call["attempts"] == 1
    assert call["chosen_uci"] == "e2e4"
    assert call["player_text"] == "come out swinging"
    assert call["fen"] == START_FEN
    assert call["model"] == llm.LLM_MODEL
    assert call["reasoning_effort"] == llm.LLM_REASONING_EFFORT
    assert call["max_retries"] == llm.LLM_MAX_RETRIES
    assert call["sdk_max_retries"] == llm._client.max_retries
    assert call["candidate_ucis"] == _candidates()
    assert call["tone_summary"] == "wary"
    assert call["intent"] is None, (
        "the move call no longer asks for an explanation, so the analysis row "
        "has none to copy"
    )
    assert call["rationale"] is None

    rows = _attempts(pgdb, call["id"])
    assert len(rows) == 1
    assert rows[0]["attempt"] == 1
    assert rows[0]["outcome"] == "ok"
    assert rows[0]["status_code"] is None, (
        "invariant 16 — the HTTP call succeeded, so there is no status to record"
    )


def test_the_call_row_joins_the_move_it_produced(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 1's second half: `USING (session_id, ply)` finds the move."""
    session = game("log-join")
    _script(monkeypatch, _says(_json_reply("e2e4")))
    _say(app_module, session)

    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT c.chosen_uci, m.uci FROM llm_calls c "
            "JOIN moves m USING (session_id, ply) WHERE c.session_id = %s",
            (session["sid"],),
        )
        joined = cur.fetchall()
    assert len(joined) == 1
    assert joined[0]["chosen_uci"] == joined[0]["uci"] == "e2e4"


# --- invariant 2 ------------------------------------------------------------

def test_a_retried_call_records_both_attempts(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 2, with the accepted finding on `raw_content`.

    Readable TEXT on every attempt that returned content, successful attempts
    included — so both rows carry the exact string the model sent.
    """
    session = game("log-retry")
    bad = _json_reply("e2e5")          # syntactically fine, not legal here
    good = _json_reply("e2e4")
    _script(monkeypatch, _says(bad), _says(good))

    assert _say(app_module, session).status_code == 200

    call = _calls(pgdb, session["sid"])[0]
    assert call["outcome"] == "ok"
    assert call["attempts"] == 2
    assert call["chosen_uci"] == "e2e4"

    rows = _attempts(pgdb, call["id"])
    assert [(r["attempt"], r["outcome"]) for r in rows] == [
        (1, "invalid_uci"), (2, "ok"),
    ]
    assert rows[0]["raw_content"] == bad
    assert rows[1]["raw_content"] == good
    assert rows[0]["error_detail"], "a failed attempt records why it failed"


def test_the_sdk_retry_count_is_recorded_per_attempt(
    app_module, pgdb, game, monkeypatch
):
    """The two layers, separated.

    The loop made one attempt; the SDK made three HTTP requests inside it. Only
    `with_raw_response` exposes that number, so a row saying 0 here means the
    transport layer is still invisible.
    """
    session = game("log-sdk-retries")
    _script(monkeypatch, _says(_json_reply("e2e4"), sdk_retries=2))

    assert _say(app_module, session).status_code == 200

    call = _calls(pgdb, session["sid"])[0]
    assert call["attempts"] == 1
    assert _attempts(pgdb, call["id"])[0]["sdk_retries"] == 2


# --- invariant 5 ------------------------------------------------------------

def test_unparseable_json_is_bad_json_and_never_invalid_uci(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 5. `json.JSONDecodeError` is a `ValueError` subclass, so a
    classifier that tests `ValueError` first labels this `invalid_uci`."""
    session = game("log-bad-json")
    _script(monkeypatch, _says("Sure! Here is the move: e2e4"),
            _says(_json_reply("e2e4")))

    assert _say(app_module, session).status_code == 200

    call = _calls(pgdb, session["sid"])[0]
    rows = _attempts(pgdb, call["id"])
    assert [(r["attempt"], r["outcome"]) for r in rows] == [(1, "bad_json"), (2, "ok")]
    assert [r["status_code"] for r in rows] == [None, None], (
        "invariant 16 — a 200 carrying garbage still has no error status"
    )


# --- invariant 3 ------------------------------------------------------------

def test_an_exhausted_call_writes_no_row_and_the_board_does_not_move(
    app_module, pgdb, game, monkeypatch
):
    """Success-only logging. No move committed, so there is nothing to record."""
    session = game("log-exhausted")
    _script(monkeypatch, _says(_json_reply("e2e5")), _says(_json_reply("e2e5")),
            max_retries=2)

    response = _say(app_module, session)
    assert response.status_code == 502
    assert response.get_json()["error"] == "llm_bad_response"

    assert _calls(pgdb, session["sid"]) == []
    assert _moves(pgdb, session["sid"]) == []
    assert _fen(pgdb, session["sid"]) == START_FEN


# --- invariant 4 ------------------------------------------------------------

def test_a_transport_failure_writes_no_row(
    app_module, pgdb, game, monkeypatch
):
    """Success-only logging, transport arm. The player still gets the mapped
    error; the analysis tables stay empty."""
    session = game("log-transport")
    _script(monkeypatch, _raises(_connection_error()))

    response = _say(app_module, session)
    assert response.status_code == 502
    assert response.get_json()["error"] == "llm_unavailable"

    assert _calls(pgdb, session["sid"]) == []
    assert _moves(pgdb, session["sid"]) == []
    assert _fen(pgdb, session["sid"]) == START_FEN


# --- invariants 14, 15 and 16 ----------------------------------------------

@pytest.mark.parametrize("status", [429, 408, 409, 500, 503])
def test_a_provider_http_error_writes_no_row(
    app_module, pgdb, game, monkeypatch, status
):
    """Invariant 14, under success-only logging.

    The mapping still has to hold — every one of these is `llm_unavailable` and
    a 502, not a bare 500 — but no move committed, so nothing is recorded. The
    status column keeps its meaning for the rows that do get written.
    """
    session = game(f"log-http-{status}")
    _script(monkeypatch, _raises(_status_error(status)))

    response = _say(app_module, session)
    assert response.status_code == 502
    assert response.get_json()["error"] == "llm_unavailable"

    assert _calls(pgdb, session["sid"]) == []
    assert _moves(pgdb, session["sid"]) == []
    assert _fen(pgdb, session["sid"]) == START_FEN


@pytest.mark.parametrize("shape", ["no_content", "no_choices"])
def test_a_reply_with_no_usable_content_is_bad_shape_and_retried(
    app_module, pgdb, game, monkeypatch, shape
):
    """Invariant 15.

    `message.content` of None is not a string, so today it reaches json.loads as
    a TypeError, escapes the except clause, and takes the whole request out with
    it. It is a content failure like any other: record it, retry it.
    """
    session = game(f"log-bad-shape-{shape}")
    empty = (_says(None) if shape == "no_content"
             else _says(_json_reply("e2e4"), choices=False))
    _script(monkeypatch, empty, _says(_json_reply("e2e4")))

    assert _say(app_module, session).status_code == 200

    call = _calls(pgdb, session["sid"])[0]
    assert call["outcome"] == "ok"
    assert call["attempts"] == 2

    rows = _attempts(pgdb, call["id"])
    assert [(r["attempt"], r["outcome"]) for r in rows] == [(1, "bad_shape"), (2, "ok")]
    assert rows[0]["raw_content"] is None, "nothing came back to store"
    assert rows[0]["status_code"] is None, "the HTTP call itself succeeded"
    assert rows[0]["error_detail"]


def test_a_call_that_only_ever_returns_no_content_is_exhausted(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 15's tail: bad_shape exhausts like any other content failure,
    which is llm_bad_response and not llm_unavailable — and, under success-only
    logging, writes no row."""
    session = game("log-bad-shape-exhausted")
    _script(monkeypatch, _says(None), _says(None), max_retries=2)

    response = _say(app_module, session)
    assert response.status_code == 502
    assert response.get_json()["error"] == "llm_bad_response"

    assert _calls(pgdb, session["sid"]) == []
    assert _moves(pgdb, session["sid"]) == []


# --- invariants 6 and 7 -----------------------------------------------------

def test_a_rolled_back_move_writes_no_row(
    app_module, pgdb, game, monkeypatch
):
    """The decision that replaced invariant 6.

    A successful LLM reply is not a successful move. The move write failed and
    the transaction rolled back, so the analysis tables must show nothing: a row
    here would count a move that was never played.

    This is the gate the persister's placement turns on. Writing from the outer
    `finally` — which runs whether the transaction committed or rolled back —
    passes the old gate and fails this one.
    """
    session = game("log-rollback")
    _script(monkeypatch, _says(_json_reply("e2e4")))

    with _inserts_fail(pgdb, "moves"):
        assert _say(app_module, session).status_code == 500

    assert _calls(pgdb, session["sid"]) == []
    assert _moves(pgdb, session["sid"]) == []
    assert _fen(pgdb, session["sid"]) == START_FEN


def test_a_failing_log_write_never_fails_the_move(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 7 — best-effort under database failure, in that direction only.

    The log write is allowed to lose its row. It is not allowed to lose the
    move, and it is not allowed to raise out of the `finally` in place of
    whatever was already travelling up the stack.
    """
    session = game("log-write-fails")
    _script(monkeypatch, _says(_json_reply("e2e4")))

    with _inserts_fail(pgdb, "llm_calls"):
        response = _say(app_module, session)

    assert response.status_code == 200
    moves = _moves(pgdb, session["sid"])
    assert len(moves) == 1
    assert moves[0]["uci"] == "e2e4"
    assert _fen(pgdb, session["sid"]) != START_FEN
    assert _calls(pgdb, session["sid"]) == []


def test_a_failing_attempt_write_leaves_no_half_row(
    app_module, pgdb, game, monkeypatch
):
    """Accepted finding: one transaction around both inserts.

    A committed call row whose attempts are missing is worse than no row: it
    counts as a call in every aggregate and contributes no attempts, so the
    retry rate silently falls.
    """
    session = game("log-attempt-fails")
    _script(monkeypatch, _says(_json_reply("e2e4")))

    with _inserts_fail(pgdb, "llm_call_attempts"):
        response = _say(app_module, session)

    assert response.status_code == 200
    assert len(_moves(pgdb, session["sid"])) == 1
    assert _calls(pgdb, session["sid"]) == []


# --- invariant 9 ------------------------------------------------------------

def test_make_move_writes_no_call_row(app_module, pgdb, game, monkeypatch):
    """Invariant 9 — the typed move path calls no LLM and logs nothing."""
    session = game("log-make-move")
    _script(monkeypatch)                     # any call at all is a failure

    assert _move(app_module, session).status_code == 200
    assert _calls(pgdb, session["sid"]) == []


# --- invariant 10 -----------------------------------------------------------

def test_off_list_is_true_for_a_legal_move_the_engine_did_not_list(
    app_module, pgdb, game, monkeypatch
):
    session = game("log-off-list")
    off = _a_legal_move_off_the_list()
    _script(monkeypatch, _says(_json_reply(off)))

    assert _say(app_module, session).status_code == 200

    call = _calls(pgdb, session["sid"])[0]
    assert call["chosen_uci"] == off
    assert off not in call["candidate_ucis"]
    assert call["off_list"] is True


def test_off_list_is_false_for_a_listed_move(
    app_module, pgdb, game, monkeypatch
):
    session = game("log-on-list")
    listed = _candidates()[0]
    _script(monkeypatch, _says(_json_reply(listed)))

    assert _say(app_module, session).status_code == 200

    call = _calls(pgdb, session["sid"])[0]
    assert call["chosen_uci"] == listed
    assert call["off_list"] is False


# --- invariant 11 -----------------------------------------------------------

def test_token_counts_are_recorded_when_the_provider_reports_them(
    app_module, pgdb, game, monkeypatch
):
    session = game("log-tokens")
    _script(monkeypatch, _says(_json_reply("e2e4"), prompt_tokens=101,
                               completion_tokens=37, reasoning_tokens=5))

    assert _say(app_module, session).status_code == 200

    row = _attempts(pgdb, _calls(pgdb, session["sid"])[0]["id"])[0]
    assert row["prompt_tokens"] == 101
    assert row["completion_tokens"] == 37
    assert row["reasoning_tokens"] == 5


def test_token_counts_are_null_and_never_zero_without_usage(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 11. `usage` is Optional and so is `completion_tokens_details`.
    A zero written for a missing count is an average that is quietly wrong."""
    session = game("log-no-usage")
    _script(monkeypatch, _says(_json_reply("e2e4"), usage=False))

    assert _say(app_module, session).status_code == 200

    row = _attempts(pgdb, _calls(pgdb, session["sid"])[0]["id"])[0]
    assert row["prompt_tokens"] is None
    assert row["completion_tokens"] is None
    assert row["reasoning_tokens"] is None


def test_the_call_latency_covers_its_attempts(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 11's second half. The call is timed around the whole loop, so
    it includes the backoff sleeps the attempts do not."""
    session = game("log-latency")
    _script(monkeypatch, _says(_json_reply("e2e5")), _says(_json_reply("e2e4")))

    assert _say(app_module, session).status_code == 200

    call = _calls(pgdb, session["sid"])[0]
    rows = _attempts(pgdb, call["id"])
    assert all(r["latency_ms"] >= 0 for r in rows)
    assert call["latency_ms"] >= sum(r["latency_ms"] for r in rows)


# --- invariant 12 -----------------------------------------------------------

def _backend_count(pgdb):
    """Postgres backends on this database. The same count
    `test_connection_isolation.py` asserts against, read here for what is left
    behind rather than for what is held open."""
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM pg_stat_activity "
            "WHERE datname = current_database()"
        )
        return cur.fetchone()["n"]


SAYS = 4                      # one to settle the baseline, three to measure
LOG_CONN_PATHS = {
    # path -> (the steps one say_move consumes at LLM_MAX_RETRIES = 3,
    #          rows that path must leave behind under success-only logging)
    "ok":        ([_says(_json_reply("e2e4"))], 1),
    "exhausted": ([_says(_json_reply("e2e5"))] * 3, 0),
    "transport": ([_raises(_connection_error())], 0),
}


@pytest.mark.parametrize("path", sorted(LOG_CONN_PATHS))
def test_the_log_connection_is_gone_between_requests(
    app_module, pgdb, game, monkeypatch, path
):
    """Invariant 12's second half: `pg_stat_activity` shows no connection
    belonging to this feature between requests.

    Invariant 8 gates the connection count *while* a call is parked; this gates
    what is still there once it has returned. The persister opens its own
    connection after the move transaction closed, so a `with db()` that did not
    close — or a pool this feature introduced — leaves a backend behind that no
    later request reuses. All three terminal paths are driven: under
    success-only logging two of them must write nothing, and a path that opens
    a connection and then decides not to write is exactly where a leak hides.

    One session per say, always: on the `ok` path the first move advances the
    board and a second white say would be refused before the LLM, writing no
    row and measuring nothing. Sessions are inserted on the test's own
    connection, so making four of them does not move the count being read.
    """
    steps, rows_per_say = LOG_CONN_PATHS[path]
    _script(monkeypatch, *(steps * SAYS), max_retries=3)
    sessions = [game(f"log-conn-{path}-{n}") for n in range(SAYS)]

    # Settle first: app import, engine warm-up and the session INSERT all touch
    # the database, so the baseline has to be read after a say_move has already
    # run rather than before the first one.
    _say(app_module, sessions[0])
    first = _calls(pgdb, sessions[0]["sid"])
    assert len(first) == rows_per_say
    if rows_per_say:
        assert first[0]["outcome"] == "ok"
    baseline = _backend_count(pgdb)

    for session in sessions[1:]:
        _say(app_module, session)

    logged = [len(_calls(pgdb, s["sid"])) for s in sessions]
    assert logged == [rows_per_say] * SAYS, (
        f"rows per say was {logged}, expected {rows_per_say} each; the "
        "persistence path was not reached the same way every time, so this "
        "gate proves nothing"
    )
    leaked = _backend_count(pgdb) - baseline
    assert leaked == 0, (
        f"{leaked} Postgres backend(s) outlived the requests that opened them; "
        "the log write must close its connection"
    )

    # The instrument, checked against a backend this test leaks on purpose.
    # Without this the gate passes just as well when `_backend_count` has
    # stopped seeing the app's connections at all, which is no gate.
    held = psycopg.connect(os.environ["DATABASE_URL"])
    try:
        assert _backend_count(pgdb) == baseline + 1, (
            "pg_stat_activity did not register a connection this test is "
            "holding open, so a leaked one would not register either"
        )
    finally:
        held.close()


# --- the unexpected ending --------------------------------------------------
#
# Decided 2026-09-13, amending the frozen plan: an attempt that ends in a way
# the classifier does not name is caught by a last-clause `except Exception`,
# recorded as `unexpected` with a NULL `raw_content`, and retried like any other
# content failure. A call that ends that way carries `unexpected` too, so its
# row is insertable whatever happened and its earlier attempts are not lost.
#
# The reply that motivates it is valid JSON that is not an object. `json.loads`
# returns a str, list, None or int, `parsed.get` raises AttributeError, and no
# clause in `llm.py` or in `say_move` names that type.

# name -> the reply, so the session id a failure names is stable across runs.
NON_OBJECT_REPLIES = {
    "string": '"e2e4"',   # a bare JSON string — what a terse model really returns
    "list": "[]",
    "null": "null",       # JSON null, which parses to None
    "number": "123",
}


@pytest.mark.parametrize("name", sorted(NON_OBJECT_REPLIES))
def test_a_non_object_reply_is_recorded_as_unexpected_and_retried(
    app_module, pgdb, game, monkeypatch, name
):
    """One unrecognised ending, then a good reply. The call succeeds and both
    attempts are on the record."""
    reply = NON_OBJECT_REPLIES[name]
    session = game(f"log-unexpected-{name}")
    _script(monkeypatch, _says(reply), _says(_json_reply("e2e4")), max_retries=3)

    assert _say(app_module, session).status_code == 200

    call = _calls(pgdb, session["sid"])[0]
    assert call["outcome"] == "ok"
    assert call["attempts"] == 2

    rows = _attempts(pgdb, call["id"])
    assert [(r["attempt"], r["outcome"]) for r in rows] == [
        (1, "unexpected"), (2, "ok"),
    ]
    assert rows[0]["raw_content"] is None, (
        "nothing goes in the text column for an unrecognised ending — the reply "
        "is not known to be safe to keep"
    )
    assert rows[0]["error_detail"], (
        "the row says only that something unrecognised happened; without the "
        "exception type and message it is unactionable"
    )
    assert rows[0]["status_code"] is None, (
        "invariant 16 — the HTTP call succeeded, so there is no status"
    )


def test_a_call_that_only_ever_ends_unexpectedly_is_exhausted(
    app_module, pgdb, game, monkeypatch
):
    """Retried like a content failure means exhausting like one: the player sees
    `llm_bad_response`, 502, the board does not move, and nothing is logged."""
    session = game("log-unexpected-exhausted")
    _script(monkeypatch, *[_says('"e2e4"')] * 3, max_retries=3)

    response = _say(app_module, session)
    assert response.status_code == 502
    assert response.get_json()["error"] == "llm_bad_response"

    assert _calls(pgdb, session["sid"]) == []
    assert _moves(pgdb, session["sid"]) == []
    assert _fen(pgdb, session["sid"]) == START_FEN


def test_an_unexpected_ending_does_not_discard_the_attempts_before_it(
    app_module, pgdb, game, monkeypatch
):
    """The reason the call-level catch-all exists.

    Attempt 1 is cleanly classified, attempt 2 ends unrecognised, attempt 3
    succeeds. Every row must survive: before this, an unset call outcome failed
    the NOT NULL, the shared transaction rolled back, and the correctly recorded
    `bad_json` went with it — one unrecognised ending erasing the history of the
    whole call, not just its own attempt.
    """
    session = game("log-unexpected-history")
    _script(
        monkeypatch,
        _says("not json at all"),
        _says("[]"),
        _says(_json_reply("e2e4")),
        max_retries=3,
    )

    assert _say(app_module, session).status_code == 200

    call = _calls(pgdb, session["sid"])[0]
    assert call["outcome"] == "ok"
    assert call["attempts"] == 3
    assert [(r["attempt"], r["outcome"]) for r in _attempts(pgdb, call["id"])] == [
        (1, "bad_json"), (2, "unexpected"), (3, "ok"),
    ]
