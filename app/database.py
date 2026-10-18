"""Database connection management and SQLite configuration."""

from __future__ import annotations

import os
import shutil
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

DEFAULT_DB_PATH = Path("inner_circle.db")


def get_db_path() -> Path:
    """Return the configured database path."""
    env_path = os.getenv("INNER_CIRCLE_DB_PATH")
    if env_path:
        return Path(env_path)
    return DEFAULT_DB_PATH


def get_connection(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open a SQLite connection configured with WAL mode and foreign keys enabled."""
    path = Path(db_path) if db_path else get_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


@contextmanager
def get_db(db_path: Path | str | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections, committing on success and rolling back on error."""
    conn = get_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def backup_database(
    db_path: Path | str | None = None, backup_dir: Path | str = "backups"
) -> Path | None:
    """Create a timestamped backup copy of the database before migrations if it exists."""
    path = Path(db_path) if db_path else get_db_path()
    if not path.exists():
        return None

    target_dir = Path(backup_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = target_dir / f"{path.stem}_{timestamp}.bak.db"
    shutil.copy2(path, backup_file)
    return backup_file
