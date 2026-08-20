"""PostgresStore — the relational reference substrate (DESIGN §8, §9; N3).

One store implementing both storage seams over a psycopg 3 connection
pool. The driver rides the `postgres` extra; importing this package never
imports psycopg (keyless boot stays driver-free).
"""

from neosian._foundation.postgres.store import PostgresStore

__all__ = ["PostgresStore"]
