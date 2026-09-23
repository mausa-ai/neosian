#!/usr/bin/env bash
# The local lane's server (DESIGN §31.6): llama.cpp's server image, the
# measured model pulled from Hugging Face at boot, waited on until /health
# answers 200 (a 405 or a 503 is the model still loading). CI's `local`
# matrix entry runs it; so does a machine without llama.cpp installed.
# With the binary installed, the docs page's one-liner is the same server.
# The image pin is bumped by hand (Dependabot reads Dockerfiles, not this
# line): b10964 is llama.cpp 0.4.1, the build the recipes were measured on.
set -euo pipefail

IMAGE="ghcr.io/ggml-org/llama.cpp:server-b10964@sha256:283ed1799f2711361dadd863305ffe31c9fca3321a099712a8cc65061502b523"
MODEL="${NEOSIAN_LOCAL_MODEL:-ggml-org/gemma-4-E4B-it-GGUF:Q4_0}"
PORT="${NEOSIAN_LOCAL_PORT:-8080}"
CONTEXT="${NEOSIAN_LOCAL_CONTEXT:-32768}"
BOOT_TIMEOUT="${NEOSIAN_LOCAL_BOOT_TIMEOUT:-900}" # the pull is 4.6 GB

# The model's default thinking stays on: measured 9/10 against 4/10 with
# the template's switch off on the function transport (DESIGN §31.6).
docker run -d --name neosian-local -p "127.0.0.1:${PORT}:8080" "$IMAGE" \
  -hf "$MODEL" --jinja -c "$CONTEXT" --host 0.0.0.0 --port 8080

echo "waiting for llama-server on :${PORT} (up to ${BOOT_TIMEOUT} s)"
for ((elapsed = 0; elapsed < BOOT_TIMEOUT; elapsed += 5)); do
  if curl -fsS "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1; then
    echo "llama-server ready after ${elapsed} s: ${MODEL}"
    exit 0
  fi
  sleep 5
done
echo "llama-server not ready after ${BOOT_TIMEOUT} s" >&2
docker logs neosian-local 2>&1 | tail -20 >&2
exit 1
