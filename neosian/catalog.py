"""The shipped OpenAI-compatible rows (DESIGN §19.5). Import as `neosian.catalog`.

`Model`'s twins for the doors NW's gate admitted — `AgentConfig(model=GROK_4_6)`.
Re-exports only; the rows register at `import neosian` either way.
"""

from neosian._foundation.shared.catalog import GEMINI_3_7_FLASH, GROK_4_6

__all__ = ["GEMINI_3_7_FLASH", "GROK_4_6"]
