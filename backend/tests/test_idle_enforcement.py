"""Invariants 4 and 5 — the read path reports, every write path refuses.

That split is what lets the client show the final position with a modal over it:
`GET /sessions/:id` keeps returning 200 and the position, and gains an `ended`
boolean; `/join`, `/move` and `/say` all raise `game_ended` with a 410. Gating
the two move paths is what closes the hole where a client with a stored token
and no socket keeps playing a game that has ended.

Games are pushed past the deadline by backdating the row — see
`test_idle_join_refusal.py` for why not by lowering the constant.
"""
import json

import pytest

pytestmark = pytest.mark.integration

PAST_THE_WINDOW = 7200          # conftest pins IDLE_TTL_SECONDS to 3600


def _backdate(pgdb, sid, seconds):
    with pgdb.cursor() as cur:
        cur.execute(
            "UPDATE sessions SET created_at = NOW() - make_interval(secs => %s) "
            "WHERE id = %s",
            (seconds, sid),
        )


def _set_status(pgdb, sid, status):
    with pgdb.cursor() as cur:
        cur.execute("UPDATE sessions SET status = %s WHERE id = %s", (status, sid))


def _board(pgdb, sid):
    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT fen, pgn, status, updated_at FROM sessions WHERE id = %s", (sid,)
        )
        return cur.fetchone()


def test_get_on_an_idle_game_returns_its_final_position_and_says_ended(
    app_module, pgdb, make_session
):
    """Invariant 4.

    The token assertion is not decoration. This work adds a third computed
    column to `select_session_summary`, whose docstring promises the endpoint
    never returns token values and which still has no general gate of its own.
    """
    session = make_session("ended-get")
    sid = session["sid"]
    _backdate(pgdb, sid, PAST_THE_WINDOW)
    stored = _board(pgdb, sid)

    response = app_module.app.test_client().get(f"/sessions/{sid}")

    assert response.status_code == 200
    body = response.get_json()
    assert body["ended"] is True
    assert body["fen"] == stored["fen"]

    assert "white_token" not in body
    assert "black_token" not in body
    serialised = json.dumps(body)
    assert session["white_token"] not in serialised
    assert session["black_token"] not in serialised


def test_get_on_a_live_game_says_it_has_not_ended(app_module, pgdb, make_session):
    """Control. Without it, `ended` hard-coded to True would pass the gate above."""
    session = make_session("live-get")

    response = app_module.app.test_client().get(f"/sessions/{session['sid']}")

    assert response.status_code == 200
    assert response.get_json()["ended"] is False


@pytest.mark.parametrize(
    "path, payload",
    [
        ("join", {}),
        ("move", {"uci": "e2e4"}),
        ("say", {"text": "attack"}),
    ],
    ids=["join", "move", "say"],
)
def test_every_write_path_refuses_an_idle_game(
    app_module, pgdb, make_session, no_llm, path, payload
):
    """Invariant 5.

    410 is the honest code — the game was here and is not coming back. A
    returning player and a new joiner are refused alike, which is why `/join`
    sends the stored token rather than none.
    """
    session = make_session(f"ended-{path}")
    sid = session["sid"]
    _backdate(pgdb, sid, PAST_THE_WINDOW)
    before = _board(pgdb, sid)

    response = app_module.app.test_client().post(
        f"/sessions/{sid}/{path}",
        json={**payload, "playerToken": session["white_token"]},
    )

    assert response.status_code == 410, (
        f"/{path} answered {response.status_code}: {response.get_data(as_text=True)}"
    )
    assert response.get_json()["error"] == "game_ended"
    assert _board(pgdb, sid) == before
    assert no_llm == [], "the phrase reached the LLM before being refused"


def test_the_idle_deadline_is_reported_before_game_over(
    app_module, pgdb, make_session, no_llm
):
    """The precedence the plan fixes, recorded rather than argued.

    A game past its deadline is unreachable however it finished, so the deadline
    is the more useful answer. It also keeps the new path clear of a broken one:
    `ApiError("game_over", 409, status=row["status"])` (`sessions_ops.py:137`)
    passes `status` positionally and by keyword, raises `TypeError`, and reaches
    the client as a 500 with no code at all. That is a known backlog entry and is
    deliberately not fixed here — only ordered behind `game_ended`.
    """
    session = make_session("ended-and-won")
    sid = session["sid"]
    _set_status(pgdb, sid, "white_won")
    _backdate(pgdb, sid, PAST_THE_WINDOW)

    response = app_module.app.test_client().post(
        f"/sessions/{sid}/move",
        json={"uci": "e2e4", "playerToken": session["white_token"]},
    )

    assert response.status_code == 410
    assert response.get_json()["error"] == "game_ended"
