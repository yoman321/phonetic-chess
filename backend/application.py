from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS
from flask_socketio import SocketIO

import db
from controller.sessions import make_sessions_bp
from controller.sockets import register_sockets
from controller_operations import presence

load_dotenv()

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# The factory, not a connection: every operation opens and closes its own.
presence.init(db.connect)
presence.reschedule_existing_sessions()

app.register_blueprint(make_sessions_bp(db.connect, socketio))
register_sockets(socketio)


if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=5001, debug=True, allow_unsafe_werkzeug=True)
