"""Offline and Postgres gates for plans/notation-tolerant-moves.md.

The model may spell one legal move several ways.  These gates require one
canonical UCI to cross the rest of the application, while keeping the legal
move set as the final authority.
"""
import ast
import inspect
import json
import pathlib

import chess
import pytest
from openai.types.chat import ChatCompletion, ChatCompletionMessage
from openai.types.chat.chat_completion import Choice

from controller_operations import engine, llm
from tests.conftest import START_FEN


CAPTURE_FEN = "8/8/4k3/3p4/4P3/8/8/4K3 w - - 0 1"
CASTLING_FEN = "r3k2r/8/8/8/8/8/8/R3K2R w KQkq - 0 1"
PROMOTION_FEN = "8/P6k/8/8/8/8/8/7K w - - 0 1"
AMBIGUOUS_FEN = "4k3/8/8/8/8/8/3N3N/4K3 w - - 0 1"


def _completion(content):
    return ChatCompletion(
        id="fake",
        model=llm.LLM_MODEL,
        object="chat.completion",
        created=0,
        choices=[Choice(
            finish_reason="stop",
            index=0,
            message=ChatCompletionMessage(role="assistant", content=content),
        )],
        usage=None,
    )


class _RawResponse:
    def __init__(self, completion):
        self.retries_taken = 0
        self._completion = completion

    def parse(self):
        return self._completion


class _RawCompletions:
    def __init__(self, outer):
        self._outer = outer

    def create(self, **kwargs):
        return _RawResponse(self._outer._next(kwargs))


class _Completions:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def _next(self, kwargs):
        self.calls.append(kwargs)
        assert self.replies, "the model was called more times than scripted"
        return _completion(self.replies.pop(0))

    def create(self, **kwargs):
        return self._next(kwargs)

    @property
    def with_raw_response(self):
        return _RawCompletions(self)


class _Chat:
    def __init__(self, completions):
        self.completions = completions


class _Provider:
    def __init__(self):
        self.max_retries = 2
        self.completions = _Completions([])
        self.chat = _Chat(self.completions)

    @property
    def calls(self):
        return self.completions.calls

    def script(self, *written_moves):
        self.completions.replies = [
            json.dumps({"uci": move, "tone_summary": "steady"})
            for move in written_moves
        ]


@pytest.fixture
def provider(monkeypatch):
    fake = _Provider()
    monkeypatch.setattr(llm, "_client", fake)
    monkeypatch.setattr(llm, "LLM_BACKOFF_BASE", 0.0)
    return fake


def _normalizer(board):
    factory = getattr(engine, "make_move_normalizer", None)
    assert callable(factory), (
        "Phase 1 behavior is missing: engine.make_move_normalizer is not callable"
    )
    return factory(board)


def _pick(board, written, provider, *, valid_ucis=None, normalize=None):
    provider.script(written)
    candidates = [(move.uci(), board.san(move)) for move in board.legal_moves]
    return llm.pick_move_with_llm(
        "play with purpose",
        board.fen(),
        candidates,
        "calm so far",
        None,
        valid_ucis=(
            valid_ucis
            if valid_ucis is not None
            else {move.uci() for move in board.legal_moves}
        ),
        normalize=normalize,
    )


ACCEPTED_NOTATIONS = [
    ("san", START_FEN, "Nf3", "g1f3"),
    ("san-check", CAPTURE_FEN, "exd5+", "e4d5"),
    ("long-pawn", CAPTURE_FEN, "e4-d5", "e4d5"),
    ("long-knight", START_FEN, "Ng1-f3", "g1f3"),
    ("overspecified-knight", START_FEN, "Ng1f3", "g1f3"),
    ("overspecified-pawn", CAPTURE_FEN, "e4xd5", "e4d5"),
    ("uci", START_FEN, "g1f3", "g1f3"),
    ("uci-promotion", PROMOTION_FEN, "a7a8q", "a7a8q"),
    ("castle-letters", CASTLING_FEN, "O-O", "e1g1"),
    ("castle-zeros", CASTLING_FEN, "0-0", "e1g1"),
    ("castle-uci", CASTLING_FEN, "e1g1", "e1g1"),
    ("castle-alternate-uci", CASTLING_FEN, "e1h1", "e1g1"),
    ("promotion-equals", PROMOTION_FEN, "a8=Q", "a7a8q"),
    ("promotion-short", PROMOTION_FEN, "a8Q", "a7a8q"),
    ("promotion-uci-uppercase", PROMOTION_FEN, "a7a8Q", "a7a8q"),
    ("surrounding-space", CAPTURE_FEN, "  exd5  ", "e4d5"),
    ("annotation", CAPTURE_FEN, "exd5!", "e4d5"),
    ("mixed-annotations", CAPTURE_FEN, "exd5?!", "e4d5"),
]


@pytest.mark.parametrize(
    "_name,fen,written,expected", ACCEPTED_NOTATIONS,
    ids=[case[0] for case in ACCEPTED_NOTATIONS],
)
def test_every_planned_notation_is_canonical_on_attempt_one(
    _name, fen, written, expected, provider
):
    """I2: every accepted spelling becomes canonical before validation."""
    board = chess.Board(fen)
    before = board.fen()

    result = _pick(board, written, provider, normalize=_normalizer(board))

    assert result == (expected, "steady")
    assert len(provider.calls) == 1
    assert board.fen() == before, "normalizing a move pushed it onto the board"


INVALID_NOTATIONS = [
    ("empty", START_FEN, ""),
    ("prose", START_FEN, "knight to f3"),
    ("figurine", START_FEN, "♘f3"),
    ("descriptive", START_FEN, "N-KB3"),
    ("wrong-case-upper", START_FEN, "NF3"),
    ("wrong-case-lower", START_FEN, "nf3"),
    ("none", START_FEN, None),
    ("integer", START_FEN, 123),
    ("null-dashes", START_FEN, "--"),
    ("null-z0", START_FEN, "Z0"),
    ("null-zeroes", START_FEN, "0000"),
    ("null-ats", START_FEN, "@@@@"),
]


@pytest.mark.parametrize(
    "_name,fen,written", INVALID_NOTATIONS,
    ids=[case[0] for case in INVALID_NOTATIONS],
)
def test_invalid_case_and_null_spellings_return_none_without_raising(
    _name, fen, written
):
    """I4-I6: bad input is quiet, case stays meaningful, and null stays out."""
    board = chess.Board(fen)
    before = board.fen()

    assert _normalizer(board)(written) is None
    assert board.fen() == before


def test_ambiguous_san_is_rejected_instead_of_guessed():
    """I13: both knights can reach f3, so plain Nf3 has no one answer."""
    board = chess.Board(AMBIGUOUS_FEN)
    assert sorted(
        move.uci() for move in board.legal_moves if move.to_square == chess.F3
    ) == ["d2f3", "h2f3"]
    assert _normalizer(board)("Nf3") is None


def test_valid_ucis_stays_authoritative_after_normalization(provider, monkeypatch):
    """I3: a legal SAN outside the caller's set is retried, not accepted."""
    board = chess.Board()
    normalize = _normalizer(board)
    provider.script("Nf3", "e4")
    monkeypatch.setattr(llm, "LLM_MAX_RETRIES", 2)
    candidates = [("g1f3", "Nf3"), ("e2e4", "e4")]

    result = llm.pick_move_with_llm(
        "play with purpose", board.fen(), candidates, "", None,
        valid_ucis={"e2e4"}, normalize=normalize,
    )

    assert result == ("e2e4", "steady")
    assert len(provider.calls) == 2


def test_none_normalizer_keeps_the_old_uci_and_rejection_paths(
    provider, monkeypatch
):
    """I1 and I11: callers without a normalizer keep their old contract."""
    params = inspect.signature(llm.pick_move_with_llm).parameters
    assert "normalize" in params and params["normalize"].default is None

    board = chess.Board()
    assert _pick(board, "  e2e4  ", provider, normalize=None) == (
        "e2e4", "steady"
    )

    provider.script("e2e5", "e2e5")
    monkeypatch.setattr(llm, "LLM_MAX_RETRIES", 2)
    with pytest.raises(ValueError, match="llm returned invalid uci: 'e2e5'"):
        llm.pick_move_with_llm(
            "play with purpose", board.fen(), [("e2e4", "e4")], "", None,
            valid_ucis={"e2e4"}, normalize=None,
        )
    assert len(provider.calls) == 3


EXPECTED_PROMPT = (
    "ROLE: pick a chess move that expresses MSG, read against the running TONE.\n"
    "MAP: aggressive|bold -> captures, checks, sharp threats.\n"
    "     cautious|sad -> quiet developing or retreating.\n"
    "     confident -> solid central. playful -> sideline, surprising.\n"
    "SHIFT: TONE calm and MSG not calm -> the move shows the change.\n"
    "OUT: JSON only, no prose.\n"
    '     {"uci":"<legal uci>","tone_summary":"<=2 sentences, TONE folded with MSG>"}',
    '{"TONE":"calm so far","LAST":null,"MSG":"press the attack",'
    '"FEN":"rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",'
    '"CAND":["e2e4","g1f3"],"RULE":"choose CAND; leave it only if none fits '
    'MSG; tone_summary nonempty"}',
)


def test_normalizer_seam_keeps_prompt_bytes_and_chess_out_of_llm():
    """I8-I9: the new seam changes validation, not prompts or module roles."""
    for name in ("_pick_move_from_messages", "pick_move_with_llm"):
        params = list(inspect.signature(getattr(llm, name)).parameters.values())
        names = [param.name for param in params]
        assert "normalize" in names, f"{name} has no normalizer seam"
        assert params[names.index("normalize")].default is None
        private_seams = [i for i, value in enumerate(names) if value.startswith("_")]
        if private_seams:
            assert names.index("normalize") < min(private_seams)

    actual = llm.build_move_prompt(
        "press the attack",
        START_FEN,
        [("e2e4", "e4"), ("g1f3", "Nf3")],
        "calm so far",
        None,
    )
    assert actual == EXPECTED_PROMPT

    tree = ast.parse(pathlib.Path(llm.__file__).read_text(encoding="utf-8"))
    chess_imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            chess_imports.extend(
                alias.name for alias in node.names
                if alias.name == "chess" or alias.name.startswith("chess.")
            )
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "chess" or node.module.startswith("chess."):
                chess_imports.append(node.module)
    assert chess_imports == []


@pytest.fixture
def notation_game(make_session, pgdb):
    session = make_session("notation-tolerant")
    yield session
    with pgdb.cursor() as cur:
        cur.execute("DELETE FROM llm_calls WHERE session_id = %s", (session["sid"],))


@pytest.mark.integration
def test_san_retry_persists_only_the_canonical_move(
    app_module, pgdb, notation_game, provider, monkeypatch
):
    """I7 and I10: retry labels and all committed move forms stay exact."""
    bad = json.dumps({"uci": "NF3", "tone_summary": "too loud"})
    rescued = json.dumps({"uci": "Ng1-f3!", "tone_summary": "steady"})
    provider.completions.replies = [bad, rescued]
    monkeypatch.setattr(llm, "LLM_MAX_RETRIES", 2)

    client = app_module.app.test_client()
    response = client.post(
        f"/sessions/{notation_game['sid']}/say",
        json={
            "text": "play with purpose",
            "playerToken": notation_game["white_token"],
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["uci"] == "g1f3"
    assert payload["san"] == "Nf3"

    with pgdb.cursor() as cur:
        cur.execute(
            "SELECT m.uci, m.san, c.id AS call_id, c.chosen_uci, "
            "c.outcome, c.attempts, s.pgn "
            "FROM sessions s "
            "JOIN moves m ON m.session_id = s.id "
            "JOIN llm_calls c USING (session_id, ply) "
            "WHERE s.id = %s",
            (notation_game["sid"],),
        )
        committed = cur.fetchone()
        cur.execute(
            "SELECT attempt, outcome, raw_content "
            "FROM llm_call_attempts WHERE call_id = %s ORDER BY attempt",
            (committed["call_id"],),
        )
        attempts = cur.fetchall()

    assert committed["uci"] == "g1f3"
    assert committed["san"] == "Nf3"
    assert committed["chosen_uci"] == "g1f3"
    assert committed["outcome"] == "ok"
    assert committed["attempts"] == 2
    assert "Nf3" in committed["pgn"].split()
    assert "Ng1-f3!" not in committed["pgn"]
    assert [(row["attempt"], row["outcome"]) for row in attempts] == [
        (1, "invalid_uci"),
        (2, "ok"),
    ]
    assert [row["raw_content"] for row in attempts] == [bad, rescued]
