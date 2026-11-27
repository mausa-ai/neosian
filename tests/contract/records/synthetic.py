"""A seeded synthetic home in the released export format, written from
DESIGN §40.54 item 16.5 with no product code (the T1gb slice of the T1f plan).

The home is a FileStore root as `neosian export DIR` writes it: per
conversation `conversations/ID/` with `turns.jsonl`, `projections.jsonl` and
`redactions.jsonl`, and per memory scope `documents/PATH.md` and
`versions/PATH.jsonl`. The full profile has the design's shape: 210
conversations, 1,357 turns, 91,160 messages, 43,952 tool calls averaging
0.8 KiB with three quarters shell commands, 329 MiB of tool output, and
maxima of 1.2 MiB per message, 12.8 MiB per turn and 38.9 MiB per
conversation, a message, turn or conversation counted as the UTF-8 bytes of
its pieces (item 3.6's `bytes`). Three conversations are fixed and written
whole in both profiles: the largest (holding the largest turn and message),
the edges (every kind, escaped and multi-byte text, chunks cut by each
clause of the split rule, sparse numbers, redacted turns, tied projections
and 600 erasure acts with tied times) and one without turns. The one-tenth
profile scales every other count to a tenth. Every byte follows from the
seed.

Run as a script to write a home and print its manifest:
`python -m tests.contract.records.synthetic DIR --profile tenth`.
"""

from __future__ import annotations

import argparse
import base64
import json
import random
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

SEED = 20_261_128
MIB = 1_048_576
MAX_MESSAGE = 1_258_291  # 1.2 MiB
MAX_TURN = 13_421_773  # 12.8 MiB
MAX_CONVERSATION = 40_789_606  # 38.9 MiB
ORDINARY_MESSAGE = 1_100_000
ARGUMENTS_MEAN = 819  # 0.8 KiB
SHELL = 0.75
DIVISORS = {"full": 1, "tenth": 10}
EDGE_ACTS = 600

Message = dict[str, Any]


@dataclass(frozen=True)
class Totals:
    conversations: int
    turns: int
    messages: int
    calls: int
    output: int


FULL = Totals(210, 1_357, 91_160, 43_952, 329 * MIB)


@dataclass
class Manifest:
    """What the generator wrote, counted as it wrote it."""

    profile: str
    conversations: int = 0
    turns: int = 0
    redacted_turns: int = 0
    messages: int = 0
    calls: int = 0
    shell_calls: int = 0
    argument_bytes: int = 0
    output_bytes: int = 0
    largest_message: int = 0
    largest_turn: int = 0
    largest_conversation: int = 0
    projections: int = 0
    acts: int = 0
    most_acts: int = 0
    scopes: int = 0
    documents: int = 0
    versions: int = 0
    archive_bytes: int = 0


def compact(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def u(text: str) -> int:
    return len(text.encode())


def piece_bytes(message: Message) -> int:
    """The UTF-8 bytes of a message's pieces: content, calls with their
    extra, reasoning and extra, each JSON value in compact form."""
    total = 0
    content = message["content"]
    if isinstance(content, str):
        total += u(content)
    elif isinstance(content, list):
        for block in content:
            total += u(block["text"] if block["type"] == "text" else compact(block))
    for call in message["tool_calls"]:
        total += u(compact(call["arguments"]))
        total += u(compact(call["extra"])) if call.get("extra") else 0
    if message["reasoning"] is not None:
        total += u(compact(message["reasoning"]))
    return total + (u(compact(message["extra"])) if message.get("extra") else 0)


def allocate(
    total: int, weights: list[float], floor: int, cap: int | list[int]
) -> list[int]:
    """Integers from `floor` up to their cap, proportional to `weights` and
    summing to `total` exactly; the remainder goes one by one in order of
    the largest fraction."""
    count = len(weights)
    caps = cap if isinstance(cap, list) else [cap] * count
    assert floor * count <= total <= sum(caps), (total, count, floor)
    sizes = [floor] * count
    left = total - floor * count
    while left > 0:
        open_ = [at for at in range(count) if sizes[at] < caps[at]]
        weight = sum(weights[at] for at in open_)
        shares = {
            at: left * weights[at] / weight if weight else left / len(open_)
            for at in open_
        }
        given = 0
        for at in open_:
            add = min(int(shares[at]), caps[at] - sizes[at])
            sizes[at] += add
            given += add
        left -= given
        if given == 0:
            order = sorted(open_, key=lambda at: int(shares[at]) - shares[at])
            for at in order[:left]:
                sizes[at] += 1
            left -= min(left, len(order))
    assert sum(sizes) == total
    return sizes


def lognormal(rng: random.Random, count: int, sigma: float) -> list[float]:
    return [rng.lognormvariate(0.0, sigma) for _ in range(count)]


WORDS = [
    *[
        "build",
        "test",
        "cargo",
        "error",
        "warning",
        "ok",
        "passed",
        "failed",
        "src",
        "lib",
        "main",
        "mod",
    ],
    *[
        "use",
        "fn",
        "let",
        "match",
        "impl",
        "struct",
        "enum",
        "trait",
        "async",
        "await",
        "return",
    ],
    *[
        "result",
        "option",
        "some",
        "none",
        "café",
        "naïve",
        "façade",
        "日本語",
        "数据库",
        "Привет",
    ],
    *["мир", "🎉", "✓", "λ", "→", "≤", "ünïcödé"],
    '"quoted"',
    "back\\slash",
    "tab\there",
    "\x1b[32mPASS\x1b[0m",
    "\x1b[31mFAIL\x1b[0m",
    "bell\x07",
    "del\x7f",
    "line\u2028sep",
    "para\u2029sep",
    "nbsp\u00a0here",
]
SEPARATORS = [" ", " ", " ", " ", "\u3000", "\x1f", "\x85", "\x1c", "\u2009", "\t"]
TANGLE = ['"', "\\", "é", "日", "🎉", "\x01", "a", "b", "\x7f", "/", "\x1e"]
ASCII = "abcdefghijklmnopqrstuvwxyz0123456789-_./:=+,"
TOOLS = ["Read", "Edit", "Write", "Grep"]


class Texts:
    """Seeded text of an exact UTF-8 size: prose in lines, one long line
    with every kind of white space, base64 without white space, and a run
    of escaped and multi-byte characters without white space."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        lines: list[str] = []
        length = 0
        while length < 3 * MIB:
            words = rng.choices(WORDS, k=rng.randint(2, 18))
            line = " ".join(words)
            if rng.random() < 0.1:
                line = f"{rng.randint(1, 9999):>5}: {line}"
            lines.append(line + ("\r\n" if rng.random() < 0.02 else "\n"))
            length += len(lines[-1])
        self.prose_corpus = "".join(lines).encode()
        parts: list[str] = []
        for _ in range(150_000):
            word = rng.choice(WORDS).replace("\n", "")
            if rng.random() < 0.002:
                word = "z" * rng.randint(500, 6_000)
            parts.append(word + rng.choice(SEPARATORS))
        self.line_corpus = "".join(parts).encode()
        self.ascii_corpus = "".join(rng.choices(ASCII + " ", k=2 * MIB)).encode()

    def _take(self, corpus: bytes, size: int) -> str:
        if size <= 0:
            return ""
        assert size < len(corpus), size
        start = self.rng.randrange(0, len(corpus) - size)
        end = start + size
        while start < end and corpus[start] & 0xC0 == 0x80:
            start += 1
        while end > start and corpus[end] & 0xC0 == 0x80:
            end -= 1
        return corpus[start:end].decode() + "x" * (size - (end - start))

    def prose(self, size: int) -> str:
        return self._take(self.prose_corpus, size)

    def line(self, size: int) -> str:
        return self._take(self.line_corpus, size)

    def ascii(self, size: int) -> str:
        return self._take(self.ascii_corpus, size)

    def blob(self, size: int) -> str:
        raw = self.rng.randbytes(size * 3 // 4 + 3)
        return base64.b64encode(raw).decode()[:size]

    def tangle(self, size: int) -> str:
        chars = self.rng.choices(TANGLE, k=size)
        text = "".join(chars).encode()[:size]
        while text and text[-1] & 0xC0 == 0x80:
            text = text[:-1]
        while text and text[-1] >= 0xC0:
            text = text[:-1]
        decoded = text.decode()
        return decoded + "a" * (size - u(decoded))

    def kind(self, name: str) -> Callable[[int], str]:
        kinds = {"prose": self.prose, "line": self.line, "blob": self.blob}
        return {**kinds, "tangle": self.tangle}[name]


def sized_arguments(texts: Texts, name: str, size: int) -> dict[str, Any]:
    """Arguments of `name` whose compact JSON is exactly `size` UTF-8 bytes,
    the last string padded to fit."""
    rng = texts.rng
    stem = (
        f"/work/src/{rng.choice(['core', 'api', '日本', 'net'])}/m{rng.randint(1, 99)}"
    )
    templates: dict[str, tuple[dict[str, Any], str]] = {
        "Bash": (
            {"command": rng.choice(COMMANDS), "description": rng.choice(WHY)},
            "command",
        ),
        "Read": (
            {"file_path": stem + ".rs", "offset": rng.randint(1, 400)},
            "file_path",
        ),
        "Grep": ({"path": "/work", "output_mode": "content", "pattern": ""}, "pattern"),
        "Edit": (
            {"file_path": stem + ".rs", "old_string": "x", "new_string": ""},
            "new_string",
        ),
        "Write": ({"file_path": stem + ".md", "content": ""}, "content"),
    }
    arguments, padded = templates[name]
    need = size - u(compact(arguments))
    if need < 0:
        arguments, padded = {padded: ""}, padded
        need = size - u(compact(arguments))
    assert need >= 0, (name, size)
    if name in ("Write", "Edit") and need > 2_000:
        body = texts.prose(need // 2)
        while u(compact(body)) - 2 > need:
            body = body[: len(body) // 2]
        arguments[padded] += body
        need = size - u(compact(arguments))
    arguments[padded] += texts.ascii(need).replace("\\", "/")
    assert u(compact(arguments)) == size, (name, size)
    return arguments


COMMANDS = [
    "cargo test --locked -p neosian -- --nocapture",
    "ls -la /work && git status --short",
    'grep -rn "fn main" src/ | head -20',
    "printf 'café ✓\\n' > /tmp/out.txt && cat /tmp/out.txt",
    'python3 -c "print(\\"日本語\\")"',
    'make lint 2>&1 | tail -5 && echo "done\t✓"',
]
WHY = [
    "Run the tests",
    "Inspect the tree",
    "Check the output · ✓",
    'Print a "quoted" line',
]


def message(
    role: str,
    content: Any,
    calls: list[dict[str, Any]] | None = None,
    call_id: str | None = None,
    reasoning: Any = None,
    extra: Any = None,
) -> Message:
    """A message in the codec's own key order (a row's canonical form)."""
    made: Message = {
        "role": role,
        "content": content,
        "reasoning": reasoning,
        "tool_calls": calls or [],
        "tool_call_id": call_id,
    }
    if extra:
        made["extra"] = extra
    return made


def call(
    identifier: str, name: str, arguments: Any, extra: Any = None
) -> dict[str, Any]:
    made = {"id": identifier, "name": name, "arguments": arguments}
    return made | {"extra": extra} if extra else made


def stamp(rng: random.Random, seconds: int) -> str:
    day, rest = divmod(seconds, 86_400)
    text = f"2026-{9 + day // 30:02}-{1 + day % 30:02}T"
    text += f"{rest // 3600:02}:{rest // 60 % 60:02}:{rest % 60:02}"
    return text + (f".{rng.randint(1, 999_999):06}Z" if rng.random() < 0.3 else "Z")


class Writer:
    """One conversation's files, tallied into the manifest as written."""

    def __init__(self, root: Path, identifier: str, manifest: Manifest) -> None:
        self.directory = root / "conversations" / identifier
        self.directory.mkdir(parents=True)
        self.manifest = manifest
        self.bytes = 0
        self.lines: list[str] = []

    def turn(
        self,
        number: int,
        created_at: str,
        actor: str | None,
        messages: list[Message],
        redacted: bool = False,
    ) -> None:
        row = {"neosian_format": 1, "turn": number, "messages": messages}
        row |= {"created_at": created_at, "actor": actor, "redacted": redacted}
        with (self.directory / "turns.jsonl").open("a", encoding="utf-8") as file:
            file.write(compact(row) + "\n")
        tally = self.manifest
        sizes = [piece_bytes(m) for m in messages]
        tally.turns += 1
        tally.redacted_turns += redacted
        tally.messages += len(messages)
        tally.largest_message = max([tally.largest_message, *sizes])
        tally.largest_turn = max(tally.largest_turn, sum(sizes))
        self.bytes += sum(sizes)
        for made in messages:
            for each in made["tool_calls"]:
                tally.calls += 1
                tally.shell_calls += each["name"] == "Bash"
                tally.argument_bytes += u(compact(each["arguments"]))
            if made["role"] == "tool" and isinstance(made["content"], str):
                tally.output_bytes += u(made["content"])

    def close(
        self, projections: list[dict[str, Any]], acts: list[dict[str, Any]]
    ) -> None:
        for name, rows in (("projections", projections), ("redactions", acts)):
            if rows:
                text = "".join(compact({"neosian_format": 1} | r) + "\n" for r in rows)
                (self.directory / f"{name}.jsonl").write_text(text, encoding="utf-8")
        tally = self.manifest
        tally.conversations += 1
        tally.projections += len(projections)
        tally.acts += len(acts)
        tally.most_acts = max(tally.most_acts, len(acts))
        tally.largest_conversation = max(tally.largest_conversation, self.bytes)


@dataclass
class Plan:
    """One live turn: its calls, how many steps carry two, and its messages
    beyond the prompt, the calls and their results (the last a reply)."""

    calls: int
    parallel: int
    extras: int


def skeleton(
    texts: Texts, plan: Plan, names: list[str], arguments: list[int]
) -> tuple[list[Message], list[Message]]:
    """A turn's messages with every result empty: a prompt, perhaps a
    system message, steps of one or two calls each followed by their
    results, replies between steps and one at the end."""
    rng = texts.rng
    steps = [2] * plan.parallel + [1] * (plan.calls - 2 * plan.parallel)
    rng.shuffle(steps)
    extras = plan.extras - 1
    prompt = texts.prose(
        rng.randint(70_000, 200_000) if rng.random() < 0.01 else short(rng)
    )
    messages = [message("user", prompt)]
    if extras and rng.random() < 0.3:
        messages.insert(0, message("system", texts.prose(short(rng))))
        extras -= 1
    replies = sorted(rng.randrange(len(steps) + 1) for _ in range(extras))
    results: list[Message] = []
    for at, width in enumerate(steps):
        while replies and replies[0] == at:
            messages.append(message("assistant", texts.prose(short(rng))))
            replies.pop(0)
        made = []
        for _ in range(width):
            name, size = names.pop(), arguments.pop()
            identifier = f"toolu_{rng.getrandbits(96):024x}"
            made.append(call(identifier, name, sized_arguments(texts, name, size)))
        content = None if rng.random() < 0.7 else texts.prose(rng.randint(5, 300))
        messages.append(message("assistant", content, made))
        for each in made:
            results.append(message("tool", "", call_id=each["id"]))
            messages.append(results[-1])
    for _ in replies:
        messages.append(message("assistant", texts.prose(short(rng))))
    messages.append(message("assistant", texts.prose(short(rng))))
    return messages, results


def short(rng: random.Random) -> int:
    return min(int(rng.lognormvariate(5.5, 1.0)), 30_000)


def fill(texts: Texts, results: list[Message], sizes: list[int], odd: float) -> None:
    for result, size in zip(results, sizes, strict=True):
        draw = texts.rng.random()
        kind = "prose"
        if draw < odd:
            kind = ["line", "blob", "tangle"][int(draw / odd * 3)]
        result["content"] = texts.kind(kind)(size)


def tools(rng: random.Random, count: int) -> list[str]:
    """Exactly three quarters shell calls, the rest the other tools."""
    shell = round(count * SHELL)
    names = ["Bash"] * shell + [TOOLS[at % 4] for at in range(count - shell)]
    rng.shuffle(names)
    return names


def argument_sizes(rng: random.Random, names: list[str]) -> list[int]:
    """Compact argument sizes averaging 0.8 KiB, long ones in Write and Edit."""
    spread = {"Bash": (1.0, 0.8), "Read": (0.3, 0.3), "Grep": (0.3, 0.3)}
    spread |= {"Edit": (2.0, 1.2), "Write": (4.0, 1.6)}
    weights = [spread[n][0] * rng.lognormvariate(0.0, spread[n][1]) for n in names]
    return allocate(len(names) * ARGUMENTS_MEAN, weights, 60, 300_000)


def largest(root: Path, texts: Texts, manifest: Manifest) -> None:
    """24 turns of 40 calls, exactly 38.9 MiB; turn 8 is exactly 12.8 MiB
    and holds the one message of exactly 1.2 MiB."""
    rng = texts.rng
    writer = Writer(root, "cc-largest", manifest)
    rest = allocate(
        MAX_CONVERSATION - MAX_TURN, lognormal(rng, 23, 0.4), 200_000, MAX_TURN
    )
    targets = rest[:7] + [MAX_TURN] + rest[7:]
    for number, target in enumerate(targets, 1):
        names = tools(rng, 40)
        messages, results = skeleton(
            texts, Plan(40, 0, 3), names, argument_sizes(rng, names)
        )
        left = target - sum(piece_bytes(m) for m in messages)
        if target == MAX_TURN:
            sizes = [MAX_MESSAGE] + allocate(
                left - MAX_MESSAGE, lognormal(rng, 39, 1.0), 1, MAX_MESSAGE
            )
            fill(texts, results, sizes, 0.0)
            for result, kind in zip(
                results[1:4], ["blob", "line", "tangle"], strict=True
            ):
                result["content"] = texts.kind(kind)(u(result["content"]))
        else:
            fill(
                texts,
                results,
                allocate(left, lognormal(rng, 40, 1.0), 1, MAX_MESSAGE),
                0.1,
            )
        writer.turn(number, stamp(rng, 3_600 * number), "claude-code:7f3a", messages)
    entries = [(8, "log", 2), (24, "epoch", 24), (3, "digest", 1)]
    writer.close([projection(t, k, texts.prose(4_000), s) for t, k, s in entries], [])


def projection(turn: int, kind: str, text: str, span: int) -> dict[str, Any]:
    return {"turn": turn, "kind": kind, "text": text, "span": span}


def act(created_at: str, actor: str | None, turns: list[int]) -> dict[str, Any]:
    return {"created_at": created_at, "actor": actor, "turns": turns}


IMAGE = {
    "type": "image",
    "media_type": "image/png",
    "data": "iVBORw0KGgo=",
    "url": None,
}
LINKED = {
    "type": "image",
    "media_type": None,
    "data": None,
    "url": "https://example.invalid/a.png",
}
PDF = {
    "type": "document",
    "media_type": "application/pdf",
    "data": "JVBERi0x",
    "url": None,
}
FOLDED = {
    "type": "compaction",
    "content": "prior evidence",
    "encrypted_content": "opaque",
}
ESCAPED = 'Quote "this", C:\\Users\\x, tab\there, bell\x07, unit\x1f, line\u2028sep, 🎉, NEL\x85.'


def edges(root: Path, texts: Texts, manifest: Manifest) -> None:
    """Every kind and content form, the split rule's three clauses on
    escaped and multi-byte text, long arguments, an empty result, sparse
    numbers, redacted turns, tied projections and 600 acts with tied times."""
    writer = Writer(root, "cc-edges", manifest)
    actor = "codex:edges"

    def ask(identifier: str, name: str, arguments: Any, extra: Any = None) -> Message:
        return message("assistant", None, [call(identifier, name, arguments, extra)])

    turns: dict[int, list[Message]] = {
        1: [
            message("system", "Be brief. Répondez en français. 日本語も可。"),
            message(
                "user", [{"type": "text", "text": "Look · café ✓"}, IMAGE, PDF, LINKED]
            ),
            message(
                "assistant",
                [FOLDED],
                [call("e1", "Bash", {"command": "ls -la"}, {"signature": "sig"})],
                reasoning="recorded reasoning",
                extra={"provider_field": {"preserved": True}},
            ),
            message("tool", "total 0\n-rw-r--r-- a.txt\n", call_id="e1"),
            message("assistant", "Done."),
        ],
        3: [
            message("user", ESCAPED),
            message(
                "assistant",
                None,
                [
                    call(
                        "e2", "Bash", {"command": texts.prose(120_000), "timeout": 600}
                    ),
                    call("e3", "Read", {"file_path": "/work/日本/ファイル.txt"}),
                ],
            ),
            message("tool", "", call_id="e2"),
            message("tool", texts.tangle(150_000), call_id="e3"),
            message(
                "assistant", "", reasoning={"summary": [{"text": "both"}], "id": "rs_1"}
            ),
            message("assistant", None, reasoning="only reasoning here"),
            message("assistant", texts.prose(3_000)),
        ],
        5: [
            message("user", texts.prose(90_000)),
            message(
                "assistant",
                "Writing the file.",
                [
                    call(
                        "e4",
                        "Write",
                        {"file_path": "/work/n.md", "content": texts.prose(300_000)},
                    )
                ],
            ),
            message("tool", "File written.", call_id="e4"),
            message("assistant", "Written."),
        ],
        7: [
            message("user", "run it"),
            ask("e5", "Bash", {"command": "cat big.log"}),
            message("tool", texts.line(200_000), call_id="e5"),
            message("assistant", "That is the log."),
        ],
        9: [
            message("user", "show the image bytes"),
            ask("e6", "Bash", {"command": "base64 image.png"}),
            message("tool", texts.blob(300_000), call_id="e6"),
            message(
                "assistant", [{"type": "text", "text": texts.prose(70_000)}, IMAGE]
            ),
        ],
        10: [message("user", "thanks"), message("assistant", texts.prose(70_000))],
        11: [
            message("user", "two at once"),
            message(
                "assistant",
                "Both.",
                [
                    call("e7", "Grep", {"pattern": "fn \\w+", "path": "/work"}),
                    call("e8", "Bash", {"command": "pwd"}, {"cache": {"hit": False}}),
                ],
            ),
            message("tool", "src/a.rs:1:fn main", call_id="e7"),
            message("tool", "/work\n", call_id="e8"),
            message("assistant", "Here.", extra={"stop": "end_turn", "usage": [1, 2]}),
        ],
        12: [
            message("user", "colours"),
            ask("e9", "Bash", {"command": "make test"}),
            message("tool", texts.prose(80_000).replace("\n", "\r\n"), call_id="e9"),
            message("assistant", "Green."),
        ],
    }
    for number in sorted([*turns, 2, 6, 13]):
        when = f"2026-08-{number + 1:02}T09:00:00.{number:06}Z"
        writer.turn(number, when, actor, turns.get(number, []), number not in turns)
    entries = [
        projection(12, "digest", '"escaped"\ttext', 4),
        projection(3, "log", "Café log ✓", 1),
        projection(9, "log", texts.prose(120_000), 2),
        projection(3, "digest", "digest of three", 1),
        projection(13, "epoch", "[redacted]", 3),
        projection(9, "log", "a second log on 9, spanning 2 (a tie)", 2),
        projection(5, "epoch", "epoch 1 to 5 日本", 5),
        projection(1, "digest", "first", 1),
    ]
    writer.close(entries, edge_acts(texts.rng))


def edge_acts(rng: random.Random) -> list[dict[str, Any]]:
    """600 acts naming the redacted turns, their times going back and
    forth; positions 100 and 101, and 499 to 502 across the old page of
    500, share a time; every seventh has no actor."""
    rows = []
    for index in range(EDGE_ACTS):
        second = (index * 37) % 3_600
        when = f"2026-08-20T{8 + second // 3_600:02}:{second // 60 % 60:02}:{second % 60:02}"
        when += ".500000Z" if index % 5 == 0 else "Z"
        if index in (100, 101):
            when = "2026-08-21T00:00:00Z"
        if 499 <= index <= 502:
            when = "2026-08-19T23:59:59Z"
        actor = None if index % 7 == 3 else f"cli:act-{index:03}"
        rows.append(act(when, actor, rng.choice([[2], [6], [13], [2, 6], [6, 13]])))
    return rows


def turnless(root: Path, manifest: Manifest) -> None:
    writer = Writer(root, "cc-turnless", manifest)
    entry = projection(3, "epoch", "kept ✓", 1)
    writer.close([entry], [act("2026-09-02T00:00:00Z", None, [3])])


def scaled(full: int, fixed: int, divisor: int) -> int:
    return round((full - fixed) / divisor)


def ordinary(root: Path, texts: Texts, manifest: Manifest, divisor: int) -> None:
    """The conversations beyond the fixed three, their counts the full
    shape less the fixed ones', divided by the profile's divisor."""
    rng = random.Random(SEED + divisor)
    count = scaled(FULL.conversations, manifest.conversations, divisor)
    turn_counts = allocate(
        scaled(FULL.turns, manifest.turns, divisor), lognormal(rng, count, 0.9), 1, 80
    )
    layout: list[list[tuple[int, bool]]] = []
    for many in turn_counts:
        sparse, number, numbers = rng.random() < 0.1, 0, []
        for _ in range(many):
            number += 1 + (rng.randint(1, 3) if sparse and rng.random() < 0.2 else 0)
            numbers.append((number, many > 1 and rng.random() < 0.012))
        layout.append(numbers)
    live = sum(not redacted for numbers in layout for _, redacted in numbers)
    calls = allocate(
        scaled(FULL.calls, manifest.calls, divisor), lognormal(rng, live, 1.0), 0, 600
    )
    messages = scaled(FULL.messages, manifest.messages, divisor)
    parallel_total = max(0, round(3.1 * live + 2 * sum(calls) - messages))
    parallel = allocate(
        parallel_total, [float(c) for c in calls], 0, [c // 2 for c in calls]
    )
    extras_total = (
        messages - live - sum(2 * c - p for c, p in zip(calls, parallel, strict=True))
    )
    extras = allocate(extras_total, lognormal(rng, live, 0.5), 1, 60)
    outputs = allocate(
        scaled(FULL.output, manifest.output_bytes, divisor),
        lognormal(rng, sum(calls), 1.7),
        1,
        ORDINARY_MESSAGE,
    )
    outputs.reverse()
    plans = [Plan(c, p, e) for c, p, e in zip(calls, parallel, extras, strict=True)]
    plans.reverse()
    for index, numbers in enumerate(layout):
        identifier = f"cc-{rng.getrandbits(32):08x}-{index:03}"
        writer = Writer(root, identifier, manifest)
        actor = rng.choice(["claude-code:a1b2", "claude-code:c3d4", "codex:2", None])
        clock = 3 * 86_400 + index * 9_000
        acts = []
        for number, redacted in numbers:
            clock += rng.randint(30, 900)
            if redacted:
                writer.turn(number, stamp(rng, clock), actor, [], True)
                acts.append(act(stamp(rng, clock + 86_400), "cli:erase", [number]))
                continue
            plan = plans.pop()
            names = tools(rng, plan.calls)
            turn, results = skeleton(texts, plan, names, argument_sizes(rng, names))
            fill(texts, results, [outputs.pop() for _ in results], 0.02)
            writer.turn(number, stamp(rng, clock), actor, turn)
        entries = []
        if rng.random() < 0.3:
            for _ in range(rng.randint(1, 5)):
                at = rng.choice(numbers)[0]
                kind = rng.choice(["log", "digest", "epoch"])
                entries.append(
                    projection(
                        at,
                        kind,
                        texts.prose(rng.randint(10, 3_000)),
                        rng.randint(1, min(at, 4)),
                    )
                )
        writer.close(entries, acts)
    assert not plans and not outputs


def scopes(
    root: Path, texts: Texts, manifest: Manifest, count: int, documents: int
) -> None:
    """Memory scopes in the FileStore layout, each document with one to
    four versions, one large document per scope."""
    rng = texts.rng
    names = [f"user:home-{at}" for at in range(count - 1)] + ["org:synthetic/team:core"]
    for scope in names:
        directory = root.joinpath(
            *[part.replace(":", "%3A") for part in scope.split("/")]
        )
        for index in range(documents):
            path = f"notes/topic-{index:02}" if index % 3 else f"journal/day-{index:02}"
            clock = 10 * 86_400 + index * 3_600
            rows: list[dict[str, Any]] = []
            for version in range(1, rng.randint(1, 4) + 1):
                clock += rng.randint(60, 7_200)
                size = 120_000 if index == 0 and version == 1 else short(rng)
                rows.append(
                    {"neosian_format": 1, "path": path, "version": version}
                    | {"action": "created" if version == 1 else "modified"}
                    | {
                        "content": texts.prose(size),
                        "actor": f"cli:synthetic-{rng.randint(1, 4)}",
                    }
                    | {"created_at": stamp(rng, clock), "redacted": False}
                )
            journal = directory / "versions" / f"{path}.jsonl"
            journal.parent.mkdir(parents=True, exist_ok=True)
            journal.write_text(
                "".join(compact(r) + "\n" for r in rows), encoding="utf-8"
            )
            head = (
                f"---\nneosian_format: 1\nversion: {len(rows)}\n"
                f"created_at: '{rows[0]['created_at']}'\n"
                f"updated_at: '{rows[-1]['created_at']}'\n"
                f"actor: {rows[-1]['actor']}\n---\n"
            )
            document = directory / "documents" / f"{path}.md"
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text(head + rows[-1]["content"], encoding="utf-8")
            manifest.documents += 1
            manifest.versions += len(rows)
        manifest.scopes += 1


def write_home(root: Path, profile: str = "tenth") -> Manifest:
    """Writes the profile's home under `root`, which must not exist."""
    divisor = DIVISORS[profile]
    root.mkdir(parents=True)
    manifest = Manifest(profile)
    texts = Texts(random.Random(SEED))
    largest(root, texts, manifest)
    edges(root, texts, manifest)
    turnless(root, manifest)
    ordinary(root, texts, manifest, divisor)
    scopes(root, texts, manifest, 8 if divisor == 1 else 3, 30 if divisor == 1 else 10)
    manifest.archive_bytes = sum(
        p.stat().st_size for p in root.rglob("*") if p.is_file()
    )
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("root", type=Path)
    parser.add_argument("--profile", choices=sorted(DIVISORS), default="tenth")
    options = parser.parse_args()
    print(json.dumps(asdict(write_home(options.root, options.profile)), indent=2))


if __name__ == "__main__":
    main()
