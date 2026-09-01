"""The five candidate doors awaiting NW's gate (DESIGN §19.7, NC2 slice B).

Data at import, registered inside a test: `pytest -m 'not external'` still
imports this tree at collection, and a module-level `register_model` would
leak rows into the unit tier's empty registry. `register` is write-once
idempotent, so every test may call it. A candidate becomes a shipped
catalog row only once green over two dispatched runs (ledger #122); its
ids, limits and knobs come from the provider docs as read 2026-09-01 and
are corrected by what the live probes find — never pricing, which enters
at promotion where the fingerprint seals it.
"""

import os
from dataclasses import dataclass

from neosian import OpenAICompatible, RegisteredModel, register_model


@dataclass(frozen=True, slots=True, kw_only=True)
class Candidate:
    door: OpenAICompatible
    model: str
    context_window: int
    max_output_tokens: int
    supports_reasoning: bool = False
    extra_env: tuple[str, ...] = ()  # beyond the door's key (qwen's workspace)

    @property
    def name(self) -> str:
        """The suite, its marker, the CI matrix entry — and the door."""
        return self.door.name

    @property
    def key_fixture(self) -> str:
        return f"{self.name}_api_key"

    @property
    def env(self) -> tuple[str, ...]:
        """Every env name the lane injects."""
        return (self.door.api_key_env, *self.extra_env)


XAI = Candidate(
    door=OpenAICompatible(
        name="xai",
        api_key_env="XAI_API_KEY",
        base_url="https://api.x.ai/v1",
        temperature=True,
        reasoning_field="reasoning_content",
    ),
    model="grok-4.6",
    context_window=500_000,
    max_output_tokens=32_768,  # unpublished — a conservative ceiling
    supports_reasoning=True,
)

GEMINI = Candidate(
    # The compat endpoint first; native later only if a feature needs it.
    # Thoughts surface only through extra_body, so the door has no field.
    door=OpenAICompatible(
        name="gemini",
        api_key_env="GEMINI_API_KEY",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        temperature=True,
    ),
    model="gemini-3.7-flash",
    context_window=1_048_576,
    max_output_tokens=65_536,
    supports_reasoning=True,
)

DEEPSEEK = Candidate(
    # Thinking is the default and ignores temperature — refuse rather than
    # pretend. Effort is low/high/max (no medium); `max` never reaches a
    # door (§19.7).
    door=OpenAICompatible(
        name="deepseek",
        api_key_env="DEEPSEEK_API_KEY",
        base_url="https://api.deepseek.com",
        reasoning_field="reasoning_content",
    ),
    model="deepseek-v4-pro",
    context_window=1_000_000,
    max_output_tokens=384_000,
    supports_reasoning=True,
)

# Model Studio's host is workspace-scoped; without the id the lane skips
# and this placeholder host is never contacted.
_WORKSPACE = os.environ.get("DASHSCOPE_WORKSPACE_ID", "unset")

QWEN = Candidate(
    # Thinking rides `enable_thinking` in extra_body, not reasoning_effort.
    door=OpenAICompatible(
        name="qwen",
        api_key_env="DASHSCOPE_API_KEY",
        base_url=(
            f"https://{_WORKSPACE}.ap-southeast-1.maas.aliyuncs.com"
            "/compatible-mode/v1"
        ),
        temperature=True,
        reasoning_effort=False,
        reasoning_field="reasoning_content",
    ),
    model="qwen3.8-max",
    context_window=1_000_000,
    max_output_tokens=131_072,
    extra_env=("DASHSCOPE_WORKSPACE_ID",),
)

KIMI = Candidate(
    # Always reasons; the docs say "do not set temperature".
    door=OpenAICompatible(
        name="kimi",
        api_key_env="MOONSHOT_API_KEY",
        base_url="https://api.moonshot.ai/v1",
        reasoning_field="reasoning_content",
    ),
    model="kimi-k3",
    context_window=1_048_576,
    max_output_tokens=131_072,  # unpublished — a conservative ceiling
    supports_reasoning=True,
)

CANDIDATES: tuple[Candidate, ...] = (XAI, GEMINI, DEEPSEEK, QWEN, KIMI)


def register(candidate: Candidate) -> RegisteredModel:
    """Register the candidate for this test; write-once makes it idempotent."""
    return register_model(
        candidate.model,
        provider=candidate.door,
        context_window=candidate.context_window,
        max_output_tokens=candidate.max_output_tokens,
        supports_reasoning=candidate.supports_reasoning,
    )
