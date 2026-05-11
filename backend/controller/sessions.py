from flask import Blueprint, jsonify, request

from controller_operations import sessions_ops
from controller_operations.errors import ApiError


def make_sessions_bp(pg, socketio):
    bp = Blueprint("sessions", __name__)

    @bp.app_errorhandler(ApiError)
    def _handle_api_error(e):
        return jsonify(e.to_payload()), e.status

    @bp.post("/sessions")
    def create_session():
        data = request.get_json(silent=True) or {}
        return jsonify(sessions_ops.create_session(pg, data.get("color"))), 201

    @bp.get("/sessions/<sid>")
    def get_session(sid):
        return jsonify(sessions_ops.get_session(pg, sid))

    @bp.post("/sessions/<sid>/join")
    def join_session_route(sid):
        data = request.get_json(silent=True) or {}
        return jsonify(sessions_ops.join_session(pg, sid, data.get("playerToken")))

    @bp.post("/sessions/<sid>/move")
    def make_move(sid):
        data = request.get_json(force=True) or {}
        uci = (data.get("uci") or "").strip()
        token = data.get("playerToken")
        return jsonify(sessions_ops.make_move(pg, socketio, sid, uci, token))

    @bp.post("/sessions/<sid>/say")
    def say_move(sid):
        data = request.get_json(force=True) or {}
        text = (data.get("text") or "").strip()
        token = data.get("playerToken")
        return jsonify(sessions_ops.say_move(pg, socketio, sid, text, token))

    return bp
