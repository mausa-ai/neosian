"""Constants and configuration values.

All magic numbers and hardcoded strings are centralized here.
Add constants as needed, not speculatively.
"""


class ErrorMessages:
    """Centralized error messages."""

    TOOL_NOT_FOUND: str = "Tool '{tool_name}' not found"
    TOOL_INVALID_ARGUMENTS: str = "Invalid arguments for tool '{tool_name}': {error}"
    TOOL_EXECUTION_FAILED: str = "Tool '{tool_name}' failed: {error}"
    FUNCTION_NOT_DECORATED: str = "Function {func_name} is not decorated with @Tool"


class Provider:
    """LLM Provider configuration."""

    class Groq:
        """Groq provider constants."""

        ID: str = "groq"
        DEFAULT_MODEL: str = "llama-3.3-70b-versatile"

    class OpenAI:
        """OpenAI provider constants."""

        ID: str = "openai"
        DEFAULT_MODEL: str = "gpt-4o-mini"

    class Anthropic:
        """Anthropic provider constants."""

        ID: str = "anthropic"
        DEFAULT_MODEL: str = "claude-3-haiku-20240307"
