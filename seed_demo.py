"""CLI tool to seed or reset a demo database with synthetic contacts."""

from __future__ import annotations

import argparse
from pathlib import Path

from app.csv_parser import parse_connections_csv
from app.database import get_db
from app.generator import generate_fictional_contacts_csv
from app.importer import commit_import, preview_import
from app.migrations_runner import apply_migrations
from app.services import ensure_dev_owner


def seed_database(db_path: Path, count: int = 100, seed: int = 42, reset: bool = False) -> None:
    # Safety check: do not accidentally wipe primary inner_circle.db
    if reset and db_path.name == "inner_circle.db":
        raise ValueError(
            "Safety restriction: Cannot use --reset on the primary 'inner_circle.db'. "
            "Specify a dedicated demo database such as --db demo.db"
        )

    if reset and db_path.exists():
        db_path.unlink()
        print(f"Removed previous demo database: {db_path}")

    # Apply migrations
    apply_migrations(db_path)

    # Generate CSV
    csv_text = generate_fictional_contacts_csv(count=count, seed=seed)
    parsed = parse_connections_csv(csv_text)

    with get_db(db_path) as conn:
        owner, focal = ensure_dev_owner(conn, owner_id="demo-owner")
        preview = preview_import(conn, owner.id, parsed.rows)
        if not preview.can_commit:
            raise RuntimeError(
                f"Generated CSV failed preview with {preview.conflict_count} conflicts "
                f"and {preview.error_count} errors."
            )
        result = commit_import(conn, owner.id, preview)

    print(
        f"Successfully seeded '{db_path}' for owner '{owner.id}' (focal: {focal.display_name}): "
        f"{result.created_count} contacts created, {result.unchanged_count} unchanged."
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed Inner Circle demo database with synthetic contacts."
    )
    parser.add_argument(
        "--db", type=str, default="demo.db", help="Path to demo SQLite database (default: demo.db)"
    )
    parser.add_argument(
        "--count", type=int, default=100, help="Number of synthetic contacts (default: 100)"
    )
    parser.add_argument(
        "--seed", type=int, default=42, help="Deterministic random seed (default: 42)"
    )
    parser.add_argument(
        "--reset", action="store_true", help="Reset/re-create the target demo database"
    )

    args = parser.parse_args()
    seed_database(
        db_path=Path(args.db),
        count=args.count,
        seed=args.seed,
        reset=args.reset,
    )


if __name__ == "__main__":
    main()
