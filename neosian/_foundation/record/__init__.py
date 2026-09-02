"""The record of a foreign agent's session (NL slice B, DESIGN §20.9).

A foreign agent's hooks call `neosian record` with one payload on stdin
per event; a prompt-to-stop span lands as one turn with the foreign
actor plus a sessions document. Pure pieces (`span`), the spool
(`spool`), the shared grammar (`settings`), the engine (`cli`) and the
hook installer (`install`).
"""
