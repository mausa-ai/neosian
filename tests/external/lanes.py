"""The door lanes of the external tier: the shipped door rows and the
candidates still at NW's gate (DESIGN §19.7, §31).

One shape either way — the same probes, the same pack, the same pacer. A
shipped row is the `Model` member itself; a candidate is built here and
registered inside a test (`pytest -m 'not external'` imports this tree
at collection, and a module-level registration would leak past the unit
tier's "nothing beyond the shipped set" pins — `_register` is write-once
idempotent, so every test may call `registered()`). A candidate carries
no pricing: the number is sealed at promotion, where the fingerprint
takes it. `requests_per_minute` is the lane's account tier, found by the
first probes and paced in `pacing.py` — never a door knob (a limit is an
account property, not a dialect). `budget_seconds` is the in-loop
ceiling a lane's board runs under (`board.py`, §31.4): inside
`conftest.py`'s 3600 s outer mark, so an unfinished cell is a recorded
`timeout` red, never an idle runner; a lane that splits its board per
transport (a paced one, or a slow local server) fits each part in the
budget. A keyless lane (`api_key_env=None`, DESIGN §31.6) is credentialed
by a URL, not a key: `NEOSIAN_TEST_LOCAL_URL` names the local server.
"""

import os
from dataclasses import dataclass

from neosian import AnyModel, Model, OpenAICompatible, Provider, RegisteredModel
from neosian._foundation.shared.models import ModelSpec
from neosian._foundation.shared.registry import _register

LOCAL_URL_ENV = "NEOSIAN_TEST_LOCAL_URL"
LOCAL_MODEL_ENV = "NEOSIAN_TEST_LOCAL_MODEL"  # the server's name for the weights


@dataclass(frozen=True, slots=True, kw_only=True)
class Lane:
    model: AnyModel
    requests_per_minute: int | None = None  # the account's tier; see pacing.py
    budget_seconds: float = 3000.0  # the in-loop ceiling per board (board.py)
    split_transports: bool = False  # one board per transport (ledger #211)

    @property
    def door(self) -> OpenAICompatible:
        assert self.model.door is not None  # a lane is a door's
        return self.model.door

    @property
    def name(self) -> str:
        """The suite, its marker, the CI matrix entry — and the door."""
        return self.door.name

    @property
    def keyless(self) -> bool:
        return self.door.api_key_env is None

    @property
    def key_fixture(self) -> str:
        """The fixture that skips the lane when its credential is unset."""
        return f"{self.name}_url" if self.keyless else f"{self.name}_api_key"

    def env_patch(self, credential: str) -> dict[str, str]:
        """The environment a lane's run sees: its key alone, or nothing."""
        env = self.door.api_key_env
        return {} if env is None else {env: credential}

    def registered(self) -> AnyModel:
        """The shipped row itself; a candidate registers for this test."""
        if isinstance(self.model, RegisteredModel):
            return _register(self.model)
        return self.model


def _candidate(
    value: str,
    door: OpenAICompatible,
    *,
    context_window: int,
    max_output_tokens: int,
    supports_reasoning: bool = False,
) -> RegisteredModel:
    spec = ModelSpec(
        provider=Provider.OPENAI_COMPATIBLE,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        supports_reasoning=supports_reasoning,
        door=door,
    )
    return RegisteredModel(value, spec)


CEREBRAS = Lane(
    # The shipped door since NC7 (#218). Its boards are the two adapter-row
    # cases of the baselines (a row is provider+model), so it probes only.
    model=Model.CEREBRAS_GPT_OSS_120B,
)

XAI = Lane(model=Model.GROK_4_6)

GEMINI = Lane(
    # The measured Gemini row since NW1 (#210); 3.7 rides the catalog probe.
    model=Model.GEMINI_3_8_FLASH,
    requests_per_minute=5,  # the project's tier, per model (probed 2026-09-01)
    split_transports=True,
)

KIMI = Lane(
    # Shipped on the user's ruling (ledger #124, 2026-09-15).
    model=Model.KIMI_K3,
    requests_per_minute=3,  # the organisation's tier (probed 2026-09-01)
    split_transports=True,
)

QWEN = Lane(
    # Shipped on the user's ruling (ledger #259, 2026-09-16) after its
    # third board; the switch is the door's (#258).
    model=Model.QWEN_3_8_MAX,
)

LOCAL = Lane(
    # NW2's local row (ledger #285): Gemma 4 E4B on a llama-server, keyless,
    # a candidate by construction (no card to seal, no fixed endpoint). The
    # URL is read with `or`: CI sets the variable to "" on every other
    # matrix entry, and an empty base_url is refused at import. The model
    # id is the server's name for the weights (Ollama's is `gemma4:e4b`),
    # so the Ollama recipe is probed through the same lane.
    model=_candidate(
        os.environ.get(LOCAL_MODEL_ENV) or "gemma-4-e4b-it",
        OpenAICompatible(
            name="local",
            api_key_env=None,
            base_url=os.environ.get(LOCAL_URL_ENV) or "http://127.0.0.1:8080/v1",
            reasoning_effort=False,
            reasoning_field="reasoning_content",
        ),
        context_window=32_768,  # the server's `-c`
        max_output_tokens=8_192,
    ),
    split_transports=True,  # a CPU runner is slow, not rate-limited
)

LANES: tuple[Lane, ...] = (CEREBRAS, XAI, GEMINI, KIMI, QWEN, LOCAL)
BOARD_LANES: tuple[Lane, ...] = tuple(lane for lane in LANES if lane is not CEREBRAS)
