"""Groq suite guard: every test here needs GROQ_API_KEY.

test_agent.py and test_session.py build agents that read the key from the
environment rather than through a client fixture, so the skip must be
autouse — per-test at setup, never module-level (DESIGN §10).
"""

import pytest


@pytest.fixture(autouse=True)
def _require_groq_key(groq_api_key: str) -> None:
    del groq_api_key
