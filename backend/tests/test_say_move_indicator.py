"""Invariant 4 — indicator balance.

Every say_move that emits `thinking on:true` emits exactly one `on:false`
afterwards, and that `on:false` is the last thinking event it emits. `on:true`
is deliberately not counted: _on_llm_retry emits one per retry, so a correct
implementation emits up to LLM_MAX_RETRIES of them.

A say_move rejected before it reaches the LLM emits no thinking event at all.
"""
import urllib.error

import pytest

from controller_operations import sessions_ops
from controller_operations.errors import ApiError
from tests.conftest import FakeConnection, FakeCursor, fake_session_row, op_db

AFTER_E4 = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"
STALEMATE_BLACK_TO_MOVE = "7k/5Q2/6K1/8/8/8/8/8 b - - 0 1"

_DEFAULT = object()


def _say(socketio, monkeypatch, llm=None, session_row=_DEFAULT, last_move=None,
         text="go for the throat", token="wtok", fail_on=None):
    cursor = FakeCursor(
        fake_session_row() if session_row is _DEFAULT else session_row,
        last_move,
        fail_on=fail_on,
    )
    if llm is not None:
        monkeypatch.setattr(sessions_ops, "pick_move_with_llm", llm)
    return sessions_ops.say_move(
        op_db(FakeConnection(cursor)), socketio, "sess0001", text, token
    )


def _thinking(socketio):
    return socketio.events("thinking")


def _assert_balanced(socketio):
    events = _thinking(socketio)
    assert len(events) >= 1, "expected at least the `on:true` emit"
    assert [e["on"] for e in events].count(False) == 1
    assert events[-1]["on"] is False


def test_indicator_clears_on_success(socketio, monkeypatch):
    _say(socketio, monkeypatch,
         llm=lambda *a, **k: ("e2e4", "eager", "attack", "because"))
    _assert_balanced(socketio)


def test_indicator_clears_on_mapped_api_error(socketio, monkeypatch):
    def _unreachable(*_a, **_k):
        raise urllib.error.URLError("groq down")

    with pytest.raises(ApiError) as excinfo:
        _say(socketio, monkeypatch, llm=_unreachable)
    assert excinfo.value.code == "llm_unavailable"
    _assert_balanced(socketio)


def test_indicator_clears_on_unexpected_exception(socketio, monkeypatch):
    """A bad GROQ_API_KEY raises AuthenticationError, which no except branch
    maps. RuntimeError stands in for any exception type say_move does not name."""
    def _unmapped(*_a, **_k):
        raise RuntimeError("AuthenticationError: invalid api key")

    with pytest.raises(RuntimeError):
        _say(socketio, monkeypatch, llm=_unmapped)
    _assert_balanced(socketio)


def test_indicator_clears_when_the_move_write_fails(socketio, monkeypatch):
    """The LLM answered and the move is legal, then the DB write throws. Same
    guarantee — this path is past the try/except but still inside the
    transaction."""
    with pytest.raises(RuntimeError):
        _say(socketio, monkeypatch,
             llm=lambda *a, **k: ("e2e4", "eager", "attack", "because"),
             fail_on="INSERT INTO moves")
    _assert_balanced(socketio)


# Every rejection reachable before the `thinking on:true` emit. The last case is
# the one Phase 3 adds; the other nine exist today.
#
# The assertion under test is the thinking-event count, not the exception type,
# so these raise `Exception`: `game_over` cannot construct its ApiError today
# (`ApiError("game_over", 409, status=...)` passes `status` twice and raises
# TypeError). That is a pre-existing bug outside this plan — see BACKLOG.md.
REJECTED_BEFORE_THE_LLM = [
    ("missing_text", {"text": ""}),
    ("missing_token", {"token": None}),
    ("not_found", {"session_row": None}),
    ("game_over", {"session_row": fake_session_row(status="white_won")}),
    ("not_a_player", {"token": "somebody-else"}),
    ("waiting_for_opponent_move_black_first",
     {"token": "btok"}),
    ("waiting_for_opponent_move_moved_last",
     {"token": "wtok", "last_move": {"ply": 1, "uci": "e2e4", "san": "e4",
                                     "tone_summary": None}}),
    ("not_your_turn",
     {"session_row": fake_session_row(fen=AFTER_E4), "token": "wtok",
      "last_move": {"ply": 2, "uci": "e7e5", "san": "e5",
                    "tone_summary": None}}),
    ("no_legal_moves",
     {"session_row": fake_session_row(fen=STALEMATE_BLACK_TO_MOVE),
      "token": "btok",
      "last_move": {"ply": 1, "uci": "f6f7", "san": "Qf7+",
                    "tone_summary": None}}),
    ("waiting_for_opponent_join",
     {"session_row": fake_session_row(black_token=None), "token": "wtok"}),
]


@pytest.mark.parametrize(
    "case", REJECTED_BEFORE_THE_LLM, ids=[c[0] for c in REJECTED_BEFORE_THE_LLM]
)
def test_no_thinking_events_when_rejected_before_the_llm(
    case, socketio, monkeypatch, no_llm
):
    _name, kwargs = case
    with pytest.raises(Exception):
        _say(socketio, monkeypatch, **kwargs)

    assert len(_thinking(socketio)) == 0
    assert len(no_llm) == 0
