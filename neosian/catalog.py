"""The shipped door rows, by their pre-§31 names. Import as `neosian.catalog`.

Every shipped row is a `Model` member since NW1; these aliases keep the
`neosian.catalog` spelling working through 1.x — `GROK_4_6 is Model.GROK_4_6`.
"""

from neosian._foundation.shared.models import Model

GROK_4_6 = Model.GROK_4_6
GEMINI_3_7_FLASH = Model.GEMINI_3_7_FLASH

__all__ = ["GEMINI_3_7_FLASH", "GROK_4_6"]
