"""The schema the model gets, and the validator behind it (NF, DESIGN §27.9).

One pydantic model is synthesised from a tool's signature. Its JSON Schema
is what every provider receives — nested models, `datetime`, `UUID`,
`$defs`, nullable optionals, pydantic's own constraint vocabulary beside
neosian's — and the same model validates every call before the body runs,
so the arguments a tool receives are the ones its signature promises.
"""

import inspect
import json
import re
from collections.abc import Callable, Mapping
from typing import Annotated, Any, get_args, get_origin, get_type_hints

import annotated_types
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    create_model,
)
from pydantic.json_schema import GenerateJsonSchema
from pydantic_core import CoreSchema

from neosian._foundation.shared.constants import ErrorMessages
from neosian._foundation.shared.constraints import (
    Desc,
    Max,
    MaxLen,
    Min,
    MinLen,
    Pattern,
)
from neosian._foundation.shared.exceptions import (
    ConfigurationError,
    ToolInvalidArgumentsError,
)
from neosian._foundation.shared.schema import add_additional_properties_false
from neosian._foundation.tools.result import ToolResult

# Dispatch is ``tool(**arguments)``: only keyword-bindable parameters are legal.
_BINDABLE_KINDS = (
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.KEYWORD_ONLY,
)
_UNBINDABLE_PARAMETER = (
    "Tool {func} declares '{param}', which keyword dispatch can never bind"
)
_UNANNOTATED_PARAMETER = (
    "Tool {func} parameter '{name}' has no type annotation — the model would "
    "be told nothing true about it"
)
_UNSERIALISABLE_DEFAULT = (
    "Tool {func} parameter '{name}' has a default that is not JSON: {default!r}"
)
_UNKNOWN_PARAMS = "Tool {func} describes parameters it does not declare: {names}"

# Google-style docstrings: an ``Args:`` section of ``name: text`` or
# ``name (type): text`` lines, continuations indented deeper.
_SECTION = re.compile(r"^\w[\w ]*:$")
_ARG_LINE = re.compile(r"^(\w+)(?:\s*\([^)]*\))?:\s*(.*)$")
# Schema nodes never descend into these keys — their values are data.
_OPAQUE_KEYS = frozenset({"default", "enum", "const", "examples"})
# Subscripted at runtime with a built tuple — a special form to mypy.
_annotated: Any = Annotated


class _ToolSchema(GenerateJsonSchema):
    """pydantic's generator without field titles — tokens, not contract."""

    def field_title_should_be_set(self, schema: CoreSchema) -> bool:  # noqa: ARG002
        return False


def _translate(item: object) -> object:
    """neosian's constraint vocabulary in pydantic's; anything else passes."""
    if isinstance(item, Min):
        return annotated_types.Ge(item.value)
    if isinstance(item, Max):
        return annotated_types.Le(item.value)
    if isinstance(item, MinLen):
        return annotated_types.MinLen(item.value)
    if isinstance(item, MaxLen):
        return annotated_types.MaxLen(item.value)
    if isinstance(item, Pattern):
        return StringConstraints(pattern=item.value)
    return item


def _annotate(hint: Any, prose: str | None) -> Any:
    """The field annotation: constraints translated, the description chosen
    by precedence `Desc()` > `params=` > `Args:` (`prose` is the latter two)."""
    base: Any = hint
    metadata: list[Any] = []
    if get_origin(hint) is Annotated:
        base, *metadata = get_args(hint)
    described = [m.value for m in metadata if isinstance(m, Desc)]
    description = described[-1] if described else prose
    items = [_translate(m) for m in metadata if not isinstance(m, Desc)]
    if description is not None:
        items.append(Field(description=description))
    return _annotated[(base, *items)] if items else base


def lift_args_docstring(doc: str | None) -> dict[str, str]:
    """Parameter prose from a docstring's ``Args:`` section."""
    lifted: dict[str, str] = {}
    if not doc:
        return lifted
    lines = doc.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == "Args:")
    except StopIteration:
        return lifted
    current: str | None = None
    arg_indent = 0
    for line in lines[start + 1 :]:
        stripped = line.strip()
        if not stripped:
            continue
        if _SECTION.match(stripped):
            break
        indent = len(line) - len(line.lstrip())
        match = _ARG_LINE.match(stripped)
        if match and (current is None or indent <= arg_indent):
            current, arg_indent = match.group(1), indent
            lifted[current] = match.group(2)
        elif current is not None:
            lifted[current] = f"{lifted[current]} {stripped}".strip()
    return lifted


def build_arguments_model(
    func: Callable[..., Any], name: str, params: Mapping[str, str] | None
) -> type[BaseModel]:
    """The model of `func`'s keyword arguments, named after the tool.

    Raises `ConfigurationError` at decoration for what the schema could
    not state truthfully: an unbindable parameter kind, a missing
    annotation, a non-JSON default, a `params` name the signature lacks.
    """
    signature = inspect.signature(func)
    hints = get_type_hints(func, include_extras=True)
    lifted = lift_args_docstring(inspect.getdoc(func))
    prose = dict(params or {})
    fields: dict[str, Any] = {}
    for param_name, param in signature.parameters.items():
        if param_name in ("self", "cls"):
            continue
        if param.kind not in _BINDABLE_KINDS:
            raise ConfigurationError(
                _UNBINDABLE_PARAMETER.format(func=func.__qualname__, param=param)
            )
        if param_name not in hints:
            raise ConfigurationError(
                _UNANNOTATED_PARAMETER.format(func=func.__qualname__, name=param_name)
            )
        default = param.default
        if default is not inspect.Parameter.empty:
            try:
                json.dumps(default)
            except (TypeError, ValueError) as exc:
                raise ConfigurationError(
                    _UNSERIALISABLE_DEFAULT.format(
                        func=func.__qualname__, name=param_name, default=default
                    )
                ) from exc
        description = prose.pop(param_name, None) or lifted.get(param_name)
        annotation = _annotate(hints[param_name], description)
        fields[param_name] = (
            annotation,
            ... if default is inspect.Parameter.empty else default,
        )
    if prose:
        raise ConfigurationError(
            _UNKNOWN_PARAMS.format(func=func.__qualname__, names=sorted(prose))
        )
    return create_model(
        f"{name}_arguments",
        __config__=ConfigDict(extra="forbid", protected_namespaces=()),
        **fields,
    )


def _strip_titles(node: Any) -> None:
    if not isinstance(node, dict):
        return
    node.pop("title", None)
    for key, value in node.items():
        if key in _OPAQUE_KEYS:
            continue
        if key in ("properties", "$defs"):
            for child in value.values():
                _strip_titles(child)
        elif isinstance(value, list):
            for child in value:
                _strip_titles(child)
        else:
            _strip_titles(value)


def arguments_schema(model: type[BaseModel]) -> dict[str, Any]:
    """The JSON Schema the model gets: no titles, every object closed."""
    schema = model.model_json_schema(schema_generator=_ToolSchema)
    _strip_titles(schema)
    add_additional_properties_false(schema)
    return schema


def validate_arguments(
    model: type[BaseModel], arguments: Mapping[str, Any]
) -> dict[str, Any]:
    """The call's arguments as the signature promises them — nested models
    as instances, `datetime`/`UUID` as objects, lax coercion applied,
    defaults filled. Raises `pydantic.ValidationError`."""
    validated = model.model_validate(dict(arguments))
    return {name: getattr(validated, name) for name in type(validated).model_fields}


def describe_validation_error(exc: ValidationError) -> str:
    """One line per failing field, without pydantic's URL footer."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: "
        f"{error['msg']}"
        for error in exc.errors()
    )


def rejection(tool_name: str, exc: Exception) -> ToolResult[Any]:
    """The corrective failure for a call that did not validate or bind —
    the same text on every transport, `tool_invalid_arguments` in-band."""
    error = describe_validation_error(exc) if isinstance(exc, ValidationError) else exc
    return ToolResult.fail(
        ErrorMessages.TOOL_INVALID_ARGUMENTS.format(tool_name=tool_name, error=error),
        code=ToolInvalidArgumentsError.code,
    )
