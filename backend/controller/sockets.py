from flask import request
from flask_socketio import emit, join_room

from controller_operations.helpers import room_name
from controller_operations.presence import is_idle, remove_socket, track_join


def register_sockets(socketio):
    @socketio.on("join_session")
    def on_join_session(data):
        sid = (data or {}).get("sessionId")
        if not sid:
            return
        # Before the room and before the row: recording this socket first would
        # make the predicate see the joiner and no game would ever be idle.
        if is_idle(sid):
            # Telling the caller is the point. A silent refusal leaves a
            # reconnecting client sitting in a game, receiving no moves and
            # given no reason — the symptom this design exists to remove.
            emit("game_ended", {"sessionId": sid})
            return
        join_room(room_name(sid))
        track_join(sid, request.sid)

    @socketio.on("disconnect")
    def on_disconnect():
        remove_socket(request.sid)
