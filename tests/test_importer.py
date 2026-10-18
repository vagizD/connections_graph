"""Tests for import preview, idempotency, merge policy, and conflict protection."""

from pathlib import Path

import pytest

from app.csv_parser import parse_connections_csv
from app.database import get_db
from app.importer import commit_import, preview_import
from app.migrations_runner import apply_migrations
from app.services import (
    ensure_dev_owner,
    get_person,
    list_contacts_connected_to_focal,
    update_person,
)

FIXTURES_DIR = Path(__file__).parent.parent / "data" / "fixtures"
REFERENCE_DIR = Path(__file__).parent.parent / "data" / "reference"


@pytest.fixture
def disposable_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_import.db"
    apply_migrations(db_file)
    return db_file


def test_import_fictional_csv_twice_is_idempotent(disposable_db: Path) -> None:
    fixture_path = FIXTURES_DIR / "fictional_contacts_10.csv"
    assert fixture_path.exists()

    with fixture_path.open("r", encoding="utf-8") as f:
        parse_res = parse_connections_csv(f)
    assert len(parse_res.errors) == 0

    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn)

        # First run: preview and commit
        preview1 = preview_import(conn, owner.id, parse_res.rows)
        assert preview1.total_rows == 10
        assert preview1.create_count == 10
        assert preview1.update_count == 0
        assert preview1.unchanged_count == 0
        assert preview1.conflict_count == 0
        assert preview1.can_commit is True

        commit_res1 = commit_import(conn, owner.id, preview1)
        assert commit_res1.created_count == 10
        assert commit_res1.updated_count == 0
        assert commit_res1.unchanged_count == 0

        contacts1 = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        assert len(contacts1) == 10

        # Second run: preview and commit
        preview2 = preview_import(conn, owner.id, parse_res.rows)
        assert preview2.total_rows == 10
        assert preview2.create_count == 0
        assert preview2.update_count == 0
        assert preview2.unchanged_count == 10
        assert preview2.conflict_count == 0
        assert preview2.can_commit is True

        commit_res2 = commit_import(conn, owner.id, preview2)
        assert commit_res2.created_count == 0
        assert commit_res2.updated_count == 0
        assert commit_res2.unchanged_count == 10

        # Exact same node and edge counts
        contacts2 = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        assert len(contacts2) == 10
        assert [c.person.id for c in contacts1] == [c.person.id for c in contacts2]


def test_manual_edits_survive_reimport(disposable_db: Path) -> None:
    fixture_path = FIXTURES_DIR / "fictional_contacts_10.csv"
    with fixture_path.open("r", encoding="utf-8") as f:
        parse_res = parse_connections_csv(f)

    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn)

        # Import first
        preview = preview_import(conn, owner.id, parse_res.rows)
        commit_import(conn, owner.id, preview)

        # Pick Alice Smith and add manual location
        contacts = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        alice = next(c.person for c in contacts if c.person.first_name == "Alice")
        assert alice.location is None

        update_person(
            conn,
            owner.id,
            alice.id,
            first_name=alice.first_name,
            last_name=alice.last_name,
            email=alice.email,
            profile_url=alice.profile_url,
            company=alice.company,
            job_title=alice.job_title,
            location="London, UK",  # Manually added
        )

        # Re-import same CSV
        preview2 = preview_import(conn, owner.id, parse_res.rows)
        assert preview2.conflict_count == 0
        commit_import(conn, owner.id, preview2)

        # Verify manual location was preserved and not erased
        alice_after = get_person(conn, owner.id, alice.id)
        assert alice_after is not None
        assert alice_after.location == "London, UK"


def test_conflicting_populated_values_block_import_and_preserve_db(disposable_db: Path) -> None:
    fixture_path = FIXTURES_DIR / "fictional_contacts_10.csv"
    with fixture_path.open("r", encoding="utf-8") as f:
        parse_res = parse_connections_csv(f)

    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn)
        preview1 = preview_import(conn, owner.id, parse_res.rows)
        commit_import(conn, owner.id, preview1)

        # Manually alter Alice's company to something else
        contacts = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        alice = next(c.person for c in contacts if c.person.first_name == "Alice")

        update_person(
            conn,
            owner.id,
            alice.id,
            first_name=alice.first_name,
            last_name=alice.last_name,
            email=alice.email,
            profile_url=alice.profile_url,
            company="Conflicting Industries Inc",
            job_title=alice.job_title,
            location=None,
        )

        # Now re-importing the original CSV will detect a conflict on company
        # (Acme Corp vs Conflicting Industries Inc)
        preview2 = preview_import(conn, owner.id, parse_res.rows)
        assert preview2.conflict_count == 1
        assert preview2.can_commit is False

        alice_item = next(r for r in preview2.rows if r.matched_person_id == alice.id)
        assert alice_item.action == "CONFLICT"
        assert len(alice_item.conflicts) == 1
        assert alice_item.conflicts[0].field_name == "company"
        assert alice_item.conflicts[0].existing_value == "Conflicting Industries Inc"
        assert alice_item.conflicts[0].imported_value == "Acme Corp"

        # Attempting to commit must fail
        with pytest.raises(ValueError, match="Cannot commit import"):
            commit_import(conn, owner.id, preview2)

        # Alice's manual edit remains intact
        alice_after = get_person(conn, owner.id, alice.id)
        assert alice_after is not None
        assert alice_after.company == "Conflicting Industries Inc"


def test_import_reference_data_if_present(disposable_db: Path) -> None:
    """Acceptance test: real reference CSV initializes user graph without requiring extra info."""
    ref_file = REFERENCE_DIR / "Connections.csv"
    if not ref_file.exists():
        return

    with ref_file.open("r", encoding="utf-8") as f:
        parse_res = parse_connections_csv(f)

    assert len(parse_res.errors) == 0
    assert len(parse_res.rows) == 20

    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn)
        preview = preview_import(conn, owner.id, parse_res.rows)
        assert preview.create_count == 20
        assert preview.can_commit is True

        commit_res = commit_import(conn, owner.id, preview)
        assert commit_res.created_count == 20

        contacts = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        assert len(contacts) == 20
