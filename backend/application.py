import os
import random
import secrets
import threading

import chess
import numpy as np
import psycopg
from dotenv import load_dotenv
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO, join_room
from neo4j import GraphDatabase
from psycopg.rows import dict_row
from sentence_transformers import SentenceTransformer
from seed.seed_db import MODEL_NAME, ensure_seeded

load_dotenv()

NEO4J_URI = f"bolt://{os.environ['NEO4J_HOST']}:{os.environ['NEO4J_PORT']}"
NEO4J_USER = os.environ.get("NEO4J_USER")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD")
DATABASE_URL = os.environ["DATABASE_URL"]

SIMILARITY_THRESHOLD = 0.35
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
SESSION_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
IDLE_TTL_SECONDS = int(os.environ.get("IDLE_TTL_SECONDS", "600"))  # default 10 min


def new_session_id(n=8):
    return "".join(secrets.choice(SESSION_ID_ALPHABET) for _ in range(n))


def new_player_token():
    return secrets.token_urlsafe(24)


def room_name(sid):
    return f"session:{sid}"


def append_pgn(prev_pgn, ply, san):
    move_num = (ply + 1) // 2
    if ply % 2 == 1:
        sep = " " if prev_pgn else ""
        return f"{prev_pgn}{sep}{move_num}. {san}"
    return f"{prev_pgn} {san}"


def status_from_board(board):
    if board.is_checkmate():
        return "white_won" if board.turn == chess.BLACK else "black_won"
    if (
        board.is_stalemate()
        or board.is_insufficient_material()
        or board.is_seventyfive_moves()
        or board.is_fivefold_repetition()
    ):
        return "draw"
    return "active"


def cosine_similarity(a, b):
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b))


def get_candidates(driver, fen):
    with driver.session() as session:
        results = session.run(
            """
            MATCH (p:Position {fen: $fen})-[m:MOVE]->(c:Position)
            RETURN m.uci AS uci, m.san AS san, m.phrase AS phrase,
                   m.opening AS opening, m.embedding AS embedding
            """,
            fen=fen,
        )
        return [dict(r) for r in results]


def find_best_match(model, user_input, candidates):
    user_embedding = model.encode(user_input)

    best = None
    best_score = -1.0

    for c in candidates:
        score = cosine_similarity(user_embedding, np.array(c["embedding"]))
        if score > best_score:
            best_score = float(score)
            best = c

    if best and best_score >= SIMILARITY_THRESHOLD:
        return best, best_score
    return None, best_score


auth = (NEO4J_USER, NEO4J_PASSWORD) if NEO4J_PASSWORD else None
driver = GraphDatabase.driver(NEO4J_URI, auth=auth)
driver.verify_connectivity()

ensure_seeded(driver)

model = SentenceTransformer(MODEL_NAME)

pg = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")


_presence_lock = threading.Lock()
_active_sids = {}      # session_id -> set of socketio sids in the room
_cleanup_timers = {}   # session_id -> threading.Timer


def _delete_session(sid):
    with _presence_lock:
        _cleanup_timers.pop(sid, None)
        if _active_sids.get(sid):
            return  # someone reconnected just before deletion fired
    with pg.cursor() as cur:
        cur.execute("DELETE FROM sessions WHERE id = %s", (sid,))
    print(f"[cleanup] deleted idle session {sid}")


def schedule_cleanup(sid):
    with _presence_lock:
        existing = _cleanup_timers.pop(sid, None)
        if existing:
            existing.cancel()
        timer = threading.Timer(IDLE_TTL_SECONDS, _delete_session, args=(sid,))
        timer.daemon = True
        _cleanup_timers[sid] = timer
        timer.start()


def cancel_cleanup(sid):
    with _presence_lock:
        existing = _cleanup_timers.pop(sid, None)
    if existing:
        existing.cancel()


def reschedule_existing_sessions():
    with pg.cursor() as cur:
        cur.execute("SELECT id FROM sessions")
        ids = [r["id"] for r in cur]
    for sid in ids:
        schedule_cleanup(sid)


reschedule_existing_sessions()


@app.post("/sessions")
def create_session():
    data = request.get_json(silent=True) or {}
    requested = data.get("color")
    if requested in (None, "random"):
        color = random.choice(("white", "black"))
    elif requested in ("white", "black"):
        color = requested
    else:
        return jsonify({"error": "bad_color"}), 400

    token = new_player_token()
    token_column = "white_token" if color == "white" else "black_token"

    for _ in range(5):
        sid = new_session_id()
        try:
            with pg.cursor() as cur:
                cur.execute(
                    f"INSERT INTO sessions (id, fen, {token_column}) VALUES (%s, %s, %s) "
                    "RETURNING id, fen, status, created_at",
                    (sid, START_FEN, token),
                )
                row = cur.fetchone()
            schedule_cleanup(sid)
            return jsonify({**row, "color": color, "playerToken": token}), 201
        except psycopg.errors.UniqueViolation:
            continue
    return jsonify({"error": "could_not_allocate_session_id"}), 500


@app.get("/sessions/<sid>")
def get_session(sid):
    with pg.cursor() as cur:
        cur.execute(
            "SELECT id, fen, pgn, status, created_at, updated_at FROM sessions WHERE id = %s",
            (sid,),
        )
        row = cur.fetchone()
    if not row:
        return jsonify({"error": "not_found"}), 404
    return jsonify(row)


@app.post("/sessions/<sid>/join")
def join_session_route(sid):
    data = request.get_json(silent=True) or {}
    existing = data.get("playerToken")

    with pg.transaction(), pg.cursor() as cur:
        cur.execute(
            "SELECT white_token, black_token FROM sessions WHERE id = %s FOR UPDATE",
            (sid,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "not_found"}), 404

        if existing:
            if existing == row["white_token"]:
                return jsonify({"color": "white", "playerToken": existing})
            if existing == row["black_token"]:
                return jsonify({"color": "black", "playerToken": existing})

        token = new_player_token()
        if not row["white_token"]:
            cur.execute(
                "UPDATE sessions SET white_token = %s WHERE id = %s", (token, sid)
            )
            return jsonify({"color": "white", "playerToken": token})
        if not row["black_token"]:
            cur.execute(
                "UPDATE sessions SET black_token = %s WHERE id = %s", (token, sid)
            )
            return jsonify({"color": "black", "playerToken": token})

        return jsonify({"error": "session_full"}), 409


@app.post("/sessions/<sid>/move")
def make_move(sid):
    data = request.get_json(force=True) or {}
    uci = (data.get("uci") or "").strip()
    token = data.get("playerToken")
    if not uci:
        return jsonify({"error": "missing_uci"}), 400
    if not token:
        return jsonify({"error": "missing_token"}), 401

    try:
        move = chess.Move.from_uci(uci)
    except Exception:
        return jsonify({"error": "bad_uci"}), 400

    with pg.transaction(), pg.cursor() as cur:
        cur.execute(
            "SELECT fen, pgn, status, white_token, black_token FROM sessions "
            "WHERE id = %s FOR UPDATE",
            (sid,),
        )
        row = cur.fetchone()
        if not row:
            return jsonify({"error": "not_found"}), 404
        if row["status"] != "active":
            return jsonify({"error": "game_over", "status": row["status"]}), 409

        if token == row["white_token"]:
            my_color = chess.WHITE
        elif token == row["black_token"]:
            my_color = chess.BLACK
        else:
            return jsonify({"error": "not_a_player"}), 403

        board = chess.Board(row["fen"])
        if my_color != board.turn:
            return jsonify({"error": "not_your_turn"}), 403
        if move not in board.legal_moves:
            return jsonify({"error": "illegal_move", "fen": row["fen"]}), 400

        san = board.san(move)
        board.push(move)
        ply = board.ply()
        new_fen = board.fen()
        new_pgn = append_pgn(row["pgn"], ply, san)
        new_status = status_from_board(board)

        cur.execute(
            "UPDATE sessions SET fen = %s, pgn = %s, status = %s, updated_at = NOW() "
            "WHERE id = %s",
            (new_fen, new_pgn, new_status, sid),
        )
        cur.execute(
            "INSERT INTO moves (session_id, ply, uci, san) VALUES (%s, %s, %s, %s)",
            (sid, ply, uci, san),
        )

    payload = {
        "fen": new_fen,
        "pgn": new_pgn,
        "status": new_status,
        "ply": ply,
        "uci": uci,
        "san": san,
    }
    socketio.emit("move", payload, to=room_name(sid))
    return jsonify(payload)


@socketio.on("join_session")
def on_join_session(data):
    sid = (data or {}).get("sessionId")
    if not sid:
        return
    join_room(room_name(sid))
    with _presence_lock:
        _active_sids.setdefault(sid, set()).add(request.sid)
    cancel_cleanup(sid)


@socketio.on("disconnect")
def on_disconnect():
    socket_sid = request.sid
    now_empty = []
    with _presence_lock:
        for sid, members in list(_active_sids.items()):
            if socket_sid in members:
                members.discard(socket_sid)
                if not members:
                    _active_sids.pop(sid, None)
                    now_empty.append(sid)
    for sid in now_empty:
        schedule_cleanup(sid)


@app.post("/match")
def match():
    data = request.get_json(force=True)
    fen = data["fen"]
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"match": None, "score": 0.0, "reason": "empty"}), 400

    candidates = get_candidates(driver, fen)
    if not candidates:
        return jsonify({"match": None, "score": 0.0, "reason": "off_book"})

    best, score = find_best_match(model, text, candidates)
    if not best:
        return jsonify({"match": None, "score": score, "reason": "below_threshold"})

    return jsonify({
        "match": {
            "uci": best["uci"],
            "san": best["san"],
            "phrase": best["phrase"],
            "opening": best["opening"],
        },
        "score": score,
    })


if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5001, debug=True, allow_unsafe_werkzeug=True)
