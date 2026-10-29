"""Create the database from the SQL files in ``sql/``.

Usage (from the project root, with .env configured):

    python scripts/init_db.py

Order matters:
    1. sql/schema.sql            tables, constraints and indexes
    2. sql/advanced_features.sql views, procedures, function, triggers
    3. sql/seed.sql              departments and document types

WARNING: ``schema.sql`` drops and recreates the application tables. Running
this script on a database with data you care about will delete that data.
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

from config import Config  # noqa: E402
import db  # noqa: E402

SQL_DIR = BASE_DIR / "sql"


def split_statements(script: str):
    """Split a .sql file into statements, honouring ``DELIMITER`` blocks.

    MySQL's command-line client understands DELIMITER; the Python connector
    does not, so we split the file ourselves. Trigger and procedure bodies
    contain semicolons, which is exactly why this is necessary.
    """
    statements = []
    buffer = []
    delimiter = ";"

    for line in script.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("DELIMITER "):
            pending = "".join(buffer).strip()
            if pending:
                statements.append(pending)
            buffer = []
            delimiter = stripped.split(None, 1)[1].strip()
            continue
        if stripped.startswith("--"):
            continue

        buffer.append(line)
        if stripped.endswith(delimiter):
            text = "\n".join(buffer).strip()
            text = text[: -len(delimiter)].strip()
            if text:
                statements.append(text)
            buffer = []

    leftover = "\n".join(buffer).strip()
    if leftover:
        statements.append(leftover)
    return statements


def apply_file(connection, filename: str, use_database: bool):
    path = SQL_DIR / filename
    if not path.exists():
        raise SystemExit(f"Missing SQL file: {path}")

    statements = split_statements(path.read_text(encoding="utf-8"))
    cursor = connection.cursor()
    if use_database:
        cursor.execute(f"USE {Config.MYSQL_DATABASE}")
    for statement in statements:
        try:
            cursor.execute(statement)
            connection.commit()
        except Exception:
            preview = statement.strip().splitlines()[0][:90]
            print(f"\nFailed while applying {filename}: {preview}")
            raise
    cursor.close()
    print(f"  applied {filename} ({len(statements)} statements)")


def main():
    print(f"Connecting to MySQL at {Config.MYSQL_HOST}:{Config.MYSQL_PORT} ...")
    connection = db.create_connection(Config, database=None)
    try:
        print("Applying schema and features ...")
        # schema.sql creates and selects the database itself.
        apply_file(connection, "schema.sql", use_database=False)
        apply_file(connection, "advanced_features.sql", use_database=True)
        apply_file(connection, "seed.sql", use_database=True)
    finally:
        connection.close()

    print("\nDatabase ready.")
    print("Next step: python scripts/seed_db.py  (creates development accounts)")


if __name__ == "__main__":
    main()
