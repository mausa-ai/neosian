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
    (ledger #218).
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


XAI = OpenAICompatible(
    name="xai",
    api_key_env="XAI_API_KEY",
    base_url="https://api.x.ai/v1",
    temperature=True,
    reasoning_field="reasoning_content",
)


GEMINI = OpenAICompatible(
    # The compat endpoint; thoughts ride ToolCall.extra (§19.3), no field.
    name="gemini",
    api_key_env="GEMINI_API_KEY",
    base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
    temperature=True,
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
