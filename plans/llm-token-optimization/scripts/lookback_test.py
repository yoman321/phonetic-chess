"""Explaining an OLD move: does looking back cost more?

Plays a 20-move game on the lean schema, keeping each ply's context. Then, from
the end of the game, asks for an explanation of plies 1, 5, 10, 15 and 20 in two
shapes:

  FLAT     — only that ply's stored context (fen before, message, prior tone, move)
  HISTORY  — the same, plus the game's move list from that ply up to now, so the
             explanation can account for what happened since

Nothing in the repo is modified.
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
client = OpenAI(
    api_key=os.environ["GROQ_API_KEY"],
    base_url="https://api.groq.com/openai/v1",
    timeout=60,
    max_retries=3,
)
PRICE_IN, PRICE_OUT = 0.075 / 1e6, 0.30 / 1e6

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
SYSTEM_LEAN = TONE_RULES + (
    'Reply ONLY with JSON of the form '
    '{"uci": "<one of the legal UCIs>", '
    '"tone_summary": "<updated rolling summary>"}.'
)
SYSTEM_EXPLAIN = (
    "A chess move was chosen to express the emotional tone of a player's "
    "message. Given the message, the position, and the move that was played, "
    "write a one-sentence 'intent' describing what the message communicates, "
    "and a one-sentence 'rationale' explaining why that move expresses it. "
    'Reply ONLY with JSON of the form '
    '{"intent": "<one sentence>", "rationale": "<one sentence>"}.'
)
SYSTEM_EXPLAIN_HIST = SYSTEM_EXPLAIN[:-1] + (
    " You are explaining a move from earlier in the game; the moves played "
    "since are given so your explanation can reflect how it turned out."
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


def explain_flat(c):
    return (
        f"Tone of the game so far: {c['prior_tone'] or '(none yet)'}\n"
        f"Player message: {c['msg']}\n"
        f"Position FEN before the move: {c['fen']}\n"
        f"Move played: {c['uci']} ({c['san']})"
    )


def explain_hist(c, sans_since):
    since = " ".join(sans_since) if sans_since else "(nothing yet)"
    return explain_flat(c) + f"\nMoves played since then: {since}"


def call(system, user):
    t0 = time.monotonic()
    raw = client.chat.completions.with_raw_response.create(
        model=MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        response_format={"type": "json_object"},
        temperature=0.7,
        extra_body={"reasoning_effort": "low", "reasoning_format": "hidden"},
    )
    resp = raw.parse()
    u = resp.usage
    d = u.completion_tokens_details
    return {
        "ms": int((time.monotonic() - t0) * 1000),
        "in": u.prompt_tokens, "out": u.completion_tokens,
        "reason": getattr(d, "reasoning_tokens", None) or 0,
        "content": resp.choices[0].message.content,
    }


def usd(r):
    return r["in"] * PRICE_IN + r["out"] * PRICE_OUT


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

board = chess.Board()
tone, last = "", None
plies = []          # one dict per completed ply

for i, msg in enumerate(MESSAGES):
    cands = rank_moves(board)
    if not cands:
        break
    legal = {m.uci() for m in board.legal_moves}
    fen_before, tone_before = board.fen(), tone
    chosen = None
    for _ in range(3):
        r = call(SYSTEM_LEAN, move_user(msg, fen_before, cands, tone_before, last))
        try:
            p = json.loads(r["content"])
        except json.JSONDecodeError:
            continue
        raw_uci = (p.get("uci") or "").strip()
        if raw_uci in legal:
            chosen = p
            break
        try:
            p["uci"] = board.parse_san(raw_uci).uci()
        except (ValueError, chess.IllegalMoveError, chess.InvalidMoveError):
            continue
        chosen = p
        break
    if chosen is None:
        print(f"ply {i+1}: no legal move; stopping")
        break
    uci = chosen["uci"].strip()
    tone = (chosen.get("tone_summary") or "").strip()
    san = board.san(chess.Move.from_uci(uci))
    plies.append({"ply": i + 1, "msg": msg, "fen": fen_before,
                  "prior_tone": tone_before, "uci": uci, "san": san})
    board.push(chess.Move.from_uci(uci))
    last = (uci, san)

n = len(plies)
print(f"\ngame complete: {n} plies\n")

TARGETS = [p for p in (1, 5, 10, 15, 20) if p <= n]
all_sans = [c["san"] for c in plies]

print("=" * 86)
print(f"EXPLAINING AN OLD MOVE, asked from the end of the game (ply {n})")
print("=" * 86)
print(f"{'target':>7s} {'lookback':>9s} {'shape':>9s} {'in':>6s} {'out':>6s} "
      f"{'$/call':>11s} {'vs flat@1':>10s} {'ms':>6s}")

base = None
rows = []
for t in TARGETS:
    c = plies[t - 1]
    since = all_sans[t:]           # moves played after that ply
    rf = call(SYSTEM_EXPLAIN, explain_flat(c))
    rh = call(SYSTEM_EXPLAIN_HIST, explain_hist(c, since))
    if base is None:
        base = usd(rf)
    for name, r in (("flat", rf), ("history", rh)):
        rows.append((t, name, r))
        print(f"{t:7d} {n - t:9d} {name:>9s} {r['in']:6d} {r['out']:6d} "
              f"{usd(r):11.8f} {usd(r)/base:9.2f}x {r['ms']:6d}")

flat = [r for t, nm, r in rows if nm == "flat"]
hist = [r for t, nm, r in rows if nm == "history"]
print("\n" + "-" * 86)
print(f"FLAT    input tokens: min {min(r['in'] for r in flat)}  "
      f"max {max(r['in'] for r in flat)}  "
      f"spread {max(r['in'] for r in flat) - min(r['in'] for r in flat)}")
print(f"HISTORY input tokens: min {min(r['in'] for r in hist)}  "
      f"max {max(r['in'] for r in hist)}  "
      f"spread {max(r['in'] for r in hist) - min(r['in'] for r in hist)}")
print(f"\nFLAT    avg $/call: {sum(usd(r) for r in flat)/len(flat):.8f}")
print(f"HISTORY avg $/call: {sum(usd(r) for r in hist)/len(hist):.8f}")
print(f"HISTORY costs {(sum(usd(r) for r in hist)/sum(usd(r) for r in flat)):.2f}x FLAT")

print("\nsample explanations of ply 1, asked at ply", n)
for t, nm, r in rows:
    if t == 1:
        print(f"  [{nm}] {r['content'][:230]}")
