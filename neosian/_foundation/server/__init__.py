"""The state process (DESIGN §18): the wire, its server, and RemoteStore.

`wire.py` and `remote.py` need httpx alone; everything that serves —
`sdk.py`, `routes.py`, `app.py`, `runner.py` — loads the serving stack
at use behind the one guarded import site in `sdk.py`.
"""
