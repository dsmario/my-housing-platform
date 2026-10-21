"""Test data for local and dev. Never runs in production (SEED_DATA unset).

Idempotent: safe to run on every start.
"""

from db import connect

MESSAGE = "Hello from the database. Web, API and Postgres are connected."


def main() -> None:
    with connect() as conn:
        if conn.execute("SELECT count(*) FROM messages").fetchone()[0] == 0:
            conn.execute("INSERT INTO messages (text) VALUES (%s)", (MESSAGE,))
            print("seed: inserted welcome message")
        else:
            print("seed: messages already present")


if __name__ == "__main__":
    main()
