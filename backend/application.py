import os

import psycopg
from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO
from psycopg.rows import dict_row

from controller.sessions import make_sessions_bp
from controller.sockets import register_sockets
from controller_operations import presence

load_dotenv()

DATABASE_URL = os.environ["DATABASE_URL"]

pg = psycopg.connect(DATABASE_URL, autocommit=True, row_factory=dict_row)

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

presence.init(pg)
presence.reschedule_existing_sessions()

app.register_blueprint(make_sessions_bp(pg, socketio))
register_sockets(socketio)


if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5001, debug=True, allow_unsafe_werkzeug=True)
