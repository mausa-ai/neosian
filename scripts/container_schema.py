"""Create/drop only the disposable schema owned by one container test run."""

import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import sql


def main() -> None:
    name = os.environ["NEOSIAN_CONTAINER_SCHEMA"]
    if not re.fullmatch(r"neosian_container_[a-f0-9]{32}", name):
        raise ValueError("refusing a schema not owned by the test runner")
    with psycopg.connect(
        os.environ["NEOSIAN_TEST_POSTGRES_DSN"], autocommit=True
    ) as connection:
        if sys.argv[1] == "create":
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
            template = (
                Path(__file__).resolve().parents[1] / "assets/sql/postgres.sql"
            ).read_text()
            try:
                connection.execute(
                    template.replace("{{schema}}", f'"{name}"').replace(
                        "{{version}}", "4"
                    )
                )
                extension = (
                    Path(__file__).resolve().parents[1]
                    / "assets/sql/changes-postgres.sql"
                ).read_text()
                connection.execute(extension.replace("{{schema}}", f'"{name}"'))
            except Exception:
                connection.execute(
                    sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name))
                )
                raise
        elif sys.argv[1] == "drop":
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name))
            )
        else:
            raise ValueError("expected create or drop")


if __name__ == "__main__":
    main()
