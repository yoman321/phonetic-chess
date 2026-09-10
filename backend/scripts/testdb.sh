#!/usr/bin/env sh
# Throwaway Postgres for the integration tier. Port 55432 so it cannot collide
# with a local server on 5432 or with the compose stack, and so test data never
# shares a volume with development data.
#
# Two backends. Docker is preferred: db.Dockerfile already bakes schema.sql into
# the init scripts, so the schema matches what compose runs. When the Docker
# daemon is not reachable it falls back to a local initdb cluster under TMPDIR,
# which needs postgres 16 on PATH (`brew install postgresql@16`).
set -e

PORT=55432
IMAGE=phonetic-chess-testdb
NAME=pc-test-db
DATADIR="${TMPDIR:-/tmp}/pc-test-db"
REPO=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)   # backend/

have_docker() {
  docker version >/dev/null 2>&1
}

docker_up() {
  docker build -q -t "$IMAGE" -f "$REPO/db.Dockerfile" "$REPO" >/dev/null
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  docker run --rm -d --name "$NAME" -p "127.0.0.1:$PORT:5432" \
    -e POSTGRES_USER=phonetic -e POSTGRES_PASSWORD=phonetic \
    -e POSTGRES_DB=phonetic_chess "$IMAGE" >/dev/null
  until docker exec "$NAME" pg_isready -U phonetic -d phonetic_chess >/dev/null 2>&1; do
    sleep 1
  done
}

docker_down() {
  docker rm -f "$NAME" >/dev/null 2>&1 || true
}

local_up() {
  command -v initdb >/dev/null 2>&1 || {
    echo "testdb.sh: no Docker daemon and no initdb on PATH." >&2
    exit 1
  }
  if [ ! -s "$DATADIR/PG_VERSION" ]; then
    rm -rf "$DATADIR"
    initdb -D "$DATADIR" -U phonetic --auth=trust >/dev/null
  fi
  pg_ctl -D "$DATADIR" -o "-p $PORT -k $DATADIR -c listen_addresses=127.0.0.1" \
    -l "$DATADIR/server.log" -w start >/dev/null
  psql -h 127.0.0.1 -p "$PORT" -U phonetic -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='phonetic_chess'" | grep -q 1 || {
      createdb -h 127.0.0.1 -p "$PORT" -U phonetic phonetic_chess
      psql -q -h 127.0.0.1 -p "$PORT" -U phonetic -d phonetic_chess \
        -v ON_ERROR_STOP=1 -f "$REPO/schema.sql" >/dev/null
    }
  until pg_isready -h 127.0.0.1 -p "$PORT" -U phonetic -d phonetic_chess >/dev/null 2>&1; do
    sleep 1
  done
}

local_down() {
  [ -s "$DATADIR/PG_VERSION" ] || return 0
  pg_ctl -D "$DATADIR" -m immediate -w stop >/dev/null 2>&1 || true
  rm -rf "$DATADIR"
}

case "$1" in
  up)   if have_docker; then docker_up; else local_up; fi ;;
  down) if have_docker; then docker_down; else local_down; fi ;;
  *)    echo "usage: $0 {up|down}" >&2; exit 2 ;;
esac
