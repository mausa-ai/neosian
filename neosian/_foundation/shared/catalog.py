"""The doors (DESIGN §19.2, §31).

An `OpenAICompatible` door names where an OpenAI-compatible endpoint
lives, which environment variable signs requests to it, and the dialect
quirks its wire has. The three doors neosian ships live here beside the
class; the rows on them are `Model` members whose spec carries the door
(`models.py`), sealed by the fingerprint like every shipped row. A row is
earned by NW's gate — green over dispatched runs of the shipped pack
(§19.7, ledger #124) — and a red one exits whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal

from neosian._foundation.shared.exceptions import ConfigurationError

_DOOR_NAME: Final = re.compile(r"\A[a-z][a-z0-9_-]{0,63}\Z")
_ENV_NAME: Final = re.compile(r"\A[A-Z][A-Z0-9_]*\Z")


@dataclass(frozen=True, slots=True, kw_only=True)
class OpenAICompatible:
    """An OpenAI-compatible endpoint and the dialect its wire speaks.

    `name` is the provider label wherever one is rendered for a model on
    this door — errors, the ready frame, the picker. `base_url=None`
    keeps the SDK's own endpoint (OpenAI's, or `OPENAI_BASE_URL`), which
    is how a fine-tuned OpenAI id the enum lacks gets registered. The
    dialect knobs default to OpenAI's behavior; a knob is earned by a
    measured need. `json_mode="json_object"` sends structured output as
    the plain JSON mode with the schema in the system prompt (DeepSeek);
    `echo_reasoning` sends `Message.reasoning` back under
    `reasoning_field` on assistant turns — the documented 400 in tool
    loops without it (DESIGN §31.4). `reasoning_format` rides in
    `extra_body` beside a sent `reasoning_effort` (Cerebras's `parsed`
    keeps thoughts in `reasoning_field`); `retry_temperature` is the
    value resent on a tool-call 400 when a temperature was in play
    (ledger #218). `wire` names the endpoint the door speaks (§31.5,
    ledger #251): `"chat"` is Chat Completions, `"responses"` the
    Responses API run stateless (`store: false`, encrypted reasoning
    items replayed from `Message.extra["openai"]`, #252); the fields
    that name Chat Completions' own (`reasoning_field`, `echo_reasoning`,
    `reasoning_format`, `json_mode="json_object"`, `thinking_switch`)
    are refused on it. `thinking_switch` names a provider's boolean
    thinking parameter (`enable_thinking` on Model Studio), sent in
    `extra_body` as on when a `reasoning_effort` was asked and off when
    none was (ledger #254, #258).
    """

    name: str
    api_key_env: str
    base_url: str | None = None
    temperature: bool = False
    reasoning_effort: bool = True
    reasoning_field: str | None = None
    strict_schemas: bool = True
    json_mode: Literal["json_schema", "json_object"] = "json_schema"
    echo_reasoning: bool = False
    reasoning_format: str | None = None
    retry_temperature: float | None = None
    wire: Literal["chat", "responses"] = "chat"
    thinking_switch: str | None = None

    def __post_init__(self) -> None:
        if not _DOOR_NAME.match(self.name):
            raise ConfigurationError(
                f"door name {self.name!r}: use lowercase letters, digits, '-' or "
                "'_', starting with a letter (at most 64 characters)"
            )
        if not _ENV_NAME.match(self.api_key_env):
            raise ConfigurationError(
                f"door {self.name!r}: api_key_env {self.api_key_env!r} must be an "
                "environment variable name (uppercase letters, digits, '_')"
            )
        if self.base_url is not None and not self.base_url.startswith(
            ("http://", "https://")
        ):
            raise ConfigurationError(
                f"door {self.name!r}: base_url {self.base_url!r} must start with "
                "http:// or https://, or be None for the SDK's own endpoint"
            )
        if self.reasoning_field is not None and not self.reasoning_field.isidentifier():
            raise ConfigurationError(
                f"door {self.name!r}: reasoning_field {self.reasoning_field!r} must "
                "be a response field name such as 'reasoning_content'"
            )
        if self.json_mode not in ("json_schema", "json_object"):
            raise ConfigurationError(
                f"door {self.name!r}: json_mode {self.json_mode!r} must be "
                "'json_schema' (the schema on the wire) or 'json_object' (the "
                "schema in the prompt)"
            )
        if self.echo_reasoning and self.reasoning_field is None:
            raise ConfigurationError(
                f"door {self.name!r}: echo_reasoning needs a reasoning_field — "
                "the field the echo is sent under"
            )
        if self.reasoning_format is not None and not self.reasoning_effort:
            raise ConfigurationError(
                f"door {self.name!r}: reasoning_format rides beside "
                "reasoning_effort — declare reasoning_effort=True"
            )
        if self.retry_temperature is not None and not (
            self.temperature and 0.0 <= self.retry_temperature <= 2.0
        ):
            raise ConfigurationError(
                f"door {self.name!r}: retry_temperature {self.retry_temperature!r} "
                "needs temperature=True and a value between 0.0 and 2.0"
            )
        if self.thinking_switch is not None and not self.thinking_switch.isidentifier():
            raise ConfigurationError(
                f"door {self.name!r}: thinking_switch {self.thinking_switch!r} must "
                "be a request parameter name such as 'enable_thinking'"
            )
        if self.wire not in ("chat", "responses"):
            raise ConfigurationError(
                f"door {self.name!r}: wire {self.wire!r} must be 'chat' (Chat "
                "Completions) or 'responses' (the Responses API)"
            )
        if self.wire == "responses":
            chat_only = {
                "reasoning_field": self.reasoning_field,
                "echo_reasoning": self.echo_reasoning or None,
                "reasoning_format": self.reasoning_format,
                "thinking_switch": self.thinking_switch,
                "json_mode": (
                    self.json_mode if self.json_mode != "json_schema" else None
                ),
            }
            for knob, value in chat_only.items():
                if value is not None:
                    raise ConfigurationError(
                        f"door {self.name!r}: {knob} names a Chat Completions field "
                        "and is not sent on the 'responses' wire"
                    )


XAI = OpenAICompatible(
    # Responses is "the preferred way" and Chat Completions deprecated on
    # docs.x.ai (ledger #250); reasoning rides the items, no field.
    name="xai",
    api_key_env="XAI_API_KEY",
    base_url="https://api.x.ai/v1",
    temperature=True,
    wire="responses",
)


GEMINI = OpenAICompatible(
    # The compat endpoint; thoughts ride ToolCall.extra (§19.3), no field.
    name="gemini",
    api_key_env="GEMINI_API_KEY",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    temperature=True,
)


KIMI = OpenAICompatible(
    # Always reasons; the docs say "do not set temperature", and
    # reasoning_content goes back on assistant turns in tool loops.
    name="kimi",
    api_key_env="MOONSHOT_API_KEY",
    base_url="https://api.moonshot.ai/v1",
    reasoning_field="reasoning_content",
    echo_reasoning=True,
)


CEREBRAS = OpenAICompatible(
    # On the OpenAI door since NC7 (ledger #218): `parsed` keeps thoughts in
    # `reasoning`, and a tool-call 400 retries at 0.3 (LL-15).
    name="cerebras",
    api_key_env="CEREBRAS_API_KEY",
    base_url="https://api.cerebras.ai/v1",
    temperature=True,
    reasoning_field="reasoning",
    reasoning_format="parsed",
    retry_temperature=0.3,
)
