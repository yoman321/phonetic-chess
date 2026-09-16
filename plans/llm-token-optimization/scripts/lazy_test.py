"""Inline vs lazy explanation: measure both on identical positions.

Plays a game using a LEAN move prompt (uci + tone_summary only). At the start,
middle and end of that game it additionally runs, on the same position:
  A) the FULL prompt as shipped today (uci + tone_summary + intent + rationale)
  B) a LAZY explain call that produces intent + rationale after the fact

Nothing in the repo is modified; the prompt variants are built here from the
same strings llm.py uses.
"""
import json
import os
import sys
import time

from dotenv import load_dotenv

load_dotenv("backend/.env")
sys.path.insert(0, "backend")

import chess
from openai import OpenAI

from controller_operations.engine import rank_moves

MODEL = "openai/gpt-oss-20b"
EFFORT = "low"
client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
    timeout=60,
    max_retries=3,
)

# --- the shipped system prompt, verbatim from llm.py -------------------------
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

# --- the lazy explain prompt: only what an explanation actually needs --------
SYSTEM_EXPLAIN = (
    "A chess move was chosen to express the emotional tone of a player's "
    "message. Given the message, the position, and the move that was played, "
    "write a one-sentence 'intent' describing what the message communicates, "
    "and a one-sentence 'rationale' explaining why that move expresses it. "
    'Reply ONLY with JSON of the form '
    '{"intent": "<one sentence>", "rationale": "<one sentence>"}.'
)


def move_user(text, fen, candidates, prior_tone, last_move):
    listing = "\n".join(f"- {u} ({s})" for u, s in candidates)
    lm = f"{last_move[0]} ({last_move[1]})" if last_move else "(no moves yet)"
    return (
        f"Tone of the game so far: {prior_tone or '(none yet)'}\n"
        f"Last move played by the opponent: {lm}\n"
        f"New player message: {text}\n"
        f"Position FEN: {fen}\n"
        f"Suggested moves (engine-recommended, uci (san)):\n{listing}\n"
        "Prefer one of the suggested moves — they are sound chess. Only pick "
        "a different legal UCI if no suggestion fits the tone at all. "
        "Always provide an updated tone_summary that reflects how the new "
        "message and the opponent's last move shift the game's mood."
    )


def explain_user(text, fen, prior_tone, uci, san):
    return (
        f"Tone of the game so far: {prior_tone or '(none yet)'}\n"
        f"Player message: {text}\n"
        f"Position FEN before the move: {fen}\n"
        f"Move played: {uci} ({san})"
    )


def call(system, user):
    t0 = time.monotonic()
    raw = client.chat.completions.with_raw_response.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        response_format={"type": "json_object"},
        temperature=0.7,
        extra_body={"reasoning_effort": EFFORT, "reasoning_format": "hidden"},
    )
    ms = int((time.monotonic() - t0) * 1000)
    resp = raw.parse()
    u = resp.usage
    d = u.completion_tokens_details
    return {
        "ms": ms,
        "retries": raw.retries_taken,
        "in": u.prompt_tokens,
        "out": u.completion_tokens,
        "reason": getattr(d, "reasoning_tokens", None) or 0,
        "content": resp.choices[0].message.content,
    }


MESSAGES = [
    "let's go, I'm coming right at you", "hmm, I need to be careful here",
    "ha! you didn't see that coming", "I'm feeling nervous about this",
    "time to attack, no more waiting", "just quietly developing",
    "I'm going to crush you now", "ugh this is getting complicated",
    "playful little sidestep for you", "confident and centred",
    "getting desperate now", "calm, patient, I can wait",
    "sharp threat incoming", "defending carefully, no risks",
    "bold sacrifice, let's see", "steady as she goes",
    "aggressive push down the flank", "cautious retreat to regroup",
    "one more strike and it's over", "resigned but still fighting",
]

PRICE_IN, PRICE_OUT = 0.075 / 1e6, 0.30 / 1e6


def usd(r):
    return r["in"] * PRICE_IN + r["out"] * PRICE_OUT


board = chess.Board()
tone = ""
last = None
lean_rows = []
san_rescues = []
checkpoints = {}          # label -> dict(full=, lazy=, lean=)
CHECK_AT = {1: "start", 10: "middle", 20: "end"}

for i, msg in enumerate(MESSAGES):
    cands = rank_moves(board)
    if not cands:
        break
    legal = {m.uci() for m in board.legal_moves}
    fen_before, tone_before = board.fen(), tone
    user_lean = move_user(msg, fen_before, cands, tone_before, last)

    # --- the lean move call (what a lazy design would ship) ---
    chosen = None
    for _ in range(3):
        r = call(SYSTEM_LEAN, user_lean)
        try:
            p = json.loads(r["content"])
        except json.JSONDecodeError:
            continue
        raw_uci = (p.get("uci") or "").strip()
        if raw_uci in legal:
            chosen = r, p, False
            break
        # The model answered in standard algebraic notation instead of UCI.
        # Converting it is exactly the "accept SAN" lever; count the rescues.
        try:
            mv = board.parse_san(raw_uci)
        except (ValueError, chess.IllegalMoveError, chess.InvalidMoveError):
            continue
        p["uci"] = mv.uci()
        chosen = r, p, True
        break
    if chosen is None:
        print(f"ply {i+1}: no legal move after 3 tries; stopping")
        break
    r_lean, parsed, was_san = chosen
    lean_rows.append(r_lean)
    san_rescues.append(was_san)
    uci = parsed["uci"].strip()
    tone = (parsed.get("tone_summary") or "").strip()
    san = board.san(chess.Move.from_uci(uci))

    label = CHECK_AT.get(i + 1)
    if label:
        # --- same position, full prompt as shipped today ---
        r_full = call(SYSTEM_FULL, move_user(msg, fen_before, cands, tone_before, last))
        # --- same position, lazy explanation after the fact ---
        r_lazy = call(SYSTEM_EXPLAIN, explain_user(msg, fen_before, tone_before, uci, san))
        checkpoints[label] = {
            "ply": i + 1, "lean": r_lean, "full": r_full, "lazy": r_lazy,
            "msg": msg, "uci": uci, "san": san,
        }
        print(f"[checkpoint {label}] ply {i+1} {san}", flush=True)

    board.push(chess.Move.from_uci(uci))
    last = (uci, san)

# ---------------------------------------------------------------- report ----
print("\n" + "=" * 78)
print("PER-CHECKPOINT: identical position, three prompt shapes")
print("=" * 78)
hdr = f"{'':8s} {'prompt':>22s} {'in':>6s} {'out':>6s} {'reason':>7s} {'$/call':>11s} {'ms':>7s}"
for label in ("start", "middle", "end"):
    c = checkpoints.get(label)
    if not c:
        continue
    print(f"\n--- {label.upper()}  (ply {c['ply']}, played {c['san']}) ---")
    print(hdr)
    for key, name in (("full", "FULL (shipped today)"), ("lean", "LEAN (uci+tone)"),
                      ("lazy", "LAZY explain call")):
        r = c[key]
        print(f"{'':8s} {name:>22s} {r['in']:6d} {r['out']:6d} {r['reason']:7d} "
              f"{usd(r):11.8f} {r['ms']:7d}")
    inline = usd(c["full"])
    split = usd(c["lean"]) + usd(c["lazy"])
    lean_only = usd(c["lean"])
    print(f"{'':8s} {'inline total':>22s} {inline:25.8f}")
    print(f"{'':8s} {'lean + lazy (clicked)':>22s} {split:25.8f}  "
          f"{split/inline:.2f}x inline")
    print(f"{'':8s} {'lean only (no click)':>22s} {lean_only:25.8f}  "
          f"{lean_only/inline:.2f}x inline")
    be = (inline - lean_only) / (split - lean_only) if split > lean_only else float("nan")
    print(f"{'':8s} {'break-even click rate':>22s} {be*100:24.1f}%")
    print(f"{'':8s} lazy output: {c['lazy']['content'][:200]}")

print("\n" + "=" * 78)
print("WHOLE-GAME: lean move calls")
print("=" * 78)
n = len(lean_rows)
ti = sum(r["in"] for r in lean_rows)
to = sum(r["out"] for r in lean_rows)
tr = sum(r["reason"] for r in lean_rows)
print(f"moves: {n}   avg in {ti/n:.0f}   avg out {to/n:.0f}   avg reasoning {tr/n:.0f}")
print(f"avg tokens/move (lean): {(ti+to)/n:.0f}")
print(f"avg $/move (lean): {(ti*PRICE_IN + to*PRICE_OUT)/n:.8f}")
print(f"output tokens/move (lean): {to/n:.0f}  -> moves/min at 1000 OTPM: {1000/(to/n):.1f}")
rescued = sum(san_rescues)
print(f"moves rescued by accepting SAN: {rescued} of {n} ({100*rescued/n:.0f}%) "
      f"— each would have been a failed move under the shipped code")
