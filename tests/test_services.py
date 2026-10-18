"""Tests for persistence, graph integrity, and owner isolation in app/services.py."""

from datetime import date
from pathlib import Path

import pytest

from app.database import get_db
from app.migrations_runner import apply_migrations
from app.services import (
    GraphIntegrityError,
    OwnerIsolationError,
    create_contact_with_focal_edge,
    create_person,
    create_relationship,
    delete_person,
    delete_relationship,
    ensure_dev_owner,
    get_person,
    get_relationship_between,
    list_contacts_connected_to_focal,
    list_disconnected_persons,
    update_person,
    update_relationship,
)


@pytest.fixture
def disposable_db(tmp_path: Path) -> Path:
    """Fixture providing a temporary SQLite database with all migrations applied."""
    db_file = tmp_path / "test_isolated.db"
    apply_migrations(db_file)
    return db_file


def test_ensure_dev_owner_idempotent(disposable_db: Path) -> None:
    with get_db(disposable_db) as conn:
        owner1, focal1 = ensure_dev_owner(conn, owner_id="dev-owner")
        assert owner1.id == "dev-owner"
        assert focal1.first_name == "Vagiz"
        assert focal1.last_name == "Daudov"
        assert owner1.focal_person_id == focal1.id

        # Calling again returns same entities without duplicating
        owner2, focal2 = ensure_dev_owner(conn, owner_id="dev-owner")
        assert owner2.id == owner1.id
        assert focal2.id == focal1.id


def test_create_contact_with_focal_edge(disposable_db: Path) -> None:
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn, owner_id="dev-owner")
        contact, edge = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Alice",
            last_name="Smith",
            company="Acme Corp",
            job_title="Lead Architect",
            location="London",
            connected_on=date(2026, 10, 15),
            notes="Met at AI conference",
        )

        assert contact.first_name == "Alice"
        assert contact.company == "Acme Corp"
        assert edge.owner_id == owner.id
        assert edge.connected_on == date(2026, 10, 15)
        assert edge.notes == "Met at AI conference"

        # Check retrieval
        retrieved_edge = get_relationship_between(conn, owner.id, focal.id, contact.id)
        assert retrieved_edge is not None
        assert retrieved_edge.id == edge.id

        # Reversed endpoint order returns the same edge
        reversed_edge = get_relationship_between(conn, owner.id, contact.id, focal.id)
        assert reversed_edge is not None
        assert reversed_edge.id == edge.id


def test_reject_self_edges_and_duplicate_edges(disposable_db: Path) -> None:
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn, owner_id="dev-owner")
        contact = create_person(conn, owner.id, "Bob", "Jones")

        # Self-edge must be rejected
        with pytest.raises(GraphIntegrityError, match="Self-edges are prohibited"):
            create_relationship(conn, owner.id, contact.id, contact.id)

        # First edge succeeds
        create_relationship(conn, owner.id, focal.id, contact.id)

        # Duplicate edge (even with reversed endpoints) must be rejected
        with pytest.raises(GraphIntegrityError, match="already exists"):
            create_relationship(conn, owner.id, contact.id, focal.id)


def test_owner_isolation(disposable_db: Path) -> None:
    with get_db(disposable_db) as conn:
        owner1, focal1 = ensure_dev_owner(conn, owner_id="owner-1")
        owner2, focal2 = ensure_dev_owner(conn, owner_id="owner-2")

        c1 = create_person(conn, owner1.id, "Owner1", "Contact")
        c2 = create_person(conn, owner2.id, "Owner2", "Contact")

        # Owner 2 cannot view Owner 1's person
        assert get_person(conn, owner2.id, c1.id) is None

        # Owner 2 cannot mutate Owner 1's person
        with pytest.raises(ValueError, match="not found for owner"):
            update_person(conn, owner2.id, c1.id, first_name="Hacked", last_name="Contact")

        # Cross-owner edge creation must be rejected
        with pytest.raises(OwnerIsolationError, match="must exist and belong to owner"):
            create_relationship(conn, owner1.id, c1.id, c2.id)

        with pytest.raises(OwnerIsolationError, match="must exist and belong to owner"):
            create_relationship(conn, owner2.id, focal2.id, c1.id)


def test_disconnect_and_reconnect_contact(disposable_db: Path) -> None:
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn)
        contact, edge = create_contact_with_focal_edge(
            conn, owner.id, focal.id, "Carol", "Taylor", company="Data Inc"
        )

        # Initially in connected list
        connected = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        assert len(connected) == 1
        assert connected[0].person.id == contact.id
        assert len(list_disconnected_persons(conn, owner.id, focal.id)) == 0

        # Disconnect: deletes only the relationship edge, retains the person
        deleted = delete_relationship(conn, owner.id, edge.id)
        assert deleted is True

        # Person still exists in database
        assert get_person(conn, owner.id, contact.id) is not None

        # Now appears in disconnected list and not in connected list
        connected_after = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        assert len(connected_after) == 0

        disconnected = list_disconnected_persons(conn, owner.id, focal.id)
        assert len(disconnected) == 1
        assert disconnected[0].id == contact.id

        # Reconnect
        new_edge = create_relationship(conn, owner.id, focal.id, contact.id)
        assert new_edge is not None
        assert len(list_contacts_connected_to_focal(conn, owner.id, focal.id)) == 1


def test_delete_person_protects_focal_and_cascades_edges(disposable_db: Path) -> None:
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn)
        contact1, _ = create_contact_with_focal_edge(conn, owner.id, focal.id, "Dave", "Miller")
        contact2, _ = create_contact_with_focal_edge(conn, owner.id, focal.id, "Elena", "Rostova")

        # Add edge between Dave and Elena
        create_relationship(conn, owner.id, contact1.id, contact2.id)

        # Focal person cannot be deleted
        with pytest.raises(GraphIntegrityError, match="Cannot delete the focal Person"):
            delete_person(conn, owner.id, focal.id)

        # Deleting contact1 removes contact1 and both incident edges (to focal and to contact2)
        edge_count = delete_person(conn, owner.id, contact1.id)
        assert edge_count == 2
        assert get_person(conn, owner.id, contact1.id) is None

        # contact2 still exists and remains connected to focal
        assert get_person(conn, owner.id, contact2.id) is not None
        assert get_relationship_between(conn, owner.id, focal.id, contact2.id) is not None
        # But relationship between contact1 and contact2 is gone
        assert get_relationship_between(conn, owner.id, contact1.id, contact2.id) is None


def test_update_person_and_relationship(disposable_db: Path) -> None:
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn)
        contact, edge = create_contact_with_focal_edge(
            conn, owner.id, focal.id, "Frank", "Chen", company="Startup A"
        )

        updated_p = update_person(
            conn,
            owner.id,
            contact.id,
            first_name="Franklin",
            last_name="Chen",
            company="Startup B",
            job_title="CEO",
            location="San Francisco",
        )
        assert updated_p.first_name == "Franklin"
        assert updated_p.company == "Startup B"
        assert updated_p.job_title == "CEO"
        assert updated_p.location == "San Francisco"

        updated_e = update_relationship(
            conn,
            owner.id,
            edge.id,
            connected_on=date(2025, 1, 1),
            notes="Updated interaction context",
        )
        assert updated_e.connected_on == date(2025, 1, 1)
        assert updated_e.notes == "Updated interaction context"
