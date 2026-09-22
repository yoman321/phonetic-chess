import json
import os
import time

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from error_logger import logger

# Groq exposes an OpenAI-compatible API at this base URL, so the openai SDK
# works against it unchanged — only api_key and base_url differ.
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
LLM_MODEL = os.environ.get("LLM_MODEL", "qwen/qwen3.8-27b")
LLM_TIMEOUT = int(os.environ.get("LLM_TIMEOUT", "30"))
# qwen3.6-27b was withdrawn from this Groq account between 2026-09-13 and
# 2026-09-16 and now 404s; 3.8 is its successor and was verified live on
# 2026-09-16 (plans/llm-token-optimization/09-model-and-max-tokens.md).
# Qwen is a reasoning model whose thinking mode is on by default. Move picking
# is latency-sensitive (one call per move, inside LLM_TIMEOUT) and reasoning
# tokens count against the rate-limit bucket as output, so default to
# non-thinking mode. Set LLM_REASONING_EFFORT=default to turn thinking back on.
# Ignored by models that don't take the parameter.
LLM_REASONING_EFFORT = os.environ.get("LLM_REASONING_EFFORT", "none")
# Groq rejects a request pre-emptively when the declared output ceiling
# exceeds the per-minute output budget, so the ceiling must be declared: the
# SDK otherwise sends its own default of 2048. Measured replies run 84-102
# visible tokens, and 400 was verified live over 10 moves with no truncation
# (plans/llm-token-optimization/09-model-and-max-tokens.md).
# A truncated reply is invalid JSON and burns all LLM_MAX_RETRIES attempts.
LLM_MAX_TOKENS = int(os.environ.get("LLM_MAX_TOKENS", "400"))
LLM_MAX_RETRIES = min(int(os.environ.get("LLM_MAX_RETRIES", "3")), 3)
LLM_BACKOFF_BASE = float(os.environ.get("LLM_BACKOFF_BASE", "0.5"))
SDK_MAX_RETRIES = 3

# max_retries=3: SDK retries 429s, connection errors, and timeouts with
# exponential backoff (honoring Retry-After when present). After 3 failed
# attempts the original error is raised, which the except blocks below
# convert into TimeoutError so the caller surfaces llm_unavailable.
_client = OpenAI(
    api_key=GROQ_API_KEY,
    base_url=GROQ_BASE_URL,
    timeout=LLM_TIMEOUT,
    max_retries=SDK_MAX_RETRIES,
)


class _BadShapeError(ValueError):
    pass


class CallLog:
    """The database-free record of one invocation and all its attempts."""

    def __init__(self, session_id, ply, fen, player_text, candidates, prior_tone):
        self.session_id = session_id
        self.ply = ply
        self.model = LLM_MODEL
        self.reasoning_effort = LLM_REASONING_EFFORT
        self.max_retries = LLM_MAX_RETRIES
        if not hasattr(_client, "max_retries"):
            # Small compatible client seams may omit configuration attributes.
            _client.max_retries = SDK_MAX_RETRIES
        self.sdk_max_retries = _client.max_retries
        self.fen = fen
        self.player_text = player_text
        self.candidate_ucis = [uci for uci, _san in candidates]
        self.prior_tone = prior_tone or None
        self.attempt_rows = []
        self.outcome = "unexpected"
        self.chosen_uci = None
        self.off_list = None
        self.intent = None
        self.rationale = None
        self.tone_summary = None
        self._started_at = time.monotonic()
        self.latency_ms = 0

    def add_attempt(
        self, attempt, outcome, started_at, sdk_retries=None, status_code=None,
        raw_content=None, error_detail=None, usage=None,
    ):
        details = usage.completion_tokens_details if usage is not None else None
        self.attempt_rows.append({
            "attempt": attempt,
            "outcome": outcome,
            "latency_ms": int((time.monotonic() - started_at) * 1000),
            "sdk_retries": sdk_retries,
            "status_code": status_code,
            "raw_content": raw_content,
            "error_detail": error_detail,
            "prompt_tokens": usage.prompt_tokens if usage is not None else None,
            "completion_tokens": (
                usage.completion_tokens if usage is not None else None
            ),
            "reasoning_tokens": (
                details.reasoning_tokens if details is not None else None
            ),
        })

    def finish(
        self, outcome, chosen_uci=None, off_list=None, intent=None,
        rationale=None, tone_summary=None,
    ):
        self.outcome = outcome
        self.chosen_uci = chosen_uci
        self.off_list = off_list
        self.intent = intent
        self.rationale = rationale
        self.tone_summary = tone_summary
        self.latency_ms = int((time.monotonic() - self._started_at) * 1000)

    def call_record(self):
        return {
            "session_id": self.session_id,
            "ply": self.ply,
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "max_retries": self.max_retries,
            "sdk_max_retries": self.sdk_max_retries,
            "fen": self.fen,
            "player_text": self.player_text,
            "candidate_ucis": self.candidate_ucis,
            "prior_tone": self.prior_tone,
            "outcome": self.outcome,
            "attempts": len(self.attempt_rows),
            "latency_ms": self.latency_ms,
            "chosen_uci": self.chosen_uci,
            "off_list": self.off_list,
            "intent": self.intent,
            "rationale": self.rationale,
            "tone_summary": self.tone_summary,
        }


def _content_outcome(exc):
    if isinstance(exc, json.JSONDecodeError):
        return "bad_json"
    if isinstance(exc, KeyError):
        return "missing_key"
    if isinstance(exc, _BadShapeError):
        return "bad_shape"
    return "invalid_uci"


def build_move_prompt(
    text, fen, candidates, prior_tone_summary="", last_move=None,
):
    """Return the rendered system and user messages for move selection."""
    system = (
        "ROLE: pick a chess move that expresses MSG, read against the running TONE.\n"
        "MAP: aggressive|bold -> captures, checks, sharp threats.\n"
        "     cautious|sad -> quiet developing or retreating.\n"
        "     confident -> solid central. playful -> sideline, surprising.\n"
        "SHIFT: TONE calm and MSG not calm -> the move shows the change.\n"
        "OUT: JSON only, no prose.\n"
        '     {"uci":"<legal uci>","tone_summary":"<=2 sentences, TONE folded with MSG>"}'
    )
    user = json.dumps({
        "TONE": prior_tone_summary or None,
        "LAST": last_move[0] if last_move else None,
        "MSG": text,
        "FEN": fen,
        "CAND": [uci for uci, _san in candidates],
        "RULE": "choose CAND; leave it only if none fits MSG; tone_summary nonempty",
    }, ensure_ascii=False, separators=(",", ":"))
    return system, user


def _pick_move_from_messages(
    system, user, candidates, valid_ucis, on_retry=None, log=None,
    normalize=None, client=None, sleeper=None,
):
    """Run the shared provider, validation, retry, and logging loop."""
    client = client or _client
    sleeper = sleeper or time.sleep
    last_err = None
    for attempt in range(LLM_MAX_RETRIES):
        attempt_started = time.monotonic()
        content = None
        sdk_retries = None
        usage = None
        try:
            raw = client.chat.completions.with_raw_response.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.7,
                max_tokens=LLM_MAX_TOKENS,
                extra_body={
                    "reasoning_effort": LLM_REASONING_EFFORT,
                    "reasoning_format": "hidden",
                },
            )
            sdk_retries = raw.retries_taken
            resp = raw.parse()
            usage = resp.usage
            if not resp.choices:
                raise _BadShapeError("llm response contained no choices")
            content = resp.choices[0].message.content
            if content is None:
                raise _BadShapeError("llm response contained no message content")
            parsed = json.loads(content)
            raw = parsed.get("uci") or ""
            uci = normalize(raw) if normalize is not None else raw.strip()
            tone_summary = (parsed.get("tone_summary") or "").strip()
            if uci is None or uci not in valid_ucis:
                raise ValueError(f"llm returned invalid uci: {raw!r}")
            off_list = uci not in {candidate for candidate, _ in candidates}
            if log is not None:
                log.add_attempt(
                    attempt + 1, "ok", attempt_started,
                    sdk_retries=sdk_retries, raw_content=content, usage=usage,
                )
                log.finish(
                    "ok", chosen_uci=uci, off_list=off_list,
                    tone_summary=tone_summary,
                )
            logger.info(
                "[llm] attempt %d/%d ok  uci=%s off_list=%s",
                attempt + 1, LLM_MAX_RETRIES, uci, off_list,
            )
            return uci, tone_summary
        except (APIConnectionError, APITimeoutError, APIStatusError) as e:
            if log is not None:
                log.add_attempt(
                    attempt + 1, "transport", attempt_started,
                    status_code=getattr(e, "status_code", None),
                    error_detail=f"{type(e).__name__}: {e}",
                )
                log.finish("transport")
            raise TimeoutError(str(e)) from e
        except (json.JSONDecodeError, KeyError, _BadShapeError, ValueError) as e:
            last_err = e
            if log is not None:
                log.add_attempt(
                    attempt + 1, _content_outcome(e), attempt_started,
                    sdk_retries=sdk_retries, raw_content=content,
                    error_detail=f"{type(e).__name__}: {e}", usage=usage,
                )
            logger.info(
                "[llm] attempt %d/%d fail %s: %s  raw=%r",
                attempt + 1, LLM_MAX_RETRIES, type(e).__name__, e, content,
            )
            if attempt + 1 < LLM_MAX_RETRIES:
                if on_retry is not None:
                    try:
                        on_retry(attempt + 1)
                    except Exception:
                        logger.exception("[llm] on_retry callback raised")
                sleeper(LLM_BACKOFF_BASE * (2 ** attempt))
        except Exception as e:
            last_err = ValueError(str(e))
            if log is not None:
                log.add_attempt(
                    attempt + 1, "unexpected", attempt_started,
                    sdk_retries=sdk_retries,
                    error_detail=f"{type(e).__name__}: {e}", usage=usage,
                )
            logger.info(
                "[llm] attempt %d/%d fail %s: %s  raw=None",
                attempt + 1, LLM_MAX_RETRIES, type(e).__name__, e,
            )
            if attempt + 1 < LLM_MAX_RETRIES:
                if on_retry is not None:
                    try:
                        on_retry(attempt + 1)
                    except Exception:
                        logger.exception("[llm] on_retry callback raised")
                sleeper(LLM_BACKOFF_BASE * (2 ** attempt))
    logger.info(
        "[llm] giving up after %d attempts; last_err=%s: %s",
        LLM_MAX_RETRIES, type(last_err).__name__, last_err,
    )
    if log is not None:
        log.finish("exhausted")
    raise last_err


def pick_move_with_llm(
    text, fen, candidates, prior_tone_summary="", last_move=None, valid_ucis=None,
    on_retry=None, log=None, normalize=None, _client_override=None, _sleeper=None,
):
    """Ask Groq to pick a UCI and emit an updated tone summary.

    candidates: list of (uci, san) pairs shown to the LLM as suggested moves
        (typically Sunfish's top-N). Advisory only.
    valid_ucis: set of UCIs the LLM's chosen move must belong to. Defaults to
        just the candidate set (strict). Pass a wider set (e.g. all legal
        moves) to make the candidate list advisory rather than binding.
    normalize: optional callable that receives the raw `uci` JSON value and
        returns a canonical UCI or None. `valid_ucis` is checked afterward.
    prior_tone_summary: rolling summary of the game's tone so far (may be "").
    last_move: (uci, san) of the most recent move played, or None.
    Returns (uci, tone_summary). Retries on bad JSON or
    out-of-set UCI up to LLM_MAX_RETRIES times before raising the last error.
    """
    if valid_ucis is None:
        valid_ucis = {uci for uci, _ in candidates}
    system, user = build_move_prompt(
        text, fen, candidates, prior_tone_summary, last_move,
    )
    return _pick_move_from_messages(
        system, user, candidates, valid_ucis, on_retry=on_retry, log=log,
        normalize=normalize, client=_client_override, sleeper=_sleeper,
    )


def explain_move_with_llm(text, fen, prior_tone, uci, san):
    """Explain a committed move; return (intent, rationale, usage)."""
    system = (
        "A chess move was chosen to express the emotional tone of a player's "
        "message. Given the message, the position, and the move that was played, "
        "write a one-sentence 'intent' describing what the message communicates, "
        "and a one-sentence 'rationale' explaining why that move expresses it. "
        'Reply ONLY with JSON of the form '
        '{"intent": "<one sentence>", "rationale": "<one sentence>"}.'
    )
    user = (
        f"Tone of the game so far: {prior_tone or '(none yet)'}\n"
        f"Player message: {text}\n"
        f"Position FEN before the move: {fen}\n"
        f"Move played: {uci} ({san})"
    )
    last_err = None
    for attempt in range(LLM_MAX_RETRIES):
        try:
            raw = _client.chat.completions.with_raw_response.create(
                model=LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                response_format={"type": "json_object"},
                temperature=0.7,
                max_tokens=LLM_MAX_TOKENS,
                extra_body={
                    "reasoning_effort": LLM_REASONING_EFFORT,
                    "reasoning_format": "hidden",
                },
            )
            resp = raw.parse()
            if not resp.choices or resp.choices[0].message.content is None:
                raise _BadShapeError("llm response contained no message content")
            parsed = json.loads(resp.choices[0].message.content)
            if not isinstance(parsed, dict):
                raise _BadShapeError("llm explanation must be a JSON object")
            intent = parsed["intent"]
            rationale = parsed["rationale"]
            if not all(isinstance(value, str) and value.strip()
                       for value in (intent, rationale)):
                raise _BadShapeError("llm explanation fields must be non-empty strings")
            return intent.strip(), rationale.strip(), resp.usage
        except (APIConnectionError, APITimeoutError, APIStatusError) as e:
            raise TimeoutError(str(e)) from e
        except (ValueError, KeyError) as e:
            last_err = e
            logger.info(
                "[llm explain] attempt %d/%d fail %s: %s",
                attempt + 1, LLM_MAX_RETRIES, type(e).__name__, e,
            )
        except Exception as e:
            last_err = ValueError(str(e))
            logger.info(
                "[llm explain] attempt %d/%d fail %s: %s",
                attempt + 1, LLM_MAX_RETRIES, type(e).__name__, e,
            )
        if attempt + 1 < LLM_MAX_RETRIES:
            time.sleep(LLM_BACKOFF_BASE * (2 ** attempt))
    raise last_err
