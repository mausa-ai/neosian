"""The docs door — `neosian docs` (DESIGN §14.4).

Prints shipped pages byte-exact: no rich, no wrapping, no colour — the
body is markdown a model reads, and stdout must survive a pipe. stdout
carries the artifact; stderr carries guidance (§14.1).
"""

from __future__ import annotations

import json
import sys
from typing import TextIO

from neosian._foundation.shared.docs_assets import list_topics, load_page


def run_docs(
    topic: str | None,
    *,
    json_output: bool,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> int:
    """Print a shipped page or the topic listing; return the exit code."""
    # Resolved at call time, not def time, so capture/redirection works.
    out = sys.stdout if out is None else out
    err = sys.stderr if err is None else err
    if topic is None:
        return _render_listing(json_output, out, err)
    page = load_page(topic)
    if page is None:
        # Argv tier: text on stderr even under --json (§14.1's asymmetry).
        known = ", ".join(entry.topic for entry in list_topics())
        err.write(f"error: unknown topic {topic!r}\n")
        err.write(f"hint: known topics: {known}\n")
        return 2
    if json_output:
        payload = {
            "topic": page.topic,
            "title": page.title,
            "summary": page.summary,
            "body": page.body,
        }
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0
    out.write(page.body + "\n")
    return 0


def _render_listing(json_output: bool, out: TextIO, err: TextIO) -> int:
    pages = list_topics()
    if json_output:
        payload = {
            "topics": [
                {"topic": entry.topic, "title": entry.title, "summary": entry.summary}
                for entry in pages
            ]
        }
        out.write(json.dumps(payload, ensure_ascii=False) + "\n")
        return 0
    width = max(len(entry.topic) for entry in pages)
    for entry in pages:
        out.write(f"{entry.topic.ljust(width)}  {entry.summary}\n")
    err.write("hint: neosian docs <topic> prints a page\n")
    return 0
