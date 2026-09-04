"""The mobility statements render for the quoted schema and gate every
insert on the unit being empty (keyless; the postgres tier runs them)."""

import pytest

from neosian._foundation.memory.portable import Portable
from neosian._foundation.postgres.statements_portable import build_portable_statements
from neosian._foundation.postgres.store import PostgresStore
from neosian._foundation.shared.exceptions import ConfigurationError

_DSN = "postgresql://nobody@localhost:1/nowhere"


@pytest.mark.unit
def test_statements_are_schema_qualified_and_gated() -> None:
    sql = build_portable_statements("neosian_x")
    for statement in (sql.restore_scope, sql.restore_conversation):
        assert '"neosian_x".' in statement
        assert statement.count("WHERE (SELECT empty FROM gate)") >= 2
        assert statement.rstrip().endswith("SELECT empty FROM gate")
    assert "WITH ORDINALITY" in sql.restore_scope
    assert 'ORDER BY scope COLLATE "C"' in sql.list_scopes
    assert 'ORDER BY conversation_id COLLATE "C"' in sql.list_conversations


@pytest.mark.unit
def test_the_store_is_portable_from_construction() -> None:
    assert isinstance(PostgresStore(_DSN), Portable)
    with pytest.raises(ConfigurationError):
        build_portable_statements("bad-name")
