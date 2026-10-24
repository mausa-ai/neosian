"""Schema naming and the shipped DDL (the migration story, N3).

The store's tables live in one dedicated Postgres schema (default
`neosian`): hosts get a collision-free namespace, tests get per-test
isolation via a unique schema plus `DROP SCHEMA ... CASCADE`. Statements
qualify every table with the quoted schema name — never `search_path`.

The DDL ships as `assets/sql/postgres.sql`, idempotent, with the
`{{schema}}` placeholder rendered here (ECOSYSTEM §8 template syntax).
Applying it is the caller's explicit act — `PostgresStore.apply_schema()`
or `python -m neosian.schemas postgres` — never a side effect of a store
method (C1/CS1: the store issues no DDL).
"""

from __future__ import annotations

import re
from functools import cache
from importlib import resources
from typing import Final

from neosian._foundation.shared.exceptions import ConfigurationError
from neosian._foundation.shared.prompt_assets import render

_SQL_ASSET: Final = "sql/postgres.sql"

# The DDL generation the asset describes, stamped into the one-row
# `neosian_schema` table on apply. `IF NOT EXISTS` cannot add a column, so
# a later generation ships as equally idempotent statements and bumps
# this number — a store can read it before trusting a column.
# NL: turns.actor; N5: turns.search_text; N8: turns.redacted, turn_redactions.
SCHEMA_VERSION: Final = 4

# Lowercase unquoted-identifier shape, ≤ 63 bytes (the Postgres NAMEDATALEN
# limit). Restricting to this set makes quoting trivially safe.
SCHEMA_NAME_PATTERN: Final = re.compile(r"\A[a-z_][a-z0-9_]{0,62}\Z")


def validate_schema_name(name: str) -> str:
    """Validate a Postgres schema name, returning it unchanged."""
    if not SCHEMA_NAME_PATTERN.match(name):
        raise ConfigurationError(
            f"Invalid Postgres schema name {name!r}: must match "
            "[a-z_][a-z0-9_]{0,62}"
        )
    return name


def quote_identifier(name: str) -> str:
    """Double-quote a validated identifier (the charset admits no quotes)."""
    return f'"{name}"'


def statement(schema: str, name: str) -> str:
    """A shared Python/Rust statement, qualified with a validated schema."""
    quoted = quote_identifier(validate_schema_name(schema))
    return _statement_template(name).replace("{{schema}}", quoted)


@cache
def _statement_template(name: str) -> str:
    return (
        resources.files("neosian.assets").joinpath(f"sql/{name}.sql").read_text("utf-8")
    )


def schema_sql(schema: str = "neosian") -> str:
    """The shipped DDL, rendered for one schema. Pure — no driver, no I/O
    beyond reading the packaged asset."""
    validate_schema_name(schema)
    template = resources.files("neosian.assets").joinpath(_SQL_ASSET).read_text("utf-8")
    return render(
        template, schema=quote_identifier(schema), version=str(SCHEMA_VERSION)
    )
