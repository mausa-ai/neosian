# The state process, as an appliance (DESIGN §18.9): one token, one
# volume, one health check. Built and smoked by CI on both backends;
# deliberately not published to a registry — the reach decision is
# NZ's (ledger #114).
#
#   docker build -t neosian .
#   docker run -e NEOSIAN_SERVE_TOKEN=... -p 6367:6367 -v state:/data neosian
#
# The default command serves a FileStore on the /data volume; set
# NEOSIAN_POSTGRES_DSN (and override the command, e.g. `--schema
# neosian`) for the Postgres backend. TLS terminates at a reverse
# proxy (§18.4); the token is env-only, and an unset token refuses to
# start at exit 2.

FROM python:3.13-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY neosian/ neosian/
# The extras are the deliberate dependency set (never the dev group):
# `server` serves, `postgres` is the second backend of NM's done-when.
RUN uv sync --locked --no-dev --no-editable --extra server --extra postgres

FROM python:3.13-slim
RUN useradd --uid 1000 --create-home neosian \
    && mkdir /data && chown neosian:neosian /data
# The venv keeps its build path; the module door below needs no shebang.
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
USER neosian
VOLUME /data
EXPOSE 6367
HEALTHCHECK --interval=10s --timeout=3s --start-period=5s CMD \
    ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:6367/health', timeout=2)"]
# 0.0.0.0 rides the entrypoint: the library default (127.0.0.1, §18.7)
# is unreachable from outside a container, and a CMD override — a
# different root, a schema — must not silently lose the bind. The
# module door is `neosian serve` without the shell's `cli` extra (TP-2):
# the appliance installs no terminal library.
ENTRYPOINT ["python", "-m", "neosian.server", "--host", "0.0.0.0"]
CMD ["--root", "/data"]
