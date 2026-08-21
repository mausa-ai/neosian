"""Public OTel surface: one span per agent hook event (DESIGN §3).

Requires the `otel` extra at call time, never at import time —
`otel_hooks()` raises a helpful ImportError without it.
"""

from neosian._foundation.otel import otel_hooks

__all__ = ["otel_hooks"]
