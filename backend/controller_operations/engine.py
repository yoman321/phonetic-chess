"""Lightweight Sunfish-backed move ranker.

Given a python-chess Board, returns the top-N best moves for the side to
move using Sunfish's static evaluation (piece-square deltas + capture
bonuses). Pure-Python, no search, runs in well under a millisecond per
position. Used to filter the candidate list before handing it to the LLM.
"""
import chess

from vendor import sunfish

ENGINE_TOPN_DEFAULT = 15


def _fen_to_sunfish_pos(fen):
    """Build a Sunfish Position from a FEN, oriented for the side to move."""
    board_part, turn, _castling, ep_str, _hm, _fm = fen.split()

    # Sunfish board layout: 12 rows of 10 chars each. Top/bottom 2 rows are
    # padding; ranks 8..1 occupy rows 2..9 with a leading space + 8 squares + \n.
    lines = [" " * 9 + "\n", " " * 9 + "\n"]
    for fen_row in board_part.split("/"):
        squares = []
        for ch in fen_row:
            if ch.isdigit():
                squares.append("." * int(ch))
            else:
                squares.append(ch)
        line = " " + "".join(squares) + "\n"
        if len(line) != 10:
            raise ValueError(f"bad FEN row {fen_row!r}: expected 8 squares")
        lines.append(line)
    lines.append(" " * 9 + "\n")
    lines.append(" " * 9 + "\n")
    board_str = "".join(lines)

    ep = 0 if ep_str == "-" else sunfish.parse(ep_str)

    # Castling rights are not consulted by Position.value(); we only use value()
    # for ranking, never gen_moves(), so leaving them all True is harmless.
    pos = sunfish.Position(
        board=board_str,
        score=0,
        wc=(True, True),
        bc=(True, True),
        ep=ep,
        kp=0,
    )

    # Sunfish always reasons from the side-to-move's POV: when it's black's
    # turn we rotate the board so black's pieces are at the bottom.
    if turn == "b":
        pos = pos.rotate()
    return pos


def _to_sunfish_move(side_is_white, uci):
    """Convert a UCI string to Sunfish's Move tuple, mirroring squares for black."""
    i = sunfish.parse(uci[0:2])
    j = sunfish.parse(uci[2:4])
    prom = uci[4:].upper() if len(uci) > 4 else ""
    if not side_is_white:
        i, j = 119 - i, 119 - j
    return sunfish.Move(i, j, prom)


def rank_moves(board, top_n=ENGINE_TOPN_DEFAULT):
    """Return up to top_n (uci, san) pairs ordered best-first.

    Falls back to the full legal-move list (in arbitrary order) if Sunfish
    chokes on the position — the LLM can still pick from the full set.
    """
    legal = list(board.legal_moves)
    if not legal:
        return []
    if len(legal) <= top_n:
        return [(m.uci(), board.san(m)) for m in legal]

    try:
        pos = _fen_to_sunfish_pos(board.fen())
        side_is_white = board.turn == chess.WHITE
        scored = []
        for move in legal:
            sf_move = _to_sunfish_move(side_is_white, move.uci())
            scored.append((pos.value(sf_move), move))
        scored.sort(key=lambda t: t[0], reverse=True)
        top = scored[:top_n]
        return [(m.uci(), board.san(m)) for _, m in top]
    except Exception:
        return [(m.uci(), board.san(m)) for m in legal[:top_n]]
