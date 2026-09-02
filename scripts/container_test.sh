#!/usr/bin/env bash
# The container conformance run (DESIGN §18.9, NM's done-when): build
# the image, start one container per backend, and point the
# `external_server` tier's RemoteStore at it over a real socket — both
# conformance kits, the bearer gate, the health check, and a graceful
# SIGTERM per leg.
#
# The FileStore leg always runs. The Postgres leg runs when
# NEOSIAN_TEST_POSTGRES_DSN is set (CI's service container, or the
# SERVICES.md one-liner) and is skipped loudly otherwise. Needs a
# docker daemon; refuses without one.
set -euo pipefail

IMAGE="${NEOSIAN_CONTAINER_IMAGE:-neosian:conformance}"
TOKEN="container-conformance"
PORT="${NEOSIAN_CONTAINER_PORT:-16367}"
SCHEMA="neosian_container"
BOOT_TIMEOUT=30

command -v docker >/dev/null || {
    echo "container_test: docker not found — install a docker daemon" >&2
    exit 2
}

cleanup() {
    if [ -n "${CID:-}" ]; then
        docker rm -f "$CID" >/dev/null 2>&1 || true
    fi
}
trap cleanup EXIT

wait_health() {
    local url="$1" waited=0
    until curl -fsS "$url/health" >/dev/null 2>&1; do
        if [ -z "$(docker ps -q --no-trunc | grep "$CID" || true)" ]; then
            echo "container_test: container exited before healthy" >&2
            docker logs "$CID" >&2 || true
            exit 1
        fi
        if [ "$waited" -ge "$BOOT_TIMEOUT" ]; then
            echo "container_test: no /health after ${BOOT_TIMEOUT}s" >&2
            docker logs "$CID" >&2 || true
            exit 1
        fi
        sleep 1
        waited=$((waited + 1))
    done
}

graceful_stop() {
    # SIGTERM drains, then uvicorn logs the shutdown — the log, never
    # the exit code, is the evidence (§18.7).
    docker stop -t 30 "$CID" >/dev/null
    # The log driver can trail the stop by a beat (run 33643692828 read
    # the log 60 ms before uvicorn's last lines landed): wait for the
    # line, up to ten seconds, before calling the shutdown ungraceful.
    local tries=0
    until docker logs "$CID" 2>&1 | grep -q "Application shutdown complete"; do
        if [ "$((tries += 1))" -ge 20 ]; then
            echo "container_test: no graceful shutdown in the logs" >&2
            docker logs "$CID" >&2 || true
            exit 1
        fi
        sleep 0.5
    done
    docker rm "$CID" >/dev/null
    CID=""
}

echo "== build $IMAGE"
docker build -q -t "$IMAGE" . >/dev/null

echo "== leg 1: volume FileStore"
ROOT="$(mktemp -d)"
chmod 0777 "$ROOT"
CID="$(docker run -d --user "$(id -u):$(id -g)" \
    -e NEOSIAN_SERVE_TOKEN="$TOKEN" \
    -p "127.0.0.1:${PORT}:6367" -v "$ROOT:/data" "$IMAGE")"
wait_health "http://127.0.0.1:${PORT}"
NEOSIAN_TEST_SERVER_URL="http://127.0.0.1:${PORT}" \
    NEOSIAN_TEST_SERVER_TOKEN="$TOKEN" \
    NEOSIAN_TEST_SERVER_ROOT="$ROOT" \
    uv run pytest -m external_server -q
graceful_stop
rm -rf "$ROOT"

if [ -z "${NEOSIAN_TEST_POSTGRES_DSN:-}" ]; then
    echo "== leg 2: Postgres — SKIPPED (NEOSIAN_TEST_POSTGRES_DSN not set)"
    exit 0
fi

echo "== leg 2: Postgres DSN"
# The schema is the operator's explicit act (C1) — applied from the
# host before the server starts.
uv run python -c "
import asyncio, os
from neosian import PostgresStore

async def main() -> None:
    store = PostgresStore(
        os.environ['NEOSIAN_TEST_POSTGRES_DSN'], schema='$SCHEMA'
    )
    await store.apply_schema()
    await store.aclose()

asyncio.run(main())
"
# Inside the container, the host's databases live at
# host.docker.internal (mapped to the gateway on plain Linux docker).
CONTAINER_DSN="${NEOSIAN_TEST_POSTGRES_DSN/localhost/host.docker.internal}"
CONTAINER_DSN="${CONTAINER_DSN/127.0.0.1/host.docker.internal}"
CID="$(docker run -d --add-host=host.docker.internal:host-gateway \
    -e NEOSIAN_SERVE_TOKEN="$TOKEN" \
    -e NEOSIAN_POSTGRES_DSN="$CONTAINER_DSN" \
    -p "127.0.0.1:${PORT}:6367" "$IMAGE" --schema "$SCHEMA")"
wait_health "http://127.0.0.1:${PORT}"
NEOSIAN_TEST_SERVER_URL="http://127.0.0.1:${PORT}" \
    NEOSIAN_TEST_SERVER_TOKEN="$TOKEN" \
    NEOSIAN_TEST_SERVER_SCHEMA="$SCHEMA" \
    uv run pytest -m external_server -q
graceful_stop

echo "== container conformance: both legs green"
