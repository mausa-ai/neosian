"""Evaluation harness (DESIGN §13).

The public surface is `neosian.evaluation` — a plain re-export facade,
never imported by the root package. Internal callers import the
submodules directly; this package module deliberately re-exports
nothing, so the facade stays the one public door.
"""
