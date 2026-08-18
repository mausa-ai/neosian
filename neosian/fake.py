"""Public FakeProvider surface (ECOSYSTEM §7). Import as `neosian.fake`.

Re-exports only — the implementation lives in _foundation.llm.fake. The
root package never imports this module; it loads only when you do.
"""

from neosian._foundation.llm.fake import (
    FakeCall,
    FakeClient,
    FakeScript,
    FakeTurn,
    StreamShape,
)
from neosian._foundation.shared.exceptions import FakeScriptExhaustedError

__all__ = [
    "FakeCall",
    "FakeClient",
    "FakeScript",
    "FakeScriptExhaustedError",
    "FakeTurn",
    "StreamShape",
]
