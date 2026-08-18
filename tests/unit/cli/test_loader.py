"""Unit tests for agent loader."""

import tempfile
from pathlib import Path

import pytest

from neosian._cli.loader import _find_project_root, load_agent_config
from neosian._foundation.shared.exceptions import (
    AgentFileNotFoundError,
    AgentInvalidConfigurationError,
    AgentInvalidDefinitionError,
    AgentMissingConfigurationError,
)
from neosian._foundation.shared.types import AgentConfig, Model
from neosian._foundation.tools.base import Tool, ToolResult


@pytest.mark.unit
class TestLoadAgentConfig:
    """Tests for load_agent_config function."""

    def test_loads_valid_agent_file(self) -> None:
        """Test loading a valid agent file with configuration."""
        agent_code = """
from neosian import AgentConfig, Tool, ToolResult

@Tool(name="greet", description="Greet someone")
async def greet(name: str) -> ToolResult[str]:
    return ToolResult.ok(f"Hello, {name}!")

configuration = AgentConfig(
    system_prompt="You are a helpful assistant.",
    tools=[greet],
)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            config, name = load_agent_config(f.name)

            assert isinstance(config, AgentConfig)
            assert config.system_prompt == "You are a helpful assistant."
            assert len(config.tools) == 1
            assert name == Path(f.name).stem

    def test_raises_on_file_not_found(self) -> None:
        """Test error when file doesn't exist."""
        with pytest.raises(AgentFileNotFoundError):
            load_agent_config("/nonexistent/path/agent.py")

    def test_raises_on_missing_configuration(self) -> None:
        """Test error when configuration is not defined."""
        agent_code = """
# No configuration variable defined
x = 1
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentMissingConfigurationError):
                load_agent_config(f.name)

    def test_raises_on_invalid_configuration_type(self) -> None:
        """Test error when configuration is not an AgentConfig."""
        agent_code = """
configuration = "not an AgentConfig"
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentInvalidConfigurationError):
                load_agent_config(f.name)

    def test_raises_on_syntax_error(self) -> None:
        """Test error when file has syntax errors."""
        agent_code = """
configuration = "Hello
"""  # Missing closing quote
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            with pytest.raises(AgentInvalidDefinitionError):
                load_agent_config(f.name)

    def test_empty_tools_list_is_valid(self) -> None:
        """Test that an empty tools list is valid."""
        agent_code = """
from neosian import AgentConfig

configuration = AgentConfig(
    system_prompt="You are helpful.",
    tools=[],
)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            config, _ = load_agent_config(f.name)

            assert config.tools == []

    def test_loads_configuration_with_model(self) -> None:
        """Test loading configuration with model."""
        agent_code = """
from neosian import AgentConfig, Model

configuration = AgentConfig(
    system_prompt="You are helpful.",
    tools=[],
    model=Model.GPT_5_NANO,
)
"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(agent_code)
            f.flush()

            config, _ = load_agent_config(f.name)

            assert config.model == Model.GPT_5_NANO


@pytest.mark.unit
class TestAgentConfig:
    """Tests for AgentConfig dataclass."""

    def test_agent_config_fields(self) -> None:
        """Test AgentConfig has correct fields."""

        @Tool(name="test", description="Test tool")
        async def test_tool() -> ToolResult[str]:
            return ToolResult.ok("test")

        config = AgentConfig(
            system_prompt="Test prompt",
            tools=[test_tool],
        )

        assert config.system_prompt == "Test prompt"
        assert len(config.tools) == 1
        assert config.model == Model.GROQ_GPT_OSS_20B  # Default model
        assert config.enable_todo is True

    def test_agent_config_with_all_fields(self) -> None:
        """Test AgentConfig with all optional fields."""
        config = AgentConfig(
            system_prompt="Test prompt",
            tools=[],
            model=Model.GROQ_QWEN3_6_27B,
            enable_todo=False,
        )

        assert config.system_prompt == "Test prompt"
        assert config.tools == []
        assert config.model == Model.GROQ_QWEN3_6_27B
        assert config.enable_todo is False


@pytest.mark.unit
class TestFindProjectRoot:
    """Tests for _find_project_root function."""

    def test_finds_pyproject_toml(self, tmp_path: Path) -> None:
        """Test finding project root via pyproject.toml."""
        # Create structure: root/subdir/agent.py with pyproject.toml at root
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        subdir = root / "agents"
        subdir.mkdir()
        agent_file = subdir / "my_agent.py"
        agent_file.write_text("# agent")

        result = _find_project_root(agent_file)

        assert result == root

    def test_finds_setup_py(self, tmp_path: Path) -> None:
        """Test finding project root via setup.py."""
        root = tmp_path / "project"
        root.mkdir()
        (root / "setup.py").write_text("from setuptools import setup\nsetup()")
        subdir = root / "src"
        subdir.mkdir()
        agent_file = subdir / "agent.py"
        agent_file.write_text("# agent")

        result = _find_project_root(agent_file)

        assert result == root

    def test_finds_git_directory(self, tmp_path: Path) -> None:
        """Test finding project root via .git directory."""
        root = tmp_path / "project"
        root.mkdir()
        (root / ".git").mkdir()
        subdir = root / "deep" / "nested"
        subdir.mkdir(parents=True)
        agent_file = subdir / "agent.py"
        agent_file.write_text("# agent")

        result = _find_project_root(agent_file)

        assert result == root

    def test_prefers_pyproject_over_setup_py_in_same_dir(self, tmp_path: Path) -> None:
        """Test that pyproject.toml takes priority over setup.py in same directory."""
        root = tmp_path / "project"
        root.mkdir()
        # Both markers in the same directory
        (root / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        (root / "setup.py").write_text("from setuptools import setup\nsetup()")
        subdir = root / "subdir"
        subdir.mkdir()
        agent_file = subdir / "agent.py"
        agent_file.write_text("# agent")

        result = _find_project_root(agent_file)

        # Should find root (pyproject.toml checked before setup.py)
        assert result == root

    def test_finds_nearest_project_root(self, tmp_path: Path) -> None:
        """Test that nearest project marker is found (monorepo support)."""
        outer = tmp_path / "monorepo"
        outer.mkdir()
        (outer / "pyproject.toml").write_text("[project]\nname = 'monorepo'\n")
        inner = outer / "packages" / "mypackage"
        inner.mkdir(parents=True)
        (inner / "pyproject.toml").write_text("[project]\nname = 'mypackage'\n")
        agent_file = inner / "agent.py"
        agent_file.write_text("# agent")

        result = _find_project_root(agent_file)

        # Should find the nearest (inner) project root
        assert result == inner

    def test_falls_back_to_parent_directory(self, tmp_path: Path) -> None:
        """Test fallback to agent's parent when no markers found."""
        # No project markers anywhere
        agent_dir = tmp_path / "standalone"
        agent_dir.mkdir()
        agent_file = agent_dir / "agent.py"
        agent_file.write_text("# standalone agent")

        result = _find_project_root(agent_file)

        assert result == agent_dir

    def test_handles_deeply_nested_structure(self, tmp_path: Path) -> None:
        """Test finding root in deeply nested directory structure."""
        root = tmp_path / "project"
        root.mkdir()
        (root / "pyproject.toml").write_text("[project]\nname = 'test'\n")
        deep_dir = root / "src" / "app" / "domains" / "agents"
        deep_dir.mkdir(parents=True)
        agent_file = deep_dir / "chat_agent.py"
        agent_file.write_text("# agent")

        result = _find_project_root(agent_file)

        assert result == root
