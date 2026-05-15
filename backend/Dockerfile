# Multi-stage isn't needed here — pure Python, no compile step.
# Pin a slim Python base for small image + reasonable security patches.
FROM python:3.12-slim

# Don't write .pyc files; force unbuffered stdout/stderr so logs stream live.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps first (separate layer) so code changes don't bust the pip cache.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir "gunicorn==23.0.0" "gevent==24.11.1" "gevent-websocket==0.10.1"

# Copy the rest of the backend source.
COPY . .

EXPOSE 5001

# geventwebsocket worker = gevent + proper WebSocket upgrade handling,
# which Flask-SocketIO needs. Plain `gevent` worker accepts the HTTP
# handshake but can't upgrade, so SocketIO falls back to broken polling.
# Keep workers=1: SocketIO without a message queue assumes a single
# process, and presence.reschedule runs at import time.
CMD ["gunicorn", "-k", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "-w", "1", "-b", "0.0.0.0:5001", "application:app"]
