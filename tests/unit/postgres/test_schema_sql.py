"""The shipped DDL asset and its renderer — keyless, driver-free."""

import subprocess
import sys
from importlib import resources

import pytest

from neosian._foundation.postgres.schema import (
    quote_identifier,
    schema_sql,
    validate_schema_name,
)
from neosian._foundation.shared.exceptions import ConfigurationError

_TABLES = (
    "memories",
    "memory_versions",
    "memory_redactions",
    "conversations",
    "turns",
    "projections",
)


@pytest.mark.unit
def test_the_asset_ships_in_the_package() -> None:
    text = resources.files("neosian.assets").joinpath("sql/postgres.sql").read_text()
    assert "{{schema}}" in text


@pytest.mark.unit
def test_rendered_ddl_names_every_table_and_index() -> None:
    ddl = schema_sql()
    for table in _TABLES:
        assert f'CREATE TABLE IF NOT EXISTS "neosian".{table}' in ddl
    assert 'CREATE SCHEMA IF NOT EXISTS "neosian";' in ddl
    assert "USING gin (search)" in ddl  # the dormant FTS escape hatch
    assert "text_pattern_ops" in ddl
    assert "projections_order" in ddl
    assert "{{" not in ddl  # no placeholder survives rendering


@pytest.mark.unit
def test_rendered_ddl_is_idempotent_by_construction() -> None:
    ddl = schema_sql()
    creates = [line for line in ddl.splitlines() if line.lstrip().startswith("CREATE")]
    assert creates, "no CREATE statements found"
    assert all("IF NOT EXISTS" in line for line in creates)


@pytest.mark.unit
def test_schema_name_is_validated_and_quoted() -> None:
    assert validate_schema_name("neosian_test_ab12") == "neosian_test_ab12"
    assert quote_identifier("my_app") == '"my_app"'
    for bad in ("a-b", "1x", 'x"y', "A", "x" * 64, ""):
        with pytest.raises(ConfigurationError):
            validate_schema_name(bad)
    with pytest.raises(ConfigurationError):
        schema_sql("bad-name")


@pytest.mark.unit
def test_schemas_cli_prints_the_ddl() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "neosian.schemas", "postgres"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert 'CREATE SCHEMA IF NOT EXISTS "neosian";' in result.stdout


@pytest.mark.unit
def test_schemas_cli_honours_schema_flag() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "neosian.schemas", "postgres", "--schema", "my_app"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert 'CREATE SCHEMA IF NOT EXISTS "my_app";' in result.stdout
    assert '"neosian".' not in result.stdout
