"""Invariant 8 — both players present.

No move is accepted until both colours are claimed. create_session sets exactly
one token, so a session whose other token is NULL has an invitee who has not
opened the share link yet: their opponent must not be able to spend a Groq call
or advance the board in the meantime.
"""
import pytest

from controller_operations import sessions_ops
from controller_operations.errors import ApiError
from tests.conftest import FakeConnection, FakeCursor, fake_session_row, op_db


def _cursor(**row_overrides):
    return FakeCursor(fake_session_row(**row_overrides))


def _writes(cursor):
    return [sql for sql, _params in cursor.executed
            if sql.startswith(("UPDATE", "INSERT"))]


HALF_CLAIMED = [
    ("black_has_not_joined", {"black_token": None}, "wtok"),
    ("white_has_not_joined", {"white_token": None}, "btok"),
]


@pytest.mark.parametrize(
    "case", HALF_CLAIMED, ids=[c[0] for c in HALF_CLAIMED]
)
def test_say_move_refuses_until_both_players_joined(case, socketio, no_llm):
    _name, row_overrides, token = case
    cursor = _cursor(**row_overrides)

    with pytest.raises(ApiError) as excinfo:
        sessions_ops.say_move(
            op_db(FakeConnection(cursor)), socketio, "sess0001", "attack", token
        )

    assert excinfo.value.code == "waiting_for_opponent_join"
    assert excinfo.value.status == 409
    assert len(no_llm) == 0
    assert _writes(cursor) == []


@pytest.mark.parametrize(
    "case", HALF_CLAIMED, ids=[c[0] for c in HALF_CLAIMED]
)
def test_make_move_refuses_until_both_players_joined(case, socketio):
    _name, row_overrides, token = case
    cursor = _cursor(**row_overrides)

    with pytest.raises(ApiError) as excinfo:
        sessions_ops.make_move(
            op_db(FakeConnection(cursor)), socketio, "sess0001", "e2e4", token
        )

    assert excinfo.value.code == "waiting_for_opponent_join"
    assert excinfo.value.status == 409
    assert _writes(cursor) == []
    assert socketio.events("move") == []


def test_make_move_is_accepted_once_both_players_joined(socketio):
    """Control: the gate must reject only half-claimed sessions."""
    cursor = _cursor()

    payload = sessions_ops.make_move(
        op_db(FakeConnection(cursor)), socketio, "sess0001", "e2e4", "wtok"
    )

    assert payload["san"] == "e4"
    assert payload["ply"] == 1
    assert len(socketio.events("move")) == 1
    assert len(_writes(cursor)) == 2      # UPDATE sessions + INSERT INTO moves


def test_say_move_is_accepted_once_both_players_joined(socketio, monkeypatch):
    """Control, tone path."""
    monkeypatch.setattr(
        sessions_ops, "pick_move_with_llm",
        lambda *a, **k: ("d2d4", "eager", "attack", "because"),
    )
    cursor = _cursor()

    payload = sessions_ops.say_move(
        op_db(FakeConnection(cursor)), socketio, "sess0001", "attack", "wtok"
    )

    assert payload["san"] == "d4"
    assert len(socketio.events("move")) == 1
