"""The state process (DESIGN §18): the wire, its server, and RemoteStore.

`wire.py` and `remote.py` are extra-free (httpx is a core dependency);
everything that serves — `sdk.py`, `routes.py`, `app.py`, `runner.py` —
rides the `server` extra behind the one guarded import site in `sdk.py`.
"""
