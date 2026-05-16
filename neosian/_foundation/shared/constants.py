"""Constants and configuration values.

All magic numbers and hardcoded strings are centralized here.
Add constants as needed, not speculatively.
"""

from neosian._foundation.shared.types import Model


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

    # Configuration validation errors
    INVALID_MODEL: str = (
        "Invalid model: expected Model enum, got {model_type} with value '{model_value}'. "
        "Supported models: {supported_models}"
    )

    # Playground errors
    GROQ_API_KEY_MISSING: str = "GROQ_API_KEY environment variable not set"
    OPENAI_API_KEY_MISSING: str = "OPENAI_API_KEY environment variable not set"
    ANTHROPIC_API_KEY_MISSING: str = "ANTHROPIC_API_KEY environment variable not set"
    CEREBRAS_API_KEY_MISSING: str = "CEREBRAS_API_KEY environment variable not set"

    # OpenAI specific errors
    OPENAI_TEMPERATURE_NOT_SUPPORTED: str = (
        "Temperature parameter is not supported for OpenAI GPT-5 models"
    )

    # Reasoning effort errors
    REASONING_EFFORT_NOT_SUPPORTED: str = (
        "reasoning_effort is only supported for models with reasoning capability. "
        "Model '{model}' does not support reasoning_effort."
    )

    # AgentConfig reasoning_effort validation
    REASONING_EFFORT_MODEL_MISMATCH: str = (
        "reasoning_effort is only supported for models with reasoning capability. "
        "Model '{model}' does not support reasoning_effort. "
        "Supported models: {supported_models}"
    )

    # Groq MAX-to-HIGH downgrade
    REASONING_EFFORT_MAX_DOWNGRADED: str = (
        "reasoning_effort=MAX is not supported by Groq models. "
        "Downgrading to HIGH for model '{model}'."
    )

    # OpenAI MAX-to-HIGH downgrade
    REASONING_EFFORT_MAX_DOWNGRADED_OPENAI: str = (
        "reasoning_effort=MAX is not supported by OpenAI models. "
        "Downgrading to HIGH for model '{model}'."
    )

    # Cerebras MAX-to-HIGH downgrade
    REASONING_EFFORT_MAX_DOWNGRADED_CEREBRAS: str = (
        "reasoning_effort=MAX is not supported by Cerebras models. "
        "Downgrading to HIGH for model '{model}'."
    )

    # Anthropic MAX-to-HIGH downgrade (MAX is Opus 4.6 only)
    REASONING_EFFORT_MAX_DOWNGRADED_ANTHROPIC: str = (
        "reasoning_effort=MAX is only supported by Claude Opus 4.6. "
        "Downgrading to HIGH for model '{model}'."
    )

    # GPT-5-Pro only supports HIGH
    REASONING_EFFORT_FORCED_HIGH: str = (
        "Model '{model}' only supports reasoning_effort=HIGH. "
        "Forcing reasoning_effort from {requested} to HIGH."
    )

    # Max output tokens validation
    MAX_OUTPUT_TOKENS_EXCEEDED: str = (
        "max_output_tokens={requested} exceeds model '{model}' " "limit of {limit}"
    )
    MAX_OUTPUT_TOKENS_INVALID: str = "max_output_tokens must be >= 1, got {requested}"

    # Max parallel tools validation
    INVALID_MAX_PARALLEL_TOOLS: str = "max_parallel_tools must be >= 1, got {value}"

    # Provider/Fallback errors
    PROVIDER_FAILED: str = "Provider {provider} failed: {error}"
    MODEL_FAILED: str = "Model {model} failed: {error}"
    MODEL_FAILED_NO_FALLBACK: str = (
        "Model {model} failed and no fallback is configured. Error: {error}"
    )
    FALLBACK_EXHAUSTED: str = (
        "Both main model ({main_model}) and fallback model ({fallback_model}) failed. "
        "Main error: {main_error}. Fallback error: {fallback_error}"
    )
    FALLBACK_TRIGGERED: str = "Falling back from {from_model} to {to_model}: {reason}"
    FALLBACK_RETRY_MAIN: str = (
        "Retrying main model {main_model} after {successful_calls} successful fallback calls"
    )
    UNSUPPORTED_PROVIDER: str = "Unsupported provider: {provider}"
    INVALID_PROVIDER_MODEL_FORMAT: str = "Invalid provider:model format: {value}"

    # Guardrail errors
    GUARDRAIL_INPUT_BLOCKED: str = "Input blocked by {guardrail_type}: {reason}"
    GUARDRAIL_OUTPUT_BLOCKED: str = "Output blocked by {guardrail_type}: {reason}"
    GUARDRAIL_POLICY_PARSE_ERROR: str = "Failed to parse policy response: {response}"
    GUARDRAIL_POLICY_REQUIRED: str = "{mode} requires {policy_field} to be set"
    GUARDRAIL_OUTPUT_REQUIRES_BLOCKING: str = (
        "Output guardrails require stream=False. "
        "Streaming cannot be used with output guardrails because content "
        "is sent to the user before it can be checked."
    )

    # Structured output errors
    STRUCTURED_OUTPUT_REQUIRES_BLOCKING: str = (
        "Structured outputs require stream=False. "
        "Schema validation needs the complete response."
    )
    STRUCTURED_OUTPUT_INCOMPATIBLE_WITH_TOOLS: str = (
        "Structured outputs cannot be used with tool-enabled agents. "
        "The agent's tool loop requires unstructured responses for tool call detection."
    )

    # Serialization errors
    MESSAGE_SERIALIZATION_FIELD: str = (
        "Cannot serialize {context}: field '{field}' contains {value_type} "
        "(not JSON-serializable). Convert to a JSON-compatible type "
        "(str, int, float, bool, list, dict, None) before passing to neosian."
    )
    MESSAGE_SERIALIZATION_GENERIC: str = (
        "Cannot serialize {context}: contains {value_type} (not JSON-serializable). "
        "Convert to a JSON-compatible type before passing to neosian."
    )

    # Playbook loading errors
    PLAYBOOK_FILE_NOT_FOUND: str = "Playbook file not found: {path}"
    PLAYBOOK_INVALID_FRONTMATTER: str = (
        "Invalid or missing frontmatter in playbook file: {path}"
    )
    PLAYBOOK_MISSING_KEY: str = (
        "Missing required key '{key}' in playbook frontmatter: {path}"
    )
    PLAYBOOK_DUPLICATE_NAME: str = (
        "Duplicate playbook name '{name}' - playbooks must have unique names"
    )
    PLAYBOOK_DIRECTORY_NOT_FOUND: str = "Playbooks directory not found: {path}"
    PLAYBOOK_NOT_FOUND: str = "Playbook '{name}' not found"

    # Blackboard errors
    BLACKBOARD_ENTRY_NOT_FOUND: str = "Blackboard entry '{name}' not found"
    BLACKBOARD_READ_ERROR: str = "Failed to read blackboard entry '{name}': {error}"
    BLACKBOARD_UPDATE_ERROR: str = "Failed to update blackboard entry '{name}': {error}"

    # FileBlackboard-specific errors
    FILE_BLACKBOARD_DIRECTORY_NOT_FOUND: str = (
        "FileBlackboard directory not found: {path}"
    )

    # Evaluation errors
    EVAL_CONFIG_NOT_FOUND: str = "Eval config file not found: {path}"
    EVAL_CONFIG_INVALID_YAML: str = "Invalid YAML in eval config: {path}"
    EVAL_CONFIG_MISSING_KEY: str = "Missing required key '{key}' in eval config: {path}"
    EVAL_PROMPT_NOT_FOUND: str = "Prompt file not found: {path}"
    EVAL_CASE_INVALID: str = "Invalid eval case '{name}': {error}"
    EVAL_RUN_ERROR: str = (
        "Evaluation run failed for {prompt} × {model} × {case}: {error}"
    )


class LLMDefaults:
    """Default values for LLM configuration."""

    TEMPERATURE: float = 0.7
    RETRY_TEMPERATURE: float = 0.3
    MAX_TOOL_CALL_RETRIES: int = 2
    MAX_OUTPUT_TOKENS: int = 8192
    # Safety-net cap on concurrent tool execution within one assistant turn.
    # Default tuned for normal LLM emission (1-5 calls); catches runaway
    # cases. Users can override via AgentConfig.max_parallel_tools.
    MAX_PARALLEL_TOOLS: int = 10


class BuiltinTools:
    """Constants for built-in tools."""

    class Todo:
        """Todo tool constants."""

        NAME: str = "update_todo"
        DESCRIPTION: str = (
            "Update the task list with current progress. Pass the complete list of tasks - "
            "this replaces all existing tasks. Use status: 'pending' for not started, "
            "'in_progress' for current work (keep to one at a time), 'completed' when done."
        )

    class Playbook:
        """Playbook tool constants."""

        LIST_NAME: str = "list_playbooks"
        LIST_DESCRIPTION: str = (
            "List available playbooks with their names and descriptions. "
            "Use this to discover what playbooks are available before loading one."
        )
        LOAD_NAME: str = "load_playbook"
        LOAD_DESCRIPTION: str = (
            "Load a playbook by name. Returns the full instructions. "
            "Use list_playbooks first to see what's available."
        )

    class Blackboard:
        """Blackboard tool constants."""

        LIST_NAME: str = "list_blackboard"
        LIST_DESCRIPTION: str = (
            "List available blackboard entries with their names and descriptions. "
            "Blackboard contains dynamic context that may change during the session."
        )
        READ_NAME: str = "read_blackboard"
        READ_DESCRIPTION: str = (
            "Read the current content of a blackboard entry by name. "
            "Use list_blackboard first to see what's available."
        )
        UPDATE_NAME: str = "update_blackboard"
        UPDATE_DESCRIPTION: str = (
            "Update an existing blackboard entry with new content. "
            "Can only update entries that already exist, not create new ones."
        )


class PlaybookLoader:
    """Constants for playbook file loading."""

    NAME_KEY: str = "name"
    DESCRIPTION_KEY: str = "description"


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
    ANTHROPIC_API_KEY: str = "anthropic_api_key"
    CEREBRAS_API_KEY: str = "cerebras_api_key"


class EnvVars:
    """Environment variable names."""

    GROQ_API_KEY: str = "GROQ_API_KEY"
    OPENAI_API_KEY: str = "OPENAI_API_KEY"
    ANTHROPIC_API_KEY: str = "ANTHROPIC_API_KEY"
    CEREBRAS_API_KEY: str = "CEREBRAS_API_KEY"


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
    """Constants for GPT-OSS-Safeguard guardrail system."""

    # Default models
    POLICY_MODEL: Model = Model.GROQ_GPT_OSS_SAFEGUARD_20B

    # Temperature for guardrail calls (deterministic)
    TEMPERATURE: float = 0.0

    class TestPolicy:
        """Test policy constants."""

        MARKER: str = "__TEST_POLICY_50_PERCENT__"
        CATEGORY: str = "TEST"
        RATIONALE: str = "Random test flag (50% chance)"

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


class Streaming:
    """Constants for SSE streaming."""

    # Heartbeat interval during long-running tool execution (seconds)
    # Keeps SSE connections alive and prevents frontend timeout/reconnect
    HEARTBEAT_INTERVAL_SECONDS: float = 15.0


class Evaluation:
    """Constants for agent evaluation framework."""

    # Rate limit throttling (avoid 429s on fast providers like Groq)
    THROTTLE_DELAY_MS: int = 500  # 500ms = max ~120 RPM

    # Config file keys
    NAME_KEY: str = "name"
    PROMPTS_KEY: str = "prompts"
    MODELS_KEY: str = "models"
    CASES_KEY: str = "cases"
    CONVERSATION_KEY: str = "conversation"
    INPUT_KEY: str = "input"
    EXPECT_KEY: str = "expect"
    TOOL_KEY: str = "tool"
    PARAMS_KEY: str = "params"
    NO_TOOL_KEY: str = "no_tool"
    RESPONSE_KEY: str = "response"
    SEQUENCE_KEY: str = "sequence"
    USER_KEY: str = "user"
    MOCK_RESPONSE_KEY: str = "mock_response"

    # Agent key (single Python file with tool implementations)
    AGENT_KEY: str = "agent"

    # Behavior configuration keys
    STOP_ON_FAILURE_KEY: str = "stop_on_failure"

    # Prompt config YAML keys
    SYSTEM_PROMPT_KEY: str = "system_prompt"
    TOOLS_KEY: str = "tools"
    DESCRIPTION_KEY: str = "description"
    ON_SUCCESS_KEY: str = "on_success"
    COMPACT_SUMMARIZE_KEY: str = "compact_summarize"

    # Output directory
    OUTPUT_DIR: str = ".neosian/evals"

    # Default mock response
    MOCK_SUCCESS_MESSAGE: str = "Tool executed successfully"

    # Param matcher marker
    EXISTS_MARKER: str = "_exists"

    class Scorer:
        """Scorer result messages."""

        PARAM_EXISTS_FAIL: str = "expected param to exist, got None"
        PARAM_MISMATCH: str = "expected '{expected}', got '{actual}'"
        EXPECTED_NO_TOOL: str = "Expected no tool, got '{tool}'"
        WRONG_TOOL: str = "Expected '{expected}', got '{actual}'"
        NO_TOOL_CALLED: str = "Expected '{expected}', no tool called"
        SEQUENCE_MISMATCH: str = "Expected sequence {expected}, got {actual}"
        STEP_PARAM_FAIL: str = "Step {step}: {param}: {reason}"

    class UI:
        """Evaluation CLI UI constants."""

        TITLE: str = "neosian eval"
        RUNNING: str = "Running"
        RESULTS: str = "RESULTS"
        SUMMARY: str = "SUMMARY"
        FAILURES: str = "FAILURES"
        PASS: str = "PASS"
        FAIL: str = "FAIL"
        BEST_ON: str = "Best on {model}"
        BEST_OVERALL: str = "Best overall"
        DETAILED_RESULTS: str = "Detailed results"
        NO_FAILURES: str = "All cases passed!"
