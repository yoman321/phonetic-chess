import json
import os
import time
import urllib.request

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:3b")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "30"))
LLM_MAX_RETRIES = min(int(os.environ.get("LLM_MAX_RETRIES", "5")), 5)
LLM_BACKOFF_BASE = float(os.environ.get("LLM_BACKOFF_BASE", "0.5"))


def pick_move_with_llm(
    text, fen, candidates, prior_tone_summary="", last_move=None, valid_ucis=None
):
    """Ask the local Llama model to pick a UCI and emit an updated tone summary.

    candidates: list of (uci, san) pairs shown to the LLM as suggested moves
        (typically Sunfish's top-N). Advisory only.
    valid_ucis: set of UCIs the LLM's chosen move must belong to. Defaults to
        just the candidate set (strict). Pass a wider set (e.g. all legal
        moves) to make the candidate list advisory rather than binding.
    prior_tone_summary: rolling summary of the game's tone so far (may be "").
    last_move: (uci, san) of the most recent move played, or None.
    Returns (uci, tone_summary). Retries on bad JSON or out-of-set UCI up to
    LLM_MAX_RETRIES times before raising the last error.
    """
    moves_listing = "\n".join(f"- {uci} ({san})" for uci, san in candidates)
    if valid_ucis is None:
        valid_ucis = {uci for uci, _ in candidates}
    last_move_str = (
        f"{last_move[0]} ({last_move[1]})" if last_move else "(no moves yet)"
    )

    system = (
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
        'Reply ONLY with JSON of the form '
        '{"uci": "<one of the legal UCIs>", "tone_summary": "<updated summary>"}.'
    )
    user = (
        f"Tone of the game so far: {prior_tone_summary or '(none yet)'}\n"
        f"Last move played by the opponent: {last_move_str}\n"
        f"New player message: {text}\n"
        f"Position FEN: {fen}\n"
        f"Suggested moves (engine-recommended, uci (san)):\n{moves_listing}\n"
        "Prefer one of the suggested moves — they are sound chess. Only pick "
        "a different legal UCI if no suggestion fits the tone at all. "
        "Always provide an updated tone_summary that reflects how the new "
        "message and the opponent's last move shift the game's mood."
    )

    body = json.dumps({
        "model": OLLAMA_MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": "json",
        "stream": False,
        "options": {"temperature": 0.7},
    }).encode()

    last_err = None
    last_content = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            req = urllib.request.Request(
                f"{OLLAMA_URL}/api/chat",
                data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
                reply = json.loads(resp.read())
            content = reply["message"]["content"]
            last_content = content
            parsed = json.loads(content)
            uci = (parsed.get("uci") or "").strip()
            tone_summary = (parsed.get("tone_summary") or "").strip()
            if uci not in valid_ucis:
                raise ValueError(f"llm returned invalid uci: {uci!r}")
            print(
                f"[llm] attempt {attempt+1}/{LLM_MAX_RETRIES} ok  uci={uci} "
                f"off_list={uci not in {u for u, _ in candidates}}"
            )
            return uci, tone_summary
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            last_err = e
            print(
                f"[llm] attempt {attempt+1}/{LLM_MAX_RETRIES} fail "
                f"{type(e).__name__}: {e}  raw={last_content!r}"
            )
            if attempt + 1 < LLM_MAX_RETRIES:
                time.sleep(LLM_BACKOFF_BASE * (2 ** attempt))
    print(
        f"[llm] giving up after {LLM_MAX_RETRIES} attempts; "
        f"last_err={type(last_err).__name__}: {last_err}"
    )
    raise last_err
