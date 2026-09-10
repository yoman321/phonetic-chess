"""Business logic for the /sessions HTTP routes.

Each function takes already-parsed inputs and returns the success
payload. On any failure it raises ApiError; the controller's error
handler turns that into the HTTP response.
"""
import json
import random
import urllib.error

import chess
import psycopg

from controller_operations.engine import evaluate, rank_moves
from controller_operations.errors import ApiError
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
from error_logger import logger
from queries import moves as moves_q
from queries import sessions as sessions_q


def create_session(db, requested_color):
    if requested_color in (None, "random"):
        color = random.choice(("white", "black"))
    elif requested_color in ("white", "black"):
        color = requested_color
    else:
        logger.error("create_session: bad_color requested=%r", requested_color)
        raise ApiError("bad_color", 400)

    token = new_player_token()

    with db() as pg, pg.cursor() as cur:
        for _ in range(5):
            sid = new_session_id()
            try:
                row = sessions_q.insert_session(cur, sid, START_FEN, color, token)
            except psycopg.errors.UniqueViolation:
                continue
            schedule_cleanup(sid)
            return {**row, "color": color, "playerToken": token}
    logger.error("create_session: could_not_allocate_session_id after 5 attempts")
    raise ApiError("could_not_allocate_session_id", 500)


def get_session(db, sid):
    with db() as pg, pg.cursor() as cur:
        row = sessions_q.select_session_summary(cur, sid)
    if not row:
        logger.error("get_session: not_found sid=%s", sid)
        raise ApiError("not_found", 404)
    row["evalCp"] = evaluate(chess.Board(row["fen"]))
    return row


def join_session(db, socketio, sid, existing_token):
    # Returning player, or a full game. Pure lookup, no lock: this is the path
    # that would otherwise wait on an in-flight say_move holding the row.
    with db() as pg, pg.cursor() as cur:
        row = sessions_q.select_tokens(cur, sid)
        if not row:
            logger.error("join_session: not_found sid=%s", sid)
            raise ApiError("not_found", 404)

        # The guard matters: a None token compares equal to a NULL column and
        # would hand a joiner a colour they never claimed.
        if existing_token:
            if existing_token == row["white_token"]:
                return {"color": "white", "playerToken": existing_token,
                        "opponentJoined": bool(row["black_token"])}
            if existing_token == row["black_token"]:
                return {"color": "black", "playerToken": existing_token,
                        "opponentJoined": bool(row["white_token"])}

        if row["white_token"] and row["black_token"]:
            # A third visitor, or a player whose sessionStorage was cleared.
            logger.error("join_session: session_full sid=%s", sid)
            raise ApiError("session_full", 409)

    # A colour is free, so by invariant 8 no move has been accepted and nothing
    # can be holding this row. Claim it under the lock.
    with db() as pg, pg.transaction(), pg.cursor() as cur:
        row = sessions_q.select_tokens_for_update(cur, sid)
        if not row:
            logger.error("join_session: not_found sid=%s", sid)
            raise ApiError("not_found", 404)

        token = new_player_token()
        if not row["white_token"]:
            sessions_q.set_white_token(cur, sid, token)
            color, opponent_joined = "white", bool(row["black_token"])
        elif not row["black_token"]:
            sessions_q.set_black_token(cur, sid, token)
            color, opponent_joined = "black", bool(row["white_token"])
        else:
            # Lost a race for the last slot between the lookup and the lock.
            logger.error("join_session: session_full sid=%s", sid)
            raise ApiError("session_full", 409)

    # After commit, matching make_move and say_move: emitting before it would
    # enable the waiting player's input for a claim a failed commit never made.
    socketio.emit("player_joined", {"color": color}, to=room_name(sid))
    return {"color": color, "playerToken": token,
            "opponentJoined": opponent_joined}


def make_move(db, socketio, sid, uci, token):
    if not uci:
        logger.error("make_move: missing_uci sid=%s", sid)
        raise ApiError("missing_uci", 400)
    if not token:
        logger.error("make_move: missing_token sid=%s", sid)
        raise ApiError("missing_token", 401)

    try:
        move = chess.Move.from_uci(uci)
    except Exception:
        logger.exception("make_move: bad_uci sid=%s uci=%r", sid, uci)
        raise ApiError("bad_uci", 400)

    with db() as pg, pg.transaction(), pg.cursor() as cur:
        row = sessions_q.select_state_for_update(cur, sid)
        if not row:
            logger.error("make_move: not_found sid=%s", sid)
            raise ApiError("not_found", 404)
        if row["status"] != "active":
            logger.error("make_move: game_over sid=%s status=%s", sid, row["status"])
            raise ApiError("game_over", 409, status=row["status"])
        if not row["white_token"] or not row["black_token"]:
            logger.info("make_move: waiting_for_opponent_join sid=%s", sid)
            raise ApiError("waiting_for_opponent_join", 409)

        if token == row["white_token"]:
            my_color = chess.WHITE
        elif token == row["black_token"]:
            my_color = chess.BLACK
        else:
            logger.error("make_move: not_a_player sid=%s", sid)
            raise ApiError("not_a_player", 403)

        board = chess.Board(row["fen"])
        if my_color != board.turn:
            logger.error("make_move: not_your_turn sid=%s", sid)
            raise ApiError("not_your_turn", 403)
        if move not in board.legal_moves:
            logger.error(
                "make_move: illegal_move sid=%s uci=%s fen=%s",
                sid, uci, row["fen"],
            )
            raise ApiError("illegal_move", 400, fen=row["fen"])

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
        "evalCp": evaluate(board),
    }
    socketio.emit("move", payload, to=room_name(sid))
    return payload


def say_move(db, socketio, sid, text, token):
    if not text:
        logger.error("say_move: missing_text sid=%s", sid)
        raise ApiError("missing_text", 400)
    if not token:
        logger.error("say_move: missing_token sid=%s", sid)
        raise ApiError("missing_token", 401)

    with db() as pg, pg.transaction(), pg.cursor() as cur:
        row = sessions_q.select_state_for_update(cur, sid)
        if not row:
            logger.error("say_move: not_found sid=%s", sid)
            raise ApiError("not_found", 404)
        if row["status"] != "active":
            logger.error("say_move: game_over sid=%s status=%s", sid, row["status"])
            raise ApiError("game_over", 409, status=row["status"])
        if not row["white_token"] or not row["black_token"]:
            logger.info("say_move: waiting_for_opponent_join sid=%s", sid)
            raise ApiError("waiting_for_opponent_join", 409)

        if token == row["white_token"]:
            my_color = chess.WHITE
        elif token == row["black_token"]:
            my_color = chess.BLACK
        else:
            logger.error("say_move: not_a_player sid=%s", sid)
            raise ApiError("not_a_player", 403)

        prior_row = moves_q.select_last_move(cur, sid)

        if prior_row is None:
            # Game just started — only white can make the first move.
            if my_color != chess.WHITE:
                logger.error(
                    "say_move: waiting_for_opponent_move sid=%s (no prior moves)", sid,
                )
                raise ApiError("waiting_for_opponent_move", 409)
        else:
            last_mover = chess.WHITE if prior_row["ply"] % 2 == 1 else chess.BLACK
            if last_mover == my_color:
                logger.error("say_move: waiting_for_opponent_move sid=%s", sid)
                raise ApiError("waiting_for_opponent_move", 409)

        board = chess.Board(row["fen"])
        if my_color != board.turn:
            logger.error("say_move: not_your_turn sid=%s", sid)
            raise ApiError("not_your_turn", 403)

        candidates = rank_moves(board)
        if not candidates:
            logger.error("say_move: no_legal_moves sid=%s fen=%s", sid, row["fen"])
            raise ApiError("no_legal_moves", 409)
        all_legal_ucis = {m.uci() for m in board.legal_moves}

        if prior_row:
            last_move = (prior_row["uci"], prior_row["san"])
            prior_tone = prior_row["tone_summary"] or ""
        else:
            last_move = None
            prior_tone = ""

        player_side = "white" if my_color == chess.WHITE else "black"
        socketio.emit(
            "thinking",
            {"on": True, "side": player_side, "status": "thinking"},
            to=room_name(sid),
        )

        # Everything from here to the end of the transaction runs under a
        # finally, so no exception type — mapped, unmapped, or raised by the
        # write — can leave the mover's indicator spinning and their input
        # disabled. Do not restore the per-except emits: two `on:false` on the
        # mapped paths breaks the same invariant from the other side.
        try:
            def _on_llm_retry(attempt):
                socketio.emit(
                    "thinking",
                    {
                        "on": True,
                        "side": player_side,
                        "status": "retrying",
                        "attempt": attempt,
                    },
                    to=room_name(sid),
                )

            try:
                chosen_uci, tone_summary, intent, rationale = pick_move_with_llm(
                    text, row["fen"], candidates, prior_tone, last_move,
                    valid_ucis=all_legal_ucis,
                    on_retry=_on_llm_retry,
                )
            except (urllib.error.URLError, TimeoutError) as e:
                logger.exception("say_move: llm_unavailable sid=%s", sid)
                raise ApiError("llm_unavailable", 502, detail=str(e))
            except (ValueError, json.JSONDecodeError, KeyError) as e:
                logger.exception("say_move: llm_bad_response sid=%s", sid)
                raise ApiError("llm_bad_response", 502, detail=str(e))

            move = chess.Move.from_uci(chosen_uci)
            san = board.san(move)
            board.push(move)
            ply = board.ply()
            new_fen = board.fen()
            new_pgn = append_pgn(row["pgn"], ply, san)
            new_status = status_from_board(board)

            sessions_q.update_after_move(cur, sid, new_fen, new_pgn, new_status)
            moves_q.insert_move(cur, sid, ply, chosen_uci, san, tone_summary or None)
        finally:
            # On success this fires inside the transaction, before the `move`
            # event at the end. Harmless: the client clears thinkingSide on both.
            socketio.emit(
                "thinking", {"on": False, "side": player_side}, to=room_name(sid)
            )

    payload = {
        "fen": new_fen,
        "pgn": new_pgn,
        "status": new_status,
        "ply": ply,
        "uci": chosen_uci,
        "san": san,
        "text": text,
        "tone_summary": tone_summary,
        "intent": intent,
        "rationale": rationale,
        "priorTone": prior_tone or "",
        "evalCp": evaluate(board),
    }
    socketio.emit("move", payload, to=room_name(sid))
    return payload
