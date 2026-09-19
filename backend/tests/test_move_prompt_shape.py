"""Invariants 1, 2, 3 and 4's arity — what the move call asks for and returns.

Fast tier: no Postgres, no network. The seam is the module-level client, so a
gate here reads the exact strings that would have gone to Groq.

Invariant 2 is a token count, and only the provider can count tokens. It is
gated twice: once offline against the exact bytes whose removal produces the
measured delta, and once live against `usage.prompt_tokens` behind LLM_LIVE=1.
`plans/llm-token-optimization/05-ab-lazy-explain.md` measured that delta at
exactly 62 tokens, 5 times out of 5 — deterministic, because the two system
prompts differ by a fixed string.
"""
import json
import os

import chess
import pytest

from controller_operations import llm
from controller_operations.engine import ENGINE_TOPN_DEFAULT, rank_moves

# --- the two prompts, verbatim ----------------------------------------------
#
# Copied from plans/llm-token-optimization/scripts/lazy_test.py, which built
# them from llm.py's own strings and measured them. TONE_RULES is the part that
# stays; FULL_TAIL is what shipped; LEAN_TAIL is what this plan ships instead.
# The 62-token input saving is exactly the difference between the two tails.

TONE_RULES = (
    "You are picking a chess move based on the emotional tone of a player's "
    "message AND the running tone of the game so far. Tone signals attitude — "
    "aggressive, defensive, playful, sad, cautious, confident, etc. Match the "
    "move's character to that tone: captures, checks, and sharp threats for "
    "aggressive or bold; quiet developing or retreating moves for cautious or "
    "sad; solid central moves for confident; sideline or surprising moves for "
    "playful. Use the prior tone summary as context — if the game has been calm "
    "and the new message is suddenly aggressive, the shift should show in the "
    "move. After choosing, write a SHORT (one or two sentences) updated tone "
    "summary that folds the new message into the running narrative. "
)
FULL_TAIL = (
    "Also write "
    "a one-sentence 'intent' describing what the new message communicates, and "
    "a one-sentence 'rationale' explaining why the chosen move expresses that "
    "intent. "
    'Reply ONLY with JSON of the form '
    '{"uci": "<one of the legal UCIs>", '
    '"tone_summary": "<updated rolling summary>", '
    '"intent": "<one sentence: what the new message communicates>", '
    '"rationale": "<one sentence: why this move expresses that intent>"}.'
)
LEAN_TAIL = (
    'Reply ONLY with JSON of the form '
    '{"uci": "<one of the legal UCIs>", '
    '"tone_summary": "<updated rolling summary>"}.'
)
SYSTEM_FULL = TONE_RULES + FULL_TAIL
SYSTEM_LEAN = TONE_RULES + LEAN_TAIL

# One fixed position, message and prior tone, so invariant 2's two arms are
# measured on identical inputs and only the prompt differs.
FIXED_FEN = chess.Board().fen()
FIXED_TEXT = "let's go, I'm coming right at you"
FIXED_PRIOR_TONE = "calm and watchful so far"

MIN_INPUT_SAVING = 55        # invariant 2; measured at 62


# --- a client that records instead of calling -------------------------------

class _RawResponse:
    def __init__(self, completion):
        self.retries_taken = 0
        self._completion = completion

    def parse(self):
        return self._completion


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Completion:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = None        # Optional in the SDK; llm.py must tolerate it


class _Recorder:
    """Records every `create` kwargs dict and replies with fixed JSON."""

    def __init__(self, content):
        self.content = content
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return _RawResponse(_Completion(self.content))

    @property
    def with_raw_response(self):
        return self

    @property
    def completions(self):
        return self

    @property
    def chat(self):
        return self


@pytest.fixture
def recorder(monkeypatch):
    rec = _Recorder(json.dumps({"uci": "e2e4", "tone_summary": "eager"}))
    monkeypatch.setattr(llm, "_client", rec)
    monkeypatch.setattr(llm, "LLM_BACKOFF_BASE", 0.0)
    return rec


def _pick(recorder):
    board = chess.Board(FIXED_FEN)
    candidates = rank_moves(board)
    return llm.pick_move_with_llm(
        FIXED_TEXT, FIXED_FEN, candidates, FIXED_PRIOR_TONE,
        valid_ucis={m.uci() for m in board.legal_moves},
    )


def _prompts(recorder):
    assert recorder.calls, "the client was never called"
    messages = recorder.calls[-1]["messages"]
    by_role = {m["role"]: m["content"] for m in messages}
    return by_role["system"], by_role["user"]


# --- invariant 1 ------------------------------------------------------------

def test_the_move_prompt_asks_for_exactly_uci_and_tone_summary(recorder):
    """Invariant 1. Both fields are still named; neither explanation field is.

    Case-insensitive over both messages: the saving is the instruction text, so
    a prompt that merely renames the JSON key still pays for the sentences.
    """
    _pick(recorder)
    system, user = _prompts(recorder)

    assert '"uci"' in system
    assert '"tone_summary"' in system
    for field in ("intent", "rationale"):
        assert field not in system.lower(), (
            f"the move prompt still instructs the model about {field!r}"
        )
        assert field not in user.lower(), (
            f"the move user message still mentions {field!r}"
        )


def test_the_move_prompt_is_the_measured_lean_prompt(recorder):
    """Invariant 2, offline arm.

    Byte-for-byte against the LEAN string that was measured at 62 input tokens
    below FULL. Asserting the count needs the provider; asserting the exact
    bytes that produce it does not, and it is the same claim.
    """
    _pick(recorder)
    system, _user = _prompts(recorder)
    assert system == SYSTEM_LEAN


def test_the_shipped_prompt_is_no_longer_the_full_prompt(recorder):
    """The control. Without it the gate above passes against a prompt nobody
    changed, if LEAN and FULL were ever to be the same string."""
    assert SYSTEM_FULL != SYSTEM_LEAN
    _pick(recorder)
    system, _user = _prompts(recorder)
    assert system != SYSTEM_FULL


# --- invariant 4, arity -----------------------------------------------------

def test_pick_move_with_llm_returns_two_values(recorder):
    """Invariant 4. Four callers unpack this; two is the new contract."""
    result = _pick(recorder)
    assert isinstance(result, tuple)
    assert len(result) == 2, f"expected (uci, tone_summary); got {len(result)} values"
    uci, tone_summary = result
    assert uci == "e2e4"
    assert tone_summary == "eager"


def test_a_model_that_still_sends_explanations_is_accepted(recorder):
    """The extra keys are ignored, not an error: a model is free to over-answer
    and that must not cost the player a move."""
    recorder.content = json.dumps({
        "uci": "e2e4", "tone_summary": "eager",
        "intent": "an intent", "rationale": "a rationale",
    })
    assert _pick(recorder) == ("e2e4", "eager")


# --- invariant 3 ------------------------------------------------------------

def test_the_default_candidate_list_is_eight():
    assert ENGINE_TOPN_DEFAULT == 8


def test_rank_moves_returns_at_most_eight_from_the_start():
    """Invariant 3. The opening has 20 legal moves, so a default of 15 shows."""
    board = chess.Board()
    assert len(list(board.legal_moves)) == 20
    assert len(rank_moves(board)) == 8


def test_rank_moves_returns_every_legal_move_when_fewer_than_eight():
    """Invariant 3's second half: the list is a cap, not a quota."""
    # Black king boxed in by a rook on h1, with only pawn moves and one flight
    # square: seven legal moves, which is fewer than the cap and more than zero.
    board = chess.Board("7k/5ppp/8/8/8/8/8/6KR b - - 0 1")
    legal = sorted(m.uci() for m in board.legal_moves)
    assert 0 < len(legal) < 8
    assert sorted(uci for uci, _san in rank_moves(board)) == legal


# --- invariant 2, live arm --------------------------------------------------

@pytest.mark.skipif(
    not os.environ.get("LLM_LIVE"),
    reason="needs a real Groq call; set LLM_LIVE=1 and GROQ_API_KEY to run",
)
def test_the_lean_prompt_costs_at_least_55_fewer_input_tokens():
    """Invariant 2, measured.

    The only arm that reads `usage.prompt_tokens`. Identical position, message
    and prior tone; the system prompt is the only thing that differs. Skipped
    by default because <test-full> must not depend on a paid external service —
    say so rather than reporting it as passed.
    """
    from openai import OpenAI

    board = chess.Board(FIXED_FEN)
    candidates = rank_moves(board)
    listing = "\n".join(f"- {u} ({s})" for u, s in candidates)
    user = (
        f"Tone of the game so far: {FIXED_PRIOR_TONE}\n"
        "Last move played by the opponent: (no moves yet)\n"
        f"New player message: {FIXED_TEXT}\n"
        f"Position FEN: {FIXED_FEN}\n"
        f"Suggested moves (engine-recommended, uci (san)):\n{listing}\n"
        "Prefer one of the suggested moves — they are sound chess. Only pick "
        "a different legal UCI if no suggestion fits the tone at all. "
        "Always provide an updated tone_summary that reflects how the new "
        "message and the opponent's last move shift the game's mood."
    )
    client = OpenAI(
        api_key=os.environ["GROQ_API_KEY"],
        base_url=llm.GROQ_BASE_URL,
        timeout=60,
        max_retries=3,
    )

    def _prompt_tokens(system):
        resp = client.chat.completions.create(
            model=llm.LLM_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
            max_tokens=llm.LLM_MAX_TOKENS,
            extra_body={
                "reasoning_effort": llm.LLM_REASONING_EFFORT,
                "reasoning_format": "hidden",
            },
        )
        return resp.usage.prompt_tokens

    full = _prompt_tokens(SYSTEM_FULL)
    lean = _prompt_tokens(SYSTEM_LEAN)
    assert full - lean >= MIN_INPUT_SAVING, (
        f"the lean prompt saved {full - lean} input tokens "
        f"({full} -> {lean}); invariant 2 requires at least {MIN_INPUT_SAVING}"
    )


# --- the explain prompt -----------------------------------------------------
#
# The other half of the split. It is gated here, with no database, because it is
# a claim about one function's prompt and return shape; what the route does with
# it is gated in test_lazy_explanations.py.

EXPLAIN_UCI = "e2e4"
EXPLAIN_SAN = "e4"


def _explain(recorder):
    return llm.explain_move_with_llm(
        FIXED_TEXT, FIXED_FEN, FIXED_PRIOR_TONE, EXPLAIN_UCI, EXPLAIN_SAN,
    )


@pytest.fixture
def explaining(monkeypatch):
    rec = _Recorder(json.dumps({"intent": "an intent", "rationale": "a why"}))
    monkeypatch.setattr(llm, "_client", rec)
    monkeypatch.setattr(llm, "LLM_BACKOFF_BASE", 0.0)
    return rec


def test_the_explain_prompt_asks_for_exactly_intent_and_rationale(explaining):
    _explain(explaining)
    system, _user = _prompts(explaining)

    assert '"intent"' in system
    assert '"rationale"' in system
    assert '"uci"' not in system, (
        "the explanation call must not ask for a move; it is given the move"
    )
    assert "tone_summary" not in system


def test_the_explain_prompt_carries_everything_an_explanation_needs(explaining):
    """The message, the position before the move, the prior tone and the move
    that was played. Nothing else — that is what makes this call cheap."""
    _explain(explaining)
    _system, user = _prompts(explaining)

    assert FIXED_TEXT in user
    assert FIXED_FEN in user
    assert FIXED_PRIOR_TONE in user
    assert EXPLAIN_UCI in user
    assert EXPLAIN_SAN in user


def test_explain_move_with_llm_returns_intent_rationale_and_usage(explaining):
    """Three values. `usage` is the best-effort analysis copy; it is returned
    beside the answer, never folded into it."""
    result = _explain(explaining)
    assert isinstance(result, tuple)
    assert len(result) == 3, (
        f"expected (intent, rationale, usage); got {len(result)} values"
    )
    intent, rationale, _usage = result
    assert intent == "an intent"
    assert rationale == "a why"


def test_the_explain_call_uses_the_same_client_settings(explaining):
    """Invariant 10 in spirit: the same client, the same JSON response format
    and the same output ceiling — no second configuration to drift."""
    _explain(explaining)
    kwargs = explaining.calls[-1]

    assert kwargs["model"] == llm.LLM_MODEL
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["max_tokens"] == llm.LLM_MAX_TOKENS
    assert kwargs["extra_body"]["reasoning_effort"] == llm.LLM_REASONING_EFFORT
    assert kwargs["extra_body"]["reasoning_format"] == "hidden"
