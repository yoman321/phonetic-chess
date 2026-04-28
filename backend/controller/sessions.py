import json
import random
import urllib.error

import chess
import psycopg
from flask import Blueprint, jsonify, request

from controller_operations.engine import rank_moves
from controller_operations.helpers import (
    START_FEN,
    append_pgn,
    new_player_token,
    new_session_id,
    room_name,
    status_from_board,
)
from controller_operations.llm import pick_move_with_llm
from controller_operations.presence import schedule_cleanup
from queries import moves as moves_q
from queries import sessions as sessions_q


def make_sessions_bp(pg, socketio):
    bp = Blueprint("sessions", __name__)

    @bp.post("/sessions")
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

        for _ in range(5):
            sid = new_session_id()
            try:
                with pg.cursor() as cur:
                    row = sessions_q.insert_session(cur, sid, START_FEN, color, token)
                schedule_cleanup(sid)
                return jsonify({**row, "color": color, "playerToken": token}), 201
            except psycopg.errors.UniqueViolation:
                continue
        return jsonify({"error": "could_not_allocate_session_id"}), 500

    @bp.get("/sessions/<sid>")
    def get_session(sid):
        with pg.cursor() as cur:
            row = sessions_q.select_session_summary(cur, sid)
        if not row:
            return jsonify({"error": "not_found"}), 404
        return jsonify(row)

    @bp.post("/sessions/<sid>/join")
    def join_session_route(sid):
        data = request.get_json(silent=True) or {}
        existing = data.get("playerToken")

        with pg.transaction(), pg.cursor() as cur:
            row = sessions_q.select_tokens_for_update(cur, sid)
            if not row:
                return jsonify({"error": "not_found"}), 404

            if existing:
                if existing == row["white_token"]:
                    return jsonify({"color": "white", "playerToken": existing})
                if existing == row["black_token"]:
                    return jsonify({"color": "black", "playerToken": existing})

            token = new_player_token()
            if not row["white_token"]:
                sessions_q.set_white_token(cur, sid, token)
                return jsonify({"color": "white", "playerToken": token})
            if not row["black_token"]:
                sessions_q.set_black_token(cur, sid, token)
                return jsonify({"color": "black", "playerToken": token})

            return jsonify({"error": "session_full"}), 409

    @bp.post("/sessions/<sid>/move")
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
            row = sessions_q.select_state_for_update(cur, sid)
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

            sessions_q.update_after_move(cur, sid, new_fen, new_pgn, new_status)
            moves_q.insert_move(cur, sid, ply, uci, san)

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

    @bp.post("/sessions/<sid>/say")
    def say_move(sid):
        data = request.get_json(force=True) or {}
        text = (data.get("text") or "").strip()
        token = data.get("playerToken")
        if not text:
            return jsonify({"error": "missing_text"}), 400
        if not token:
            return jsonify({"error": "missing_token"}), 401

        with pg.transaction(), pg.cursor() as cur:
            row = sessions_q.select_state_for_update(cur, sid)
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

            prior_row = moves_q.select_last_move(cur, sid)

            if prior_row is None:
                # Game just started — only white can make the first move.
                if my_color != chess.WHITE:
                    return jsonify({"error": "waiting_for_opponent_move"}), 409
            else:
                last_mover = chess.WHITE if prior_row["ply"] % 2 == 1 else chess.BLACK
                if last_mover == my_color:
                    return jsonify({"error": "waiting_for_opponent_move"}), 409

            board = chess.Board(row["fen"])
            if my_color != board.turn:
                return jsonify({"error": "not_your_turn"}), 403

            candidates = rank_moves(board)
            if not candidates:
                return jsonify({"error": "no_legal_moves"}), 409
            all_legal_ucis = {m.uci() for m in board.legal_moves}

            if prior_row:
                last_move = (prior_row["uci"], prior_row["san"])
                prior_tone = prior_row["tone_summary"] or ""
            else:
                last_move = None
                prior_tone = ""

            try:
                chosen_uci, tone_summary = pick_move_with_llm(
                    text, row["fen"], candidates, prior_tone, last_move,
                    valid_ucis=all_legal_ucis,
                )
            except (urllib.error.URLError, TimeoutError) as e:
                return jsonify({"error": "llm_unavailable", "detail": str(e)}), 502
            except (ValueError, json.JSONDecodeError, KeyError) as e:
                return jsonify({"error": "llm_bad_response", "detail": str(e)}), 502

            move = chess.Move.from_uci(chosen_uci)
            san = board.san(move)
            board.push(move)
            ply = board.ply()
            new_fen = board.fen()
            new_pgn = append_pgn(row["pgn"], ply, san)
            new_status = status_from_board(board)

            sessions_q.update_after_move(cur, sid, new_fen, new_pgn, new_status)
            moves_q.insert_move(cur, sid, ply, chosen_uci, san, tone_summary or None)

        payload = {
            "fen": new_fen,
            "pgn": new_pgn,
            "status": new_status,
            "ply": ply,
            "uci": chosen_uci,
            "san": san,
            "text": text,
            "tone_summary": tone_summary,
        }
        socketio.emit("move", payload, to=room_name(sid))
        return jsonify(payload)

    return bp
