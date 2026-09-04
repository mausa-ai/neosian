"""External tests for the Anthropic client (multimodal input, structured output).

Requires a real API key:
- ANTHROPIC_API_KEY: Anthropic API key

Run with:
    ANTHROPIC_API_KEY=sk-ant-xxx uv run pytest -m external_anthropic -v
"""

import base64

import pytest
from pydantic import BaseModel

from neosian._foundation.agent.base import Agent
from neosian._foundation.llm.anthropic import AnthropicClient
from neosian._foundation.llm.base import (
    DocumentBlock,
    Message,
    Role,
    TextBlock,
    text_of,
)
from neosian._foundation.shared.schema import validate_json
from neosian._foundation.shared.types import (
    AgentConfig,
    Model,
    ResponseFormat,
)

# Distinctive token the model must reproduce verbatim in its transcription.
_SENTINEL = "NEOSIAN-7391-MULTIMODAL"


def _build_tiny_pdf(text: str) -> bytes:
    """Assemble a minimal 1-page PDF containing `text`, with no dependencies.

    Builds the four classic objects (catalog, pages, page, font) plus a
    Helvetica content stream, and computes correct xref byte offsets by hand.
    `text` must not contain parentheses or backslashes (PDF string syntax).
    """
    stream_content = f"BT /F1 24 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length "
        + str(len(stream_content)).encode()
        + b" >>\nstream\n"
        + stream_content
        + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref_pos = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref_pos}\n%%EOF\n"
    ).encode()
    return bytes(out)


class TestAnthropicMultimodal:
    """Real-API tests for document input."""

    @pytest.mark.asyncio
    async def test_pdf_document_transcription(
        self, anthropic_client: AnthropicClient
    ) -> None:
        """A 1-page PDF is transcribed and the sentinel text survives."""
        pdf_b64 = base64.standard_b64encode(_build_tiny_pdf(_SENTINEL)).decode()

        messages = [
            Message(
                role=Role.USER,
                content=[
                    DocumentBlock(media_type="application/pdf", data=pdf_b64),
                    TextBlock(
                        text=(
                            "Transcribe this document to markdown. "
                            "Output only the transcribed text."
                        )
                    ),
                ],
            ),
        ]

        response = await anthropic_client.complete(
            messages=messages,
            model=Model.CLAUDE_HAIKU_4_5,
        )

        assert _SENTINEL in text_of(response.message)
        assert response.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_pdf_transcription_via_agent_session(
        self,
        anthropic_api_key: str,  # noqa: ARG002 - gates on key presence
    ) -> None:
        """End-to-end: the one-shot extraction shape downstream projects use."""
        pdf_b64 = base64.standard_b64encode(_build_tiny_pdf(_SENTINEL)).decode()

        config = AgentConfig(
            system_prompt="You transcribe documents to markdown. "
            "Output only the transcribed text.",
            tools=[],
            enable_todo=False,
            model=Model.CLAUDE_HAIKU_4_5,
            cache_conversation=False,
        )
        agent = Agent(config=config)

        async with agent.session() as session:
            response = await session.run(
                [
                    Message(
                        role=Role.USER,
                        content=[
                            DocumentBlock(media_type="application/pdf", data=pdf_b64),
                            TextBlock(text="Transcribe this document."),
                        ],
                    ),
                ],
                stream=False,
            )

        assert _SENTINEL in text_of(response.message)
        assert response.stop_reason is not None


class _QuizQuestion(BaseModel):
    """Nested child model — forces Pydantic to emit $defs."""

    question: str
    options: list[str]


class _Quiz(BaseModel):
    """Parent model containing a nested model."""

    questions: list[_QuizQuestion]


class TestAnthropicStructuredOutputNested:
    """A nested BaseModel must round-trip through the real API.

    Regression: $defs objects shipped without additionalProperties, so any
    nested schema 400'd. This is the case that would have caught it.
    """

    @pytest.mark.asyncio
    async def test_nested_model_round_trips(
        self, anthropic_client: AnthropicClient
    ) -> None:
        """Nested schema is accepted and the response validates against it."""
        response = await anthropic_client.complete(
            messages=[
                Message(
                    role=Role.USER,
                    content=(
                        "Write exactly 2 quiz questions about gradient descent, "
                        "each with 4 options."
                    ),
                )
            ],
            model=Model.CLAUDE_HAIKU_4_5,
            response_format=ResponseFormat(schema=_Quiz),
            max_tokens=2000,
        )

        quiz = validate_json(_Quiz, text_of(response.message))
        assert isinstance(quiz, _Quiz)
        assert len(quiz.questions) == 2
        assert all(q.question and q.options for q in quiz.questions)

    @pytest.mark.asyncio
    async def test_nested_model_round_trips_non_strict(
        self, anthropic_client: AnthropicClient
    ) -> None:
        """Regression: strict=False used to skip the root patch and 400."""
        response = await anthropic_client.complete(
            messages=[
                Message(
                    role=Role.USER,
                    content="Write exactly 1 quiz question about MSE, 3 options.",
                )
            ],
            model=Model.CLAUDE_HAIKU_4_5,
            response_format=ResponseFormat(schema=_Quiz, strict=False),
            max_tokens=1000,
        )

        quiz = validate_json(_Quiz, text_of(response.message))
        assert isinstance(quiz, _Quiz)
        assert len(quiz.questions) >= 1
