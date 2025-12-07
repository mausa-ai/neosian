"""Basic example agent for testing the playground.

Usage:
    neosian playground examples/basic_agent.py
"""

from datetime import datetime
from pathlib import Path

from neosian import (
    AgentConfig,
    CommonPolicies,
    GuardrailMode,
    GuardrailsConfig,
    PolicyBuilder,
    Tool,
    ToolResult,
)


@Tool(name="get_current_datetime", description="Get the current date and time")
async def get_current_datetime() -> ToolResult[str]:
    """Return the current date and time."""
    now = datetime.now()
    return ToolResult.ok(now.strftime("%Y-%m-%d %H:%M:%S"))


@Tool(name="read_file", description="Read the contents of a file")
async def read_file(path: str) -> ToolResult[str]:
    """Read a file from the filesystem.

    Args:
        path: Path to the file to read.
    """
    try:
        file_path = Path(path)
        if not file_path.exists():
            return ToolResult.fail(f"File not found: {path}")
        if not file_path.is_file():
            return ToolResult.fail(f"Not a file: {path}")
        content = file_path.read_text(encoding="utf-8")
        return ToolResult.ok(content)
    except PermissionError:
        return ToolResult.fail(f"Permission denied: {path}")
    except Exception as e:
        return ToolResult.fail(f"Error reading file: {e}")


@Tool(name="write_file", description="Write content to a file")
async def write_file(path: str, content: str) -> ToolResult[str]:
    """Write content to a file.

    Args:
        path: Path to the file to write.
        content: Content to write to the file.
    """
    try:
        file_path = Path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return ToolResult.ok(f"Successfully wrote {len(content)} characters to {path}")
    except PermissionError:
        return ToolResult.fail(f"Permission denied: {path}")
    except Exception as e:
        return ToolResult.fail(f"Error writing file: {e}")


@Tool(name="list_directory", description="List files in a directory")
async def list_directory(path: str = ".") -> ToolResult[list[str]]:
    """List files and directories at the given path.

    Args:
        path: Directory path to list (default: current directory).
    """
    try:
        dir_path = Path(path)
        if not dir_path.exists():
            return ToolResult.fail(f"Directory not found: {path}")
        if not dir_path.is_dir():
            return ToolResult.fail(f"Not a directory: {path}")
        items = sorted([item.name for item in dir_path.iterdir()])
        return ToolResult.ok(items)
    except PermissionError:
        return ToolResult.fail(f"Permission denied: {path}")
    except Exception as e:
        return ToolResult.fail(f"Error listing directory: {e}")


# Build a custom policy for additional protection
custom_policy = (
    PolicyBuilder()
    .add(CommonPolicies.PROMPT_INJECTION)
    .add(CommonPolicies.HARMFUL_INSTRUCTIONS)
    .add(CommonPolicies.PERSONAL_DATA_EXTRACTION)
    .build()
)

# Agent configuration - export as 'configuration'
configuration = AgentConfig(
    system_prompt="""You are a helpful assistant with access to basic utilities.

You can:
- Get the current date and time
- Read files from the filesystem
- Write content to files

Always confirm with the user before writing to files.
Be concise and helpful in your responses.""",
    tools=[
        get_current_datetime,
        read_file,
        write_file,
        list_directory,
    ],
    provider="groq",  # Options: "groq" (default), "openai"
    guardrails=GuardrailsConfig(
        # Input guardrails: classifier only (fast, no policy check)
        input_mode=GuardrailMode.CLASSIFIER_ONLY,
        block_on_input=True,
        # Output guardrails: disabled (allows streaming)
        output_mode=GuardrailMode.NONE,
    ),
)
