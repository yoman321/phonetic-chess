from flask import request
from flask_socketio import join_room

from controller_operations.helpers import room_name
from controller_operations.presence import (
    cancel_cleanup,
    remove_socket,
    schedule_cleanup,
    track_join,
)


def register_sockets(socketio):
    @socketio.on("join_session")
    def on_join_session(data):
        sid = (data or {}).get("sessionId")
        if not sid:
            return
        join_room(room_name(sid))
        track_join(sid, request.sid)
        cancel_cleanup(sid)

    @socketio.on("disconnect")
    def on_disconnect():
        for sid in remove_socket(request.sid):
            schedule_cleanup(sid)
