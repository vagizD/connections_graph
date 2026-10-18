"""Database migration runner executing numbered SQL migrations."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from app.database import backup_database, get_connection

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def ensure_migrations_table(conn: sqlite3.Connection) -> None:
    """Ensure the schema_migrations bookkeeping table exists."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    conn.commit()


def get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Return set of migration version integers already applied."""
    ensure_migrations_table(conn)
    cursor = conn.execute("SELECT version FROM schema_migrations ORDER BY version ASC;")
    return {int(row["version"]) for row in cursor.fetchall()}


def apply_migrations(db_path: Path | str | None = None) -> list[str]:
    """Run pending database migrations. Backs up the database before making changes."""
    conn = get_connection(db_path)
    try:
        ensure_migrations_table(conn)
        applied = get_applied_versions(conn)

        # Discover SQL files
        migration_files: list[tuple[int, str, Path]] = []
        for file_path in MIGRATIONS_DIR.glob("*.sql"):
            match = re.match(r"^(\d+)_(.+)\.sql$", file_path.name)
            if match:
                version = int(match.group(1))
                name = match.group(2)
                migration_files.append((version, name, file_path))

        migration_files.sort(key=lambda item: item[0])

        pending = [m for m in migration_files if m[0] not in applied]
        if not pending:
            return []

        # Back up existing DB before executing migrations
        backup_database(db_path)

        applied_names: list[str] = []
        for version, name, path in pending:
            sql_content = path.read_text(encoding="utf-8")
            # Execute migration in a single transaction
            conn.execute("BEGIN;")
            try:
                conn.executescript(sql_content)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?);",
                    (version, name),
                )
                conn.commit()
                applied_names.append(f"{version:03d}_{name}")
            except Exception:
                conn.rollback()
                raise

        return applied_names
    finally:
        conn.close()
