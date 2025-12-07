"""Constants and configuration values.

All magic numbers and hardcoded strings are centralized here.
Add constants as needed, not speculatively.
"""


class App:
    """Application metadata."""

    NAME: str = "neosian"
    DESCRIPTION: str = "Stateless agentic AI library"
    PYTHON_VERSION: str = ">=3.12"


class ErrorMessages:
    """Centralized error messages."""

    TOOL_NOT_FOUND: str = "Tool '{tool_name}' not found"
    TOOL_INVALID_ARGUMENTS: str = "Invalid arguments for tool '{tool_name}': {error}"
    TOOL_EXECUTION_FAILED: str = "Tool '{tool_name}' failed: {error}"
    FUNCTION_NOT_DECORATED: str = "Function {func_name} is not decorated with @Tool"
    TOOL_CALL_GENERATION_FAILED: str = (
        "Failed to generate valid tool call after {retries} retries"
    )

    # Prompt loading errors
    PROMPT_FILE_NOT_FOUND: str = "Prompt file not found: {path}"
    PROMPT_INVALID_YAML: str = "Invalid YAML in prompt file: {path}"
    PROMPT_MISSING_KEY: str = "Missing '{key}' in prompt file: {path}"

    # Agent loading errors
    AGENT_FILE_NOT_FOUND: str = "Agent file not found: {path}"
    AGENT_MISSING_CONFIGURATION: str = "Agent file missing 'configuration': {path}"
    AGENT_INVALID_CONFIGURATION: str = (
        "configuration must be an AgentConfig instance: {path}"
    )
    AGENT_LOAD_ERROR: str = "Failed to load agent file: {path} - {error}"

    # Playground errors
    GROQ_API_KEY_MISSING: str = "GROQ_API_KEY environment variable not set"
    OPENAI_API_KEY_MISSING: str = "OPENAI_API_KEY environment variable not set"

    # OpenAI specific errors
    OPENAI_TEMPERATURE_NOT_SUPPORTED: str = (
        "Temperature parameter is not supported for OpenAI GPT-5 models"
    )


class LLMDefaults:
    """Default values for LLM configuration."""

    TEMPERATURE: float = 0.7
    RETRY_TEMPERATURE: float = 0.3
    MAX_TOOL_CALL_RETRIES: int = 2


class Provider:
    """LLM Provider configuration."""

    class Groq:
        """Groq provider constants."""

        ID: str = "groq"
        DEFAULT_MODEL: str = "openai/gpt-oss-20b"

        class Production:
            """Production-ready models."""

            LLAMA_3_3_70B: str = "llama-3.3-70b-versatile"
            LLAMA_3_1_8B: str = "llama-3.1-8b-instant"
            GPT_OSS_120B: str = "openai/gpt-oss-120b"
            GPT_OSS_20B: str = "openai/gpt-oss-20b"
            LLAMA_GUARD_4_12B: str = "meta-llama/llama-guard-4-12b"

        class Preview:
            """Preview models (may change)."""

            LLAMA_4_MAVERICK_17B: str = "meta-llama/llama-4-maverick-17b-128e-instruct"
            LLAMA_4_SCOUT_17B: str = "meta-llama/llama-4-scout-17b-16e-instruct"
            QWEN3_32B: str = "qwen/qwen3-32b"
            KIMI_K2: str = "moonshotai/kimi-k2-instruct"
            KIMI_K2_0905: str = "moonshotai/kimi-k2-instruct-0905"

    class OpenAI:
        """OpenAI provider constants."""

        ID: str = "openai"
        DEFAULT_MODEL: str = "gpt-5-nano-2025-08-07"

        class Models:
            """OpenAI models."""

            GPT_5_1: str = "gpt-5.1-2025-11-13"  # Best for coding and agentic tasks
            GPT_5_MINI: str = "gpt-5-mini-2025-08-07"  # Faster, cost-efficient
            GPT_5_NANO: str = "gpt-5-nano-2025-08-07"  # Fastest, most cost-efficient
            GPT_5_PRO: str = "gpt-5-pro-2025-10-06"  # Smarter and more precise

    class Anthropic:
        """Anthropic provider constants."""

        ID: str = "anthropic"
        DEFAULT_MODEL: str = "claude-3-haiku-20240307"


class BuiltinTools:
    """Constants for built-in tools."""

    class Todo:
        """Todo tool constants."""

        NAME: str = "update_todo"
        DESCRIPTION: str = (
            "Track and update task progress. Use this to plan complex tasks, "
            "track what you're working on, and mark tasks complete. "
            "Each item has content (what to do) and status (pending/in_progress/completed)."
        )


class PromptLoader:
    """Constants for YAML prompt loading."""

    SYSTEM_PROMPT_KEY: str = "system_prompt"


class AgentLoader:
    """Constants for loading agent definitions from Python files."""

    CONFIGURATION_VAR: str = "configuration"
    MODULE_NAME: str = "user_agent"


class PlaygroundUI:
    """Constants for playground CLI interface."""

    TITLE: str = "neosian playground"
    AGENT_LOADED: str = "Agent: {name}"
    SESSION_START: str = "Type /exit or /quit to end session."
    USER_PROMPT: str = "You"
    ASSISTANT_LABEL: str = "Assistant"
    TOOL_CALL_LABEL: str = "Tool Call"
    TOOL_RESULT_LABEL: str = "Tool Result"
    EXIT_COMMANDS: tuple[str, ...] = ("/exit", "/quit", "/q")
    SAVE_MENU_TITLE: str = "Save conversation?"
    SAVE_OPTION_YES: str = "Yes, save to file"
    SAVE_OPTION_NO: str = "No, discard"
    SESSION_SAVED: str = "Session saved: {path}"
    SESSION_DISCARDED: str = "Session discarded."
    GOODBYE: str = "Goodbye!"
    THINKING: str = "Thinking..."


class Assets:
    """Asset file paths."""

    PACKAGE: str = "neosian.assets"
    LOGO_FILE: str = "logo_ascii_small.txt"
    ASCII_FILE: str = "ascii.txt"
    HEADER_SPACING: str = "  "


class Config:
    """Configuration file constants."""

    DIR_NAME: str = ".neosian"
    FILE_NAME: str = "config.toml"
    GROQ_API_KEY: str = "groq_api_key"
    OPENAI_API_KEY: str = "openai_api_key"
