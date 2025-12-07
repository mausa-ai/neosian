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

    # Guardrail errors
    GUARDRAIL_INPUT_BLOCKED: str = "Input blocked by {guardrail_type}: {reason}"
    GUARDRAIL_OUTPUT_BLOCKED: str = "Output blocked by {guardrail_type}: {reason}"
    GUARDRAIL_CLASSIFIER_PARSE_ERROR: str = (
        "Failed to parse classifier response: {response}"
    )
    GUARDRAIL_POLICY_PARSE_ERROR: str = "Failed to parse policy response: {response}"
    GUARDRAIL_POLICY_REQUIRED: str = "{mode} requires {policy_field} to be set"
    GUARDRAIL_OUTPUT_REQUIRES_BLOCKING: str = (
        "Output guardrails require stream=False. "
        "Streaming cannot be used with output guardrails because content "
        "is sent to the user before it can be checked."
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

            class Guardrails:
                """Production guardrail models."""

                LLAMA_GUARD_4_12B: str = "meta-llama/llama-guard-4-12b"

        class Preview:
            """Preview models (may change)."""

            LLAMA_4_MAVERICK_17B: str = "meta-llama/llama-4-maverick-17b-128e-instruct"
            LLAMA_4_SCOUT_17B: str = "meta-llama/llama-4-scout-17b-16e-instruct"
            QWEN3_32B: str = "qwen/qwen3-32b"
            KIMI_K2: str = "moonshotai/kimi-k2-instruct"
            KIMI_K2_0905: str = "moonshotai/kimi-k2-instruct-0905"

            class Guardrails:
                """Preview guardrail models."""

                GPT_OSS_SAFEGUARD_20B: str = "openai/gpt-oss-safeguard-20b"

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

    # Guardrail display
    GUARDRAIL_BLOCKED_LABEL: str = "Guardrail Blocked"
    GUARDRAIL_INPUT_BLOCKED: str = "Input blocked by safety guardrails"
    GUARDRAIL_OUTPUT_BLOCKED: str = "Output blocked by safety guardrails"
    GUARDRAIL_CATEGORIES: str = "Categories: {categories}"
    GUARDRAIL_RATIONALE: str = "Reason: {rationale}"



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


class EnvVars:
    """Environment variable names."""

    GROQ_API_KEY: str = "GROQ_API_KEY"
    OPENAI_API_KEY: str = "OPENAI_API_KEY"


class ArenaUI:
    """Constants for arena mode interface."""

    SELECT_COUNT: str = "How many models to compare?"
    COUNT_OPTIONS: tuple[str, ...] = ("2", "3")
    MODEL_LABEL: str = "Model {n}"
    SELECT_PROVIDER: str = "{label} - Select Provider:"
    SELECT_MODEL: str = "{label} - Select Model ({provider}):"
    THINKING: str = "Running {label}..."
    COLORS: tuple[str, ...] = ("cyan", "magenta", "green")


class Guardrails:
    """Constants for guardrail system."""

    # Default models (reference existing Provider constants)
    CLASSIFIER_MODEL: str = Provider.Groq.Production.Guardrails.LLAMA_GUARD_4_12B
    POLICY_MODEL: str = Provider.Groq.Preview.Guardrails.GPT_OSS_SAFEGUARD_20B

    # Temperature for guardrail calls (deterministic)
    TEMPERATURE: float = 0.0

    class ClassifierResponse:
        """Llama Guard response format constants."""

        SAFE: str = "safe"
        UNSAFE: str = "unsafe"

    class TestPolicy:
        """Test policy constants."""

        MARKER: str = "__TEST_POLICY_50_PERCENT__"
        CATEGORY: str = "TEST"
        RATIONALE: str = "Random test flag (50% chance)"

    class Categories:
        """Llama Guard 4 safety categories (S1-S14)."""

        class S1:
            CODE: str = "S1"
            NAME: str = "Violent Crimes"

        class S2:
            CODE: str = "S2"
            NAME: str = "Non-Violent Crimes"

        class S3:
            CODE: str = "S3"
            NAME: str = "Sex-Related Crimes"

        class S4:
            CODE: str = "S4"
            NAME: str = "Child Sexual Exploitation"

        class S5:
            CODE: str = "S5"
            NAME: str = "Defamation"

        class S6:
            CODE: str = "S6"
            NAME: str = "Specialized Advice"

        class S7:
            CODE: str = "S7"
            NAME: str = "Privacy"

        class S8:
            CODE: str = "S8"
            NAME: str = "Intellectual Property"

        class S9:
            CODE: str = "S9"
            NAME: str = "Indiscriminate Weapons"

        class S10:
            CODE: str = "S10"
            NAME: str = "Hate"

        class S11:
            CODE: str = "S11"
            NAME: str = "Suicide & Self-Harm"

        class S12:
            CODE: str = "S12"
            NAME: str = "Sexual Content"

        class S13:
            CODE: str = "S13"
            NAME: str = "Elections"

        class S14:
            CODE: str = "S14"
            NAME: str = "Code Interpreter Abuse"

        ALL_CODES: tuple[str, ...] = (
            "S1",
            "S2",
            "S3",
            "S4",
            "S5",
            "S6",
            "S7",
            "S8",
            "S9",
            "S10",
            "S11",
            "S12",
            "S13",
            "S14",
        )

    class PolicyPrompt:
        """Policy prompt templates for GPT-OSS-Safeguard."""

        TEMPLATE: str = (
            "# Content Policy\n\n"
            "## INSTRUCTIONS\n"
            "Classify the content against the policies below. Return JSON with:\n"
            "- violation: 1 if violates any policy, 0 if safe\n"
            '- category: The policy code (e.g., "P1") or null if safe\n'
            "- rationale: Brief explanation of your decision\n\n"
            "## POLICIES\n{policies}\n\n"
            "## OUTPUT FORMAT\n"
            "Return ONLY valid JSON: "
            '{{"violation": 0|1, "category": "CODE"|null, "rationale": "..."}}\n\n'
            "Content to evaluate:\n{content}"
        )

        CATEGORY_TEMPLATE: str = (
            "### {code}: {name}\n"
            "{description}\n"
            "VIOLATES: {violates}\n"
            "SAFE: {safe}\n"
        )
