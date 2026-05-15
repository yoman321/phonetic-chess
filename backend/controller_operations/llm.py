import json
import os
import time

from openai import APIConnectionError, APITimeoutError, OpenAI, RateLimitError

from error_logger import logger

# Groq exposes an OpenAI-compatible API at this base URL, so the openai SDK
# works against it unchanged — only api_key and base_url differ.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "llama-3.3-70b-versatile")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "30"))
LLM_MAX_RETRIES = min(int(os.environ.get("LLM_MAX_RETRIES", "3")), 3)
LLM_BACKOFF_BASE = float(os.environ.get("LLM_BACKOFF_BASE", "0.5"))

# max_retries=3: SDK retries 429s, connection errors, and timeouts with
# exponential backoff (honoring Retry-After when present). After 3 failed
# attempts the original error is raised, which the except blocks below
# convert into TimeoutError so the caller surfaces llm_unavailable.
_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url=GROQ_BASE_URL,
    timeout=LLM_TIMEOUT,
    max_retries=3,
)


def pick_move_with_llm(
    text, fen, candidates, prior_tone_summary="", last_move=None, valid_ucis=None,
    on_retry=None,
):
    """Ask Groq to pick a UCI and emit an updated tone summary.

    candidates: list of (uci, san) pairs shown to the LLM as suggested moves
        (typically Sunfish's top-N). Advisory only.
    valid_ucis: set of UCIs the LLM's chosen move must belong to. Defaults to
        just the candidate set (strict). Pass a wider set (e.g. all legal
        moves) to make the candidate list advisory rather than binding.
    prior_tone_summary: rolling summary of the game's tone so far (may be "").
    last_move: (uci, san) of the most recent move played, or None.
    Returns (uci, tone_summary, intent, rationale). Retries on bad JSON or
    out-of-set UCI up to LLM_MAX_RETRIES times before raising the last error.
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
        "summary that folds the new message into the running narrative. Also write "
        "a one-sentence 'intent' describing what the new message communicates, and "
        "a one-sentence 'rationale' explaining why the chosen move expresses that "
        "intent. "
        'Reply ONLY with JSON of the form '
        '{"uci": "<one of the legal UCIs>", '
        '"tone_summary": "<updated rolling summary>", '
        '"intent": "<one sentence: what the new message communicates>", '
        '"rationale": "<one sentence: why this move expresses that intent>"}.'
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

    last_err = None
    last_content = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            resp = _client.chat.completions.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.7,
            )
            content = resp.choices[0].message.content
            last_content = content
            parsed = json.loads(content)
            uci = (parsed.get("uci") or "").strip()
            tone_summary = (parsed.get("tone_summary") or "").strip()
            intent = (parsed.get("intent") or "").strip()
            rationale = (parsed.get("rationale") or "").strip()
            if uci not in valid_ucis:
                raise ValueError(f"llm returned invalid uci: {uci!r}")
            logger.info(
                "[llm] attempt %d/%d ok  uci=%s off_list=%s",
                attempt + 1, LLM_MAX_RETRIES, uci,
                uci not in {u for u, _ in candidates},
            )
            return uci, tone_summary, intent, rationale
        except (APIConnectionError, APITimeoutError, RateLimitError) as e:
            # SDK already retried 3 times — surface as TimeoutError so the
            # caller's (URLError, TimeoutError) handler maps it to the
            # llm_unavailable ApiError the frontend knows how to display.
            raise TimeoutError(str(e)) from e
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            last_err = e
            logger.info(
                "[llm] attempt %d/%d fail %s: %s  raw=%r",
                attempt + 1, LLM_MAX_RETRIES, type(e).__name__, e, last_content,
            )
            if attempt + 1 < LLM_MAX_RETRIES:
                if on_retry is not None:
                    try:
                        on_retry(attempt + 1)
                    except Exception:
                        logger.exception("[llm] on_retry callback raised")
                time.sleep(LLM_BACKOFF_BASE * (2 ** attempt))
    logger.info(
        "[llm] giving up after %d attempts; last_err=%s: %s",
        LLM_MAX_RETRIES, type(last_err).__name__, last_err,
    )
    raise last_err
