"""Exception hierarchy for neosian.

All exceptions are centralized here with their error messages.
Add exceptions as needed, not speculatively.

Every exception carries a stable machine `code` under a closed family
prefix and a `retryable` flag (DESIGN §5, ECOSYSTEM §6). Codes are
append-only: deprecate, never repurpose or rename. The full registry is
exported as ERROR_CODES and printed by `python -m neosian.schemas errors`.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING

from neosian._foundation.shared.exceptions.agent import (
    AgentFileNotFoundError as AgentFileNotFoundError,
    AgentInvalidConfigurationError as AgentInvalidConfigurationError,
    AgentInvalidDefinitionError as AgentInvalidDefinitionError,
    AgentLoadError as AgentLoadError,
    AgentMissingConfigurationError as AgentMissingConfigurationError,
    ConfigurationError as ConfigurationError,
    InvalidModelError as InvalidModelError,
    MissingAPIKeyError as MissingAPIKeyError,
    PromptFileNotFoundError as PromptFileNotFoundError,
    PromptInvalidYAMLError as PromptInvalidYAMLError,
    PromptLoadError as PromptLoadError,
    PromptMissingKeyError as PromptMissingKeyError,
    StructuredOutputError as StructuredOutputError,
    StructuredOutputStreamingError as StructuredOutputStreamingError,
    StructuredOutputToolsError as StructuredOutputToolsError,
)
from neosian._foundation.shared.exceptions.base import (
    NeosianError as NeosianError,
)
from neosian._foundation.shared.exceptions.blackboard import (
    BlackboardEntryNotFoundError as BlackboardEntryNotFoundError,
    BlackboardError as BlackboardError,
    BlackboardReadError as BlackboardReadError,
    BlackboardUpdateError as BlackboardUpdateError,
    FileBlackboardDirectoryNotFoundError as FileBlackboardDirectoryNotFoundError,
)
from neosian._foundation.shared.exceptions.conversation import (
    ConversationConflictError as ConversationConflictError,
    ConversationFormatUnsupportedError as ConversationFormatUnsupportedError,
    ConversationIdInvalidError as ConversationIdInvalidError,
    ConversationStoreError as ConversationStoreError,
)
from neosian._foundation.shared.exceptions.evaluation import (
    EvalCaseInvalidError as EvalCaseInvalidError,
    EvalConfigInvalidYAMLError as EvalConfigInvalidYAMLError,
    EvalConfigMissingKeyError as EvalConfigMissingKeyError,
    EvalConfigNotFoundError as EvalConfigNotFoundError,
    EvalConfigUnknownKeyError as EvalConfigUnknownKeyError,
    EvalError as EvalError,
    EvalModelUnknownError as EvalModelUnknownError,
    EvalPromptNotFoundError as EvalPromptNotFoundError,
    EvalRunError as EvalRunError,
)
from neosian._foundation.shared.exceptions.guardrails import (
    GuardrailError as GuardrailError,
    GuardrailPolicyParseError as GuardrailPolicyParseError,
    GuardrailStreamingError as GuardrailStreamingError,
)
from neosian._foundation.shared.exceptions.llm import (
    LLMError as LLMError,
    MessageSerializationError as MessageSerializationError,
    ToolCallGenerationError as ToolCallGenerationError,
    UnsupportedContentError as UnsupportedContentError,
    UnsupportedParameterError as UnsupportedParameterError,
)
from neosian._foundation.shared.exceptions.memory import (
    MemoryActorInvalidError as MemoryActorInvalidError,
    MemoryConflictError as MemoryConflictError,
    MemoryDocumentNotFoundError as MemoryDocumentNotFoundError,
    MemoryEditOnlyMountError as MemoryEditOnlyMountError,
    MemoryFormatUnsupportedError as MemoryFormatUnsupportedError,
    MemoryPathInvalidError as MemoryPathInvalidError,
    MemoryReadOnlyMountError as MemoryReadOnlyMountError,
    MemoryScopeInvalidError as MemoryScopeInvalidError,
    MemoryStoreError as MemoryStoreError,
)
from neosian._foundation.shared.exceptions.provider import (
    AuthenticationError as AuthenticationError,
    ContextWindowExceededError as ContextWindowExceededError,
    FakeScriptExhaustedError as FakeScriptExhaustedError,
    FallbackExhaustedError as FallbackExhaustedError,
    ModelFailedError as ModelFailedError,
    ProviderError as ProviderError,
)
from neosian._foundation.shared.exceptions.skills import (
    SkillDirectoryNotFoundError as SkillDirectoryNotFoundError,
    SkillDuplicateNameError as SkillDuplicateNameError,
    SkillFileNotFoundError as SkillFileNotFoundError,
    SkillInvalidFrontmatterError as SkillInvalidFrontmatterError,
    SkillLoadError as SkillLoadError,
    SkillMissingKeyError as SkillMissingKeyError,
)
from neosian._foundation.shared.exceptions.tools import (
    McpConnectionError as McpConnectionError,
    ToolExecutionError as ToolExecutionError,
    ToolInvalidArgumentsError as ToolInvalidArgumentsError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping


def _collect_error_codes() -> dict[str, type[NeosianError]]:
    registry: dict[str, type[NeosianError]] = {NeosianError.code: NeosianError}
    stack: list[type[NeosianError]] = [NeosianError]
    while stack:
        for subclass in stack.pop().__subclasses__():
            registry[subclass.code] = subclass
            stack.append(subclass)
    return registry


# The append-only machine-code registry (ECOSYSTEM §6). Hosts key i18n and
# alerting on these; `python -m neosian.schemas errors` prints it as JSON.
ERROR_CODES: Mapping[str, type[NeosianError]] = MappingProxyType(_collect_error_codes())
