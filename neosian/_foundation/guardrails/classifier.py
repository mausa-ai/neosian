"""Classifier module for Llama Guard content classification.

Handles parsing and evaluation of Llama Guard 4 responses.
"""

from groq import AsyncGroq

from neosian._foundation.shared.constants import Guardrails
from neosian._foundation.shared.exceptions import GuardrailClassifierParseError
from neosian._foundation.shared.types import ClassifierResult


def parse_classifier_response(response: str) -> ClassifierResult:
    """Parse Llama Guard response into ClassifierResult.

    Llama Guard returns:
    - "safe" for safe content
    - "unsafe\\nS1" or "unsafe\\nS1,S3" for unsafe content

    Args:
        response: Raw response from Llama Guard.

    Returns:
        ClassifierResult with safe status and categories.

    Raises:
        GuardrailClassifierParseError: If response cannot be parsed.
    """
    response = response.strip().lower()

    if response == Guardrails.ClassifierResponse.SAFE:
        return ClassifierResult(safe=True, categories=[])

    # Handle "unsafe" with or without categories
    lines = response.split("\n")
    if lines[0] == Guardrails.ClassifierResponse.UNSAFE:
        categories: list[str] = []
        if len(lines) > 1 and lines[1]:
            # Parse comma-separated categories like "S1,S3"
            raw_categories = lines[1].upper().split(",")
            categories = [c.strip() for c in raw_categories if c.strip()]

        return ClassifierResult(safe=False, categories=categories)

    # Unknown format - treat as parse error
    raise GuardrailClassifierParseError(response)


async def check_with_classifier(
    content: str,
    client: AsyncGroq,
    model: str | None = None,
) -> ClassifierResult:
    """Check content using Llama Guard classifier.

    Args:
        content: Content to classify.
        client: Groq async client.
        model: Model to use (defaults to Guardrails.CLASSIFIER_MODEL).

    Returns:
        ClassifierResult with classification.

    Raises:
        GuardrailClassifierParseError: If response cannot be parsed.
    """
    model = model or Guardrails.CLASSIFIER_MODEL

    response = await client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": content}],
        temperature=Guardrails.TEMPERATURE,
    )

    response_text = response.choices[0].message.content or ""
    return parse_classifier_response(response_text)
