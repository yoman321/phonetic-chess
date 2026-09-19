"""Invariants 5-13 — the on-demand explanation, and where it may read from.

Drives the real HTTP routes against a real Postgres, then reads the rows back on
a second connection. The seam is the OpenAI client object, shared with
`test_llm_call_log.py` so one script covers both the move call and the explain
call and the gates can count LLM calls exactly.

The line this file exists to hold: `moves` is game storage and the product may
read it; `llm_calls`, `llm_call_attempts` and `llm_call_metrics` are a personal
analysis record and the product may never read them. Several gates delete or
break the analysis tables and then require the feature to work anyway.

Two names the plan describes but does not spell:
  - the explain response carries `intent` and `rationale`, the same keys the
    move payload used to carry;
  - a move with no saved context answers `explanation_unavailable`.
Both are assumptions recorded in handoff.md, not decisions taken here.

Nothing here imports `queries.llm_calls` or `queries.moves` (invariant 13): a
gate asserts what a row is, so it reads the row back.
"""
import contextlib
import json

import psycopg
import pytest

from controller_operations import llm
from tests.conftest import START_FEN
from tests.test_llm_call_log import (
    _connection_error,
    _explain_reply,
    _json_reply,
    _raises,
    _says,
    _script,
)

pytestmark = pytest.mark.integration

MESSAGE = "come out swinging"
INTENT = "The player is announcing an all-out attack."
RATIONALE = "Pushing the king's pawn two squares seizes the centre at once."


# --- reading the tables back ------------------------------------------------

def _moves(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT * FROM moves WHERE session_id = %s ORDER BY ply", (sid,)
        )
        return cur.fetchall()


def _calls(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT * FROM llm_calls WHERE session_id = %s ORDER BY id", (sid,)
        )
        return cur.fetchall()


def _session(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute("SELECT * FROM sessions WHERE id = %s", (sid,))
        return cur.fetchone()


def _columns(pgdb, table):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_name = %s",
            (table,),
        )
        return {r["column_name"]: r for r in cur.fetchall()}


@contextlib.contextmanager
def _writes_fail(pgdb, table):
    """Make every INSERT and UPDATE on `table` raise, from inside Postgres.

    INSERT *and* UPDATE: the request counter is an UPDATE, and a gate that only
    breaks inserts would let a failing counter through untested.
    """
    fn = f"pc_test_fail_w_{table}"
    with pgdb.cursor() as cur:
        cur.execute(
            f"CREATE OR REPLACE FUNCTION {fn}() RETURNS TRIGGER AS $$ "
            f"BEGIN RAISE EXCEPTION 'forced failure writing {table}'; "
            f"END; $$ LANGUAGE plpgsql"
        )
        cur.execute(
            f"CREATE TRIGGER trg_{fn} BEFORE INSERT OR UPDATE ON {table} "
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
    """A session whose analysis rows are cleaned up too."""
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


# --- driving the routes -----------------------------------------------------

def _say(app_module, session, text=MESSAGE, token=None):
    return app_module.app.test_client().post(
        f"/sessions/{session['sid']}/say",
        json={"text": text, "playerToken": token or session["white_token"]},
    )


def _move(app_module, session, uci="e2e4", token=None):
    return app_module.app.test_client().post(
        f"/sessions/{session['sid']}/move",
        json={"uci": uci, "playerToken": token or session["white_token"]},
    )


def _explain(app_module, session, ply=1, token=None):
    return app_module.app.test_client().post(
        f"/sessions/{session['sid']}/moves/{ply}/explain",
        json={"playerToken": token or session["white_token"]},
    )


def _prompts(client):
    """Every (system, user) pair the scripted client was handed, in order."""
    return [
        (
            {m["role"]: m["content"] for m in kwargs["messages"]}["system"],
            {m["role"]: m["content"] for m in kwargs["messages"]}["user"],
        )
        for kwargs in client.completions.kwargs
    ]


# --- storage: the columns the product owns ----------------------------------

MOVES_PRODUCT_COLUMNS = {
    "player_text": ("text", "YES"),
    "pre_move_fen": ("text", "YES"),
    "prior_tone": ("text", "YES"),
    "intent": ("text", "YES"),
    "rationale": ("text", "YES"),
}


def test_moves_carries_the_product_context_and_cache(pgdb):
    """Invariant 12. Nullable, because a manual move has no message and old
    rows are never backfilled."""
    found = _columns(pgdb, "moves")
    assert found, "moves does not exist"
    for name, (data_type, nullable) in MOVES_PRODUCT_COLUMNS.items():
        col = found.get(name)
        assert col is not None, f"moves.{name} is missing"
        assert (col["data_type"], col["is_nullable"]) == (data_type, nullable), (
            f"moves.{name} is {(col['data_type'], col['is_nullable'])}"
        )


LLM_CALLS_EXPLAIN_COLUMNS = {
    "explain_requests": ("integer", "NO"),
    "explain_prompt_tokens": ("integer", "YES"),
    "explain_completion_tokens": ("integer", "YES"),
    "explain_latency_ms": ("integer", "YES"),
}


def test_the_analysis_table_carries_the_explain_copies(pgdb):
    """Invariant 11's storage. Best-effort copies, not product data."""
    found = _columns(pgdb, "llm_calls")
    for name, (data_type, nullable) in LLM_CALLS_EXPLAIN_COLUMNS.items():
        col = found.get(name)
        assert col is not None, f"llm_calls.{name} is missing"
        assert (col["data_type"], col["is_nullable"]) == (data_type, nullable), (
            f"llm_calls.{name} is {(col['data_type'], col['is_nullable'])}"
        )
    assert "0" in (found["explain_requests"]["column_default"] or ""), (
        "explain_requests must default to 0; a NULL there makes every count a "
        "special case"
    )


# --- invariant 12: context is written with the move -------------------------

def test_an_llm_move_stores_its_context_with_the_move(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 12. Everything an explanation needs is in `moves`, written in
    the move's own transaction."""
    session = game("lazy-context")
    _script(monkeypatch, _says(_json_reply("e2e4", tone="fired up")))

    assert _say(app_module, session).status_code == 200

    rows = _moves(pgdb, session["sid"])
    assert len(rows) == 1
    row = rows[0]
    assert row["uci"] == "e2e4"
    assert row["player_text"] == MESSAGE
    assert row["pre_move_fen"] == START_FEN
    assert row["prior_tone"] in (None, ""), "the first move has no prior tone"
    assert row["intent"] is None, "nothing is cached until somebody asks"
    assert row["rationale"] is None


def test_the_second_llm_move_stores_the_tone_it_was_given(
    app_module, pgdb, game, monkeypatch
):
    """The prior-tone column, on the first move that actually has one."""
    session = game("lazy-prior-tone")
    _script(
        monkeypatch,
        _says(_json_reply("e2e4", tone="fired up")),
        _says(_json_reply("e7e5", tone="answering in kind")),
    )
    assert _say(app_module, session).status_code == 200
    assert _say(
        app_module, session, text="right back at you",
        token=session["black_token"],
    ).status_code == 200

    rows = _moves(pgdb, session["sid"])
    assert [r["ply"] for r in rows] == [1, 2]
    assert rows[1]["prior_tone"] == "fired up"
    assert rows[1]["player_text"] == "right back at you"
    assert rows[1]["pre_move_fen"] != START_FEN


def test_a_manual_move_leaves_the_context_columns_null(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 12. The typed-move path has no message, so it stores none —
    and, having no context, is not an eligible move for `?`."""
    session = game("lazy-manual")
    _script(monkeypatch)                    # any LLM call at all is a failure

    assert _move(app_module, session).status_code == 200

    row = _moves(pgdb, session["sid"])[0]
    assert row["player_text"] is None
    assert row["pre_move_fen"] is None
    assert row["intent"] is None
    assert row["rationale"] is None


def test_the_context_is_stored_even_when_the_analysis_write_fails(
    app_module, pgdb, game, monkeypatch
):
    """Invariants 11 and 12 together, and the whole reason the columns moved.

    The analysis tables are unwritable. The move still commits, and it still
    carries everything an explanation needs. A design that kept the context in
    `llm_calls` loses it here — silently, and forever.
    """
    session = game("lazy-context-log-down")
    _script(monkeypatch, _says(_json_reply("e2e4")))

    with _writes_fail(pgdb, "llm_calls"):
        assert _say(app_module, session).status_code == 200

    row = _moves(pgdb, session["sid"])[0]
    assert row["player_text"] == MESSAGE
    assert row["pre_move_fen"] == START_FEN
    assert _calls(pgdb, session["sid"]) == []


# --- invariants 5 and 6: generate once, then reuse --------------------------

def _played(app_module, pgdb, game, monkeypatch, name, *extra_steps):
    """One committed LLM move, with the rest of the script left for explains."""
    session = game(name)
    client = _script(monkeypatch, _says(_json_reply("e2e4")), *extra_steps)
    assert _say(app_module, session).status_code == 200
    assert len(_moves(pgdb, session["sid"])) == 1
    return session, client


def test_the_first_explain_generates_and_stores_the_answer(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 5."""
    session, client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-first",
        _says(_explain_reply(INTENT, RATIONALE)),
    )
    before = client.completions.calls

    response = _explain(app_module, session)

    assert response.status_code == 200
    body = response.get_json()
    assert body["intent"] == INTENT
    assert body["rationale"] == RATIONALE
    assert client.completions.calls == before + 1, (
        "the explanation was not generated by an LLM call"
    )

    row = _moves(pgdb, session["sid"])[0]
    assert row["intent"] == INTENT
    assert row["rationale"] == RATIONALE


def test_a_second_explain_makes_no_llm_call(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 6. The script holds exactly one explain step, so a second
    generation raises inside the scripted client rather than passing quietly."""
    session, client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-cached",
        _says(_explain_reply(INTENT, RATIONALE)),
    )
    assert _explain(app_module, session).status_code == 200
    after_first = client.completions.calls

    response = _explain(app_module, session)

    assert response.status_code == 200
    assert response.get_json()["intent"] == INTENT
    assert response.get_json()["rationale"] == RATIONALE
    assert client.completions.calls == after_first, (
        f"a cached explanation cost {client.completions.calls - after_first} "
        "more LLM call(s); invariant 6 requires zero"
    )


def test_the_explain_prompt_carries_the_saved_context(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 12's sharpest edge: the answer is built from the move's own
    saved context, not from the current board and not from a log row.

    The analysis rows are deleted before the explain, so anything the prompt
    knows it learned from `moves`.
    """
    session, client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-prompt",
        _says(_explain_reply(INTENT, RATIONALE)),
    )
    with pgdb.cursor() as cur:
        cur.execute(
            "DELETE FROM llm_calls WHERE session_id = %s", (session["sid"],)
        )
    assert _calls(pgdb, session["sid"]) == []

    assert _explain(app_module, session).status_code == 200

    system, user = _prompts(client)[-1]
    assert "intent" in system.lower() and "rationale" in system.lower()
    assert MESSAGE in user, "the explain prompt lost the player's message"
    assert START_FEN in user, (
        "the explain prompt did not use the position before the move"
    )
    assert "e2e4" in user and "e4" in user


def test_a_move_with_no_saved_context_is_unavailable_not_read_from_the_log(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 12's other edge.

    An old move has NULL context and an analysis row that still holds the
    message and the position. Reading that row would answer the request; the
    plan forbids it. The request must fail instead, and no LLM call may be made
    on context the product is not allowed to have.
    """
    session = game("lazy-no-context")
    with pgdb.cursor() as cur:
        cur.execute(
            "INSERT INTO moves (session_id, ply, uci, san) VALUES (%s, 1, %s, %s)",
            (session["sid"], "e2e4", "e4"),
        )
        cur.execute(
            "INSERT INTO llm_calls (session_id, ply, model, reasoning_effort, "
            "max_retries, sdk_max_retries, fen, player_text, candidate_ucis, "
            "outcome, attempts, latency_ms, chosen_uci) VALUES "
            "(%s, 1, 'm', 'none', 3, 3, %s, %s, %s, 'ok', 1, 10, 'e2e4')",
            (session["sid"], START_FEN, MESSAGE, ["e2e4"]),
        )
    client = _script(monkeypatch)            # any LLM call at all is a failure

    response = _explain(app_module, session)

    assert response.status_code >= 400
    assert response.get_json()["error"] == "explanation_unavailable"
    assert client.completions.calls == 0
    row = _moves(pgdb, session["sid"])[0]
    assert row["intent"] is None and row["rationale"] is None


# --- invariant 7: the request counter is attempted, never depended on -------

def test_every_explain_request_attempts_the_request_counter(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 7. Cache hits count too — a counter that only sees misses
    cannot answer how often the panel is opened."""
    session, _client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-counter",
        _says(_explain_reply(INTENT, RATIONALE)),
    )
    assert _calls(pgdb, session["sid"])[0]["explain_requests"] == 0

    assert _explain(app_module, session).status_code == 200
    assert _calls(pgdb, session["sid"])[0]["explain_requests"] == 1

    assert _explain(app_module, session).status_code == 200
    assert _calls(pgdb, session["sid"])[0]["explain_requests"] == 2, (
        "the cache hit was not counted"
    )


def test_an_explain_works_with_no_analysis_row_at_all(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 7's second half. Nothing to increment is not an error."""
    session, _client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-no-row",
        _says(_explain_reply(INTENT, RATIONALE)),
    )
    with pgdb.cursor() as cur:
        cur.execute(
            "DELETE FROM llm_calls WHERE session_id = %s", (session["sid"],)
        )

    response = _explain(app_module, session)

    assert response.status_code == 200
    assert response.get_json()["intent"] == INTENT
    assert _moves(pgdb, session["sid"])[0]["intent"] == INTENT


def test_an_explain_works_when_the_analysis_table_is_unwritable(
    app_module, pgdb, game, monkeypatch
):
    """Invariants 7 and 11. The counter and the usage copy both fail; the
    player's answer arrives anyway and is cached in game storage."""
    session, _client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-log-down",
        _says(_explain_reply(INTENT, RATIONALE)),
    )

    with _writes_fail(pgdb, "llm_calls"):
        response = _explain(app_module, session)

    assert response.status_code == 200
    assert response.get_json()["rationale"] == RATIONALE
    assert _moves(pgdb, session["sid"])[0]["rationale"] == RATIONALE


def test_the_explain_usage_is_copied_to_the_analysis_row(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 11. Best-effort, but when it is there it is the real number."""
    session, _client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-usage",
        _says(_explain_reply(INTENT, RATIONALE), prompt_tokens=266,
              completion_tokens=70),
    )

    assert _explain(app_module, session).status_code == 200

    call = _calls(pgdb, session["sid"])[0]
    assert call["explain_prompt_tokens"] == 266
    assert call["explain_completion_tokens"] == 70
    assert call["explain_latency_ms"] is not None
    assert call["explain_latency_ms"] >= 0


# --- invariant 8: a failed explanation changes nothing ----------------------

def _unchanged_game(pgdb, session, before_fen, before_pgn):
    row = _session(pgdb, session["sid"])
    assert row["fen"] == before_fen
    assert row["pgn"] == before_pgn
    moves = _moves(pgdb, session["sid"])
    assert len(moves) == 1, "an explanation must never add a move"
    assert moves[0]["intent"] is None
    assert moves[0]["rationale"] is None


def test_an_exhausted_explain_leaves_the_game_alone(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 8. Three unusable replies: the panel gets an error and the
    board, the history and the cache are untouched."""
    session, _client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-explain-exhausted",
        _says("not json at all"), _says("not json at all"),
        _says("not json at all"),
    )
    row = _session(pgdb, session["sid"])
    before_fen, before_pgn = row["fen"], row["pgn"]

    response = _explain(app_module, session)

    assert response.status_code == 502
    assert response.get_json()["error"] == "llm_bad_response"
    _unchanged_game(pgdb, session, before_fen, before_pgn)


def test_an_unreachable_explain_maps_to_llm_unavailable(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 8, transport arm — the same mapping the move path uses."""
    session, _client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-explain-transport",
        _raises(_connection_error()),
    )
    row = _session(pgdb, session["sid"])
    before_fen, before_pgn = row["fen"], row["pgn"]

    response = _explain(app_module, session)

    assert response.status_code == 502
    assert response.get_json()["error"] == "llm_unavailable"
    _unchanged_game(pgdb, session, before_fen, before_pgn)


def test_a_failed_explain_can_be_retried_and_then_succeeds(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 8's tail: a failure caches nothing, so the next press works."""
    session, _client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-explain-retry",
        _raises(_connection_error()),
        _says(_explain_reply(INTENT, RATIONALE)),
    )
    assert _explain(app_module, session).status_code == 502

    response = _explain(app_module, session)

    assert response.status_code == 200
    assert response.get_json()["intent"] == INTENT
    assert _moves(pgdb, session["sid"])[0]["intent"] == INTENT


# --- who may ask ------------------------------------------------------------

def test_either_player_may_explain_a_move(
    app_module, pgdb, game, monkeypatch
):
    """The mover's opponent presses `?` too, and the mover presses it on their
    own move. Neither is a turn, so no turn check applies."""
    session, client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-both-players",
        _says(_explain_reply(INTENT, RATIONALE)),
    )
    mine = _explain(app_module, session, token=session["white_token"])
    theirs = _explain(app_module, session, token=session["black_token"])

    assert mine.status_code == 200
    assert theirs.status_code == 200
    assert theirs.get_json()["intent"] == INTENT


def test_a_stranger_cannot_explain_a_move(app_module, pgdb, game, monkeypatch):
    session, client = _played(
        app_module, pgdb, game, monkeypatch, "lazy-stranger",
    )
    before = client.completions.calls

    response = _explain(app_module, session, token="somebody-else")

    assert response.status_code == 403
    assert response.get_json()["error"] == "not_a_player"
    assert client.completions.calls == before, "a stranger spent an LLM call"


def test_an_explain_without_a_token_is_refused(
    app_module, pgdb, game, monkeypatch
):
    session, client = _played(app_module, pgdb, game, monkeypatch, "lazy-no-token")
    before = client.completions.calls

    response = app_module.app.test_client().post(
        f"/sessions/{session['sid']}/moves/1/explain", json={}
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "missing_token"
    assert client.completions.calls == before


def test_an_explain_for_a_ply_that_was_never_played_is_not_found(
    app_module, pgdb, game, monkeypatch
):
    session, client = _played(app_module, pgdb, game, monkeypatch, "lazy-no-ply")
    before = client.completions.calls

    response = _explain(app_module, session, ply=99)

    assert response.status_code == 404
    assert response.get_json()["error"] == "not_found"
    assert client.completions.calls == before


def test_an_explain_for_an_unknown_session_is_not_found(
    app_module, monkeypatch
):
    client = _script(monkeypatch)
    response = app_module.app.test_client().post(
        "/sessions/no-such-session/moves/1/explain",
        json={"playerToken": "whatever"},
    )
    assert response.status_code == 404
    assert response.get_json()["error"] == "not_found"
    assert client.completions.calls == 0


# --- the payload no longer carries the explanation --------------------------

def test_the_move_payload_has_no_inline_explanation(
    app_module, pgdb, game, monkeypatch
):
    """Invariant 4 and Phase 1. `priorTone` stays — invariant 9 renders it with
    no fetch — but the two generated fields are gone."""
    session = game("lazy-payload")
    _script(monkeypatch, _says(_json_reply("e2e4", tone="fired up")))

    body = json.loads(_say(app_module, session).data)

    assert body["ply"] == 1
    assert body["uci"] == "e2e4"
    assert body["tone_summary"] == "fired up"
    assert "priorTone" in body
    assert "intent" not in body
    assert "rationale" not in body
