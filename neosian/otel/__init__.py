"""Public OTel surface: one span per agent hook event (DESIGN §3).

The API loads at call time, never at import time — `otel_hooks()`
raises a helpful ImportError when it is missing.
"""

from neosian._foundation.otel import otel_hooks

__all__ = ["otel_hooks"]
