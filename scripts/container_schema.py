"""Create/drop only the disposable schema owned by one container test run.

`create` installs schema 4 and the changes extension; `create records` also
installs records extension 1, for the records proof under a memory limit.
"""

import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql

SQL = Path(__file__).resolve().parents[1] / "assets/sql"


def owned(name: str) -> str:
    if not re.fullmatch(r"neosian_container_[a-f0-9]{32}", name):
        raise ValueError("refusing a schema not owned by the test runner")
    return name


def create(dsn: str, name: str, records: bool = False) -> None:
    """Schema 4 and the changes extension in `name`, and records extension 1
    when `records`; a failure drops what was made."""
    files = ["changes-postgres.sql"] + ["records-postgres.sql"] * records
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(owned(name)))
        )
        try:
            template = (SQL / "postgres.sql").read_text()
            connection.execute(
                template.replace("{{schema}}", f'"{name}"').replace("{{version}}", "4")
            )
            for file in files:
                extension = (SQL / file).read_text()
                connection.execute(extension.replace("{{schema}}", f'"{name}"'))
        except Exception:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name))
            )
            raise


def drop(dsn: str, name: str) -> None:
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(
            sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                sql.Identifier(owned(name))
            )
        )


def main() -> None:
    name = os.environ["NEOSIAN_CONTAINER_SCHEMA"]
    dsn = os.environ["NEOSIAN_TEST_POSTGRES_DSN"]
    if sys.argv[1:] in (["create"], ["create", "records"]):
        create(dsn, name, records=sys.argv[2:] == ["records"])
    elif sys.argv[1:] == ["drop"]:
        drop(dsn, name)
    else:
        raise ValueError("expected create, create records or drop")


if __name__ == "__main__":
    main()
