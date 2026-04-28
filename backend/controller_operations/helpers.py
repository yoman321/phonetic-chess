import secrets

import chess

SESSION_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
START_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


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
