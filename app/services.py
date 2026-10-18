"""Domain services for managing owners, persons, and relationships."""

from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime

from app.models import ContactView, Owner, Person, Relationship


class GraphIntegrityError(Exception):
    """Raised when an operation violates graph invariants."""


class OwnerIsolationError(Exception):
    """Raised when an operation crosses owner boundaries."""


def _row_to_owner(row: sqlite3.Row) -> Owner:
    return Owner(
        id=str(row["id"]),
        email=str(row["email"]) if row["email"] is not None else None,
        focal_person_id=str(row["focal_person_id"]) if row["focal_person_id"] is not None else None,
        created_at=str(row["created_at"]),
    )


def _row_to_person(row: sqlite3.Row) -> Person:
    return Person(
        id=str(row["id"]),
        owner_id=str(row["owner_id"]),
        first_name=str(row["first_name"]),
        last_name=str(row["last_name"]),
        email=str(row["email"]) if row["email"] is not None else None,
        profile_url=str(row["profile_url"]) if row["profile_url"] is not None else None,
        company=str(row["company"]) if row["company"] is not None else None,
        job_title=str(row["job_title"]) if row["job_title"] is not None else None,
        location=str(row["location"]) if row["location"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_relationship(row: sqlite3.Row) -> Relationship:
    connected_str = row["connected_on"]
    connected_date: date | None = None
    if connected_str:
        connected_date = datetime.strptime(str(connected_str), "%Y-%m-%d").date()

    return Relationship(
        id=str(row["id"]),
        owner_id=str(row["owner_id"]),
        person_a_id=str(row["person_a_id"]),
        person_b_id=str(row["person_b_id"]),
        connected_on=connected_date,
        notes=str(row["notes"]) if row["notes"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _normalize_pair(id1: str, id2: str) -> tuple[str, str]:
    """Normalize endpoints order to ensure person_a < person_b and reject self-loops."""
    if id1 == id2:
        raise GraphIntegrityError(
            "Self-edges are prohibited: cannot connect a person to themselves."
        )
    return (id1, id2) if id1 < id2 else (id2, id1)


def ensure_dev_owner(
    conn: sqlite3.Connection,
    owner_id: str = "dev-owner",
    owner_email: str | None = "owner@innercircle.local",
    focal_first_name: str = "Vagiz",
    focal_last_name: str = "Daudov",
) -> tuple[Owner, Person]:
    """Ensure a fixed development owner and their focal Person exist idempotently."""
    cursor = conn.execute("SELECT * FROM owners WHERE id = ?;", (owner_id,))
    existing_owner = cursor.fetchone()

    if existing_owner and existing_owner["focal_person_id"]:
        focal_cursor = conn.execute(
            "SELECT * FROM persons WHERE id = ? AND owner_id = ?;",
            (existing_owner["focal_person_id"], owner_id),
        )
        focal_row = focal_cursor.fetchone()
        if focal_row:
            return _row_to_owner(existing_owner), _row_to_person(focal_row)

    # Need to create or update owner with a new focal person
    focal_id = str(uuid.uuid4())
    now = datetime.now().isoformat()

    conn.execute(
        """
        INSERT OR IGNORE INTO owners (id, email, focal_person_id, created_at)
        VALUES (?, ?, ?, ?);
        """,
        (owner_id, owner_email, focal_id, now),
    )

    conn.execute(
        """
        INSERT INTO persons (
            id, owner_id, first_name, last_name, company, job_title, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            focal_id,
            owner_id,
            focal_first_name,
            focal_last_name,
            "Duke University",
            "Owner & Network Focal Point",
            now,
            now,
        ),
    )

    conn.execute(
        "UPDATE owners SET focal_person_id = ? WHERE id = ?;",
        (focal_id, owner_id),
    )

    owner_cursor = conn.execute("SELECT * FROM owners WHERE id = ?;", (owner_id,))
    owner_row = owner_cursor.fetchone()
    assert owner_row is not None

    person_cursor = conn.execute("SELECT * FROM persons WHERE id = ?;", (focal_id,))
    person_row = person_cursor.fetchone()
    assert person_row is not None

    return _row_to_owner(owner_row), _row_to_person(person_row)


def get_owner(conn: sqlite3.Connection, owner_id: str) -> Owner | None:
    cursor = conn.execute("SELECT * FROM owners WHERE id = ?;", (owner_id,))
    row = cursor.fetchone()
    return _row_to_owner(row) if row else None


def create_person(
    conn: sqlite3.Connection,
    owner_id: str,
    first_name: str,
    last_name: str,
    email: str | None = None,
    profile_url: str | None = None,
    company: str | None = None,
    job_title: str | None = None,
    location: str | None = None,
) -> Person:
    """Create a new person node in the owner's graph."""
    clean_first = first_name.strip()
    clean_last = last_name.strip()
    if not clean_first and not clean_last:
        raise ValueError("At least one non-empty name component (first or last) is required.")

    person_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    clean_email = email.strip().lower() if email and email.strip() else None
    clean_url = profile_url.strip() if profile_url and profile_url.strip() else None
    clean_company = company.strip() if company and company.strip() else None
    clean_title = job_title.strip() if job_title and job_title.strip() else None
    clean_loc = location.strip() if location and location.strip() else None

    conn.execute(
        """
        INSERT INTO persons (
            id, owner_id, first_name, last_name, email,
            profile_url, company, job_title, location, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            person_id,
            owner_id,
            clean_first,
            clean_last,
            clean_email,
            clean_url,
            clean_company,
            clean_title,
            clean_loc,
            now,
            now,
        ),
    )

    cursor = conn.execute(
        "SELECT * FROM persons WHERE id = ? AND owner_id = ?;", (person_id, owner_id)
    )
    row = cursor.fetchone()
    assert row is not None
    return _row_to_person(row)


def get_person(conn: sqlite3.Connection, owner_id: str, person_id: str) -> Person | None:
    """Get a person by ID strictly scoped to owner_id."""
    cursor = conn.execute(
        "SELECT * FROM persons WHERE id = ? AND owner_id = ?;", (person_id, owner_id)
    )
    row = cursor.fetchone()
    return _row_to_person(row) if row else None


def update_person(
    conn: sqlite3.Connection,
    owner_id: str,
    person_id: str,
    first_name: str,
    last_name: str,
    email: str | None = None,
    profile_url: str | None = None,
    company: str | None = None,
    job_title: str | None = None,
    location: str | None = None,
) -> Person:
    """Update person fields. Scoped to owner_id."""
    existing = get_person(conn, owner_id, person_id)
    if not existing:
        raise ValueError(f"Person '{person_id}' not found for owner '{owner_id}'.")

    clean_first = first_name.strip()
    clean_last = last_name.strip()
    if not clean_first and not clean_last:
        raise ValueError("At least one non-empty name component (first or last) is required.")

    now = datetime.now().isoformat()
    clean_email = email.strip().lower() if email and email.strip() else None
    clean_url = profile_url.strip() if profile_url and profile_url.strip() else None
    clean_company = company.strip() if company and company.strip() else None
    clean_title = job_title.strip() if job_title and job_title.strip() else None
    clean_loc = location.strip() if location and location.strip() else None

    conn.execute(
        """
        UPDATE persons
        SET first_name = ?, last_name = ?, email = ?, profile_url = ?,
            company = ?, job_title = ?, location = ?, updated_at = ?
        WHERE id = ? AND owner_id = ?;
        """,
        (
            clean_first,
            clean_last,
            clean_email,
            clean_url,
            clean_company,
            clean_title,
            clean_loc,
            now,
            person_id,
            owner_id,
        ),
    )

    updated = get_person(conn, owner_id, person_id)
    assert updated is not None
    return updated


def delete_person(conn: sqlite3.Connection, owner_id: str, person_id: str) -> int:
    """Delete a person and all their incident edges atomically. Protects focal Person."""
    owner = get_owner(conn, owner_id)
    if owner and owner.focal_person_id == person_id:
        raise GraphIntegrityError("Cannot delete the focal Person representing the owner.")

    existing = get_person(conn, owner_id, person_id)
    if not existing:
        return 0

    # Count incident edges
    edge_cursor = conn.execute(
        """
        SELECT COUNT(*) as edge_count FROM relationships
        WHERE owner_id = ? AND (person_a_id = ? OR person_b_id = ?);
        """,
        (owner_id, person_id, person_id),
    )
    edge_count = int(edge_cursor.fetchone()["edge_count"])

    # Deleting the person cascades deletion of relationships and fingerprints
    conn.execute("DELETE FROM persons WHERE id = ? AND owner_id = ?;", (person_id, owner_id))
    return edge_count


def create_relationship(
    conn: sqlite3.Connection,
    owner_id: str,
    person_1_id: str,
    person_2_id: str,
    connected_on: date | None = None,
    notes: str | None = None,
) -> Relationship:
    """Create an undirected relationship edge between two distinct people owned by owner_id."""
    person_a, person_b = _normalize_pair(person_1_id, person_2_id)

    # Verify both people exist and belong to this owner
    p_a = get_person(conn, owner_id, person_a)
    p_b = get_person(conn, owner_id, person_b)
    if not p_a or not p_b:
        raise OwnerIsolationError(
            f"Both relationship endpoints must exist and belong to owner '{owner_id}'."
        )

    # Check for existing relationship
    existing = get_relationship_between(conn, owner_id, person_a, person_b)
    if existing:
        raise GraphIntegrityError(
            f"A relationship already exists between person '{person_a}' and person '{person_b}'."
        )

    rel_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    conn_str = connected_on.isoformat() if connected_on else None
    clean_notes = notes.strip() if notes and notes.strip() else None

    conn.execute(
        """
        INSERT INTO relationships (
            id, owner_id, person_a_id, person_b_id, connected_on, notes, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (rel_id, owner_id, person_a, person_b, conn_str, clean_notes, now, now),
    )

    rel = get_relationship_by_id(conn, owner_id, rel_id)
    assert rel is not None
    return rel


def get_relationship_by_id(
    conn: sqlite3.Connection, owner_id: str, relationship_id: str
) -> Relationship | None:
    cursor = conn.execute(
        "SELECT * FROM relationships WHERE id = ? AND owner_id = ?;",
        (relationship_id, owner_id),
    )
    row = cursor.fetchone()
    return _row_to_relationship(row) if row else None


def get_relationship_between(
    conn: sqlite3.Connection, owner_id: str, person_1_id: str, person_2_id: str
) -> Relationship | None:
    """Get the relationship between two people, regardless of endpoint order."""
    if person_1_id == person_2_id:
        return None
    person_a, person_b = _normalize_pair(person_1_id, person_2_id)
    cursor = conn.execute(
        """
        SELECT * FROM relationships
        WHERE owner_id = ? AND person_a_id = ? AND person_b_id = ?;
        """,
        (owner_id, person_a, person_b),
    )
    row = cursor.fetchone()
    return _row_to_relationship(row) if row else None


def update_relationship(
    conn: sqlite3.Connection,
    owner_id: str,
    relationship_id: str,
    connected_on: date | None = None,
    notes: str | None = None,
) -> Relationship:
    """Update relationship metadata (connected_on date, context notes)."""
    rel = get_relationship_by_id(conn, owner_id, relationship_id)
    if not rel:
        raise ValueError(f"Relationship '{relationship_id}' not found for owner '{owner_id}'.")

    now = datetime.now().isoformat()
    conn_str = connected_on.isoformat() if connected_on else None
    clean_notes = notes.strip() if notes and notes.strip() else None

    conn.execute(
        """
        UPDATE relationships
        SET connected_on = ?, notes = ?, updated_at = ?
        WHERE id = ? AND owner_id = ?;
        """,
        (conn_str, clean_notes, now, relationship_id, owner_id),
    )

    updated = get_relationship_by_id(conn, owner_id, relationship_id)
    assert updated is not None
    return updated


def delete_relationship(conn: sqlite3.Connection, owner_id: str, relationship_id: str) -> bool:
    """Disconnect two people by removing their relationship edge without deleting either person."""
    cursor = conn.execute(
        "DELETE FROM relationships WHERE id = ? AND owner_id = ?;",
        (relationship_id, owner_id),
    )
    return cursor.rowcount > 0


def create_contact_with_focal_edge(
    conn: sqlite3.Connection,
    owner_id: str,
    focal_person_id: str,
    first_name: str,
    last_name: str,
    email: str | None = None,
    profile_url: str | None = None,
    company: str | None = None,
    job_title: str | None = None,
    location: str | None = None,
    connected_on: date | None = None,
    notes: str | None = None,
) -> tuple[Person, Relationship]:
    """Atomically create a new person and an edge to the owner's focal person."""
    person = create_person(
        conn=conn,
        owner_id=owner_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        profile_url=profile_url,
        company=company,
        job_title=job_title,
        location=location,
    )
    edge = create_relationship(
        conn=conn,
        owner_id=owner_id,
        person_1_id=focal_person_id,
        person_2_id=person.id,
        connected_on=connected_on,
        notes=notes,
    )
    return person, edge


def list_contacts_connected_to_focal(
    conn: sqlite3.Connection,
    owner_id: str,
    focal_person_id: str,
    search_query: str | None = None,
) -> list[ContactView]:
    """List all contacts connected directly to the focal person with their relationship details."""
    sql = """
        SELECT p.*, r.id as rel_id, r.connected_on as rel_connected_on,
               r.notes as rel_notes, r.created_at as rel_created_at,
               r.updated_at as rel_updated_at, r.person_a_id, r.person_b_id
        FROM relationships r
        JOIN persons p ON (
            (r.person_a_id = ? AND r.person_b_id = p.id) OR
            (r.person_b_id = ? AND r.person_a_id = p.id)
        )
        WHERE r.owner_id = ? AND p.id != ?
    """
    params: list[str] = [focal_person_id, focal_person_id, owner_id, focal_person_id]

    if search_query and search_query.strip():
        q = f"%{search_query.strip().lower()}%"
        sql += """
            AND (
                LOWER(p.first_name) LIKE ? OR
                LOWER(p.last_name) LIKE ? OR
                LOWER(p.first_name || ' ' || p.last_name) LIKE ? OR
                LOWER(COALESCE(p.company, '')) LIKE ? OR
                LOWER(COALESCE(p.job_title, '')) LIKE ? OR
                LOWER(COALESCE(p.location, '')) LIKE ?
            )
        """
        params.extend([q, q, q, q, q, q])

    sql += " ORDER BY p.first_name ASC, p.last_name ASC;"

    cursor = conn.execute(sql, params)
    views: list[ContactView] = []
    for row in cursor.fetchall():
        person = _row_to_person(row)
        connected_str = row["rel_connected_on"]
        connected_date: date | None = None
        if connected_str:
            connected_date = datetime.strptime(str(connected_str), "%Y-%m-%d").date()

        rel = Relationship(
            id=str(row["rel_id"]),
            owner_id=owner_id,
            person_a_id=str(row["person_a_id"]),
            person_b_id=str(row["person_b_id"]),
            connected_on=connected_date,
            notes=str(row["rel_notes"]) if row["rel_notes"] is not None else None,
            created_at=str(row["rel_created_at"]),
            updated_at=str(row["rel_updated_at"]),
        )
        views.append(ContactView(person=person, relationship=rel))

    return views


def list_disconnected_persons(
    conn: sqlite3.Connection,
    owner_id: str,
    focal_person_id: str,
) -> list[Person]:
    """List persons owned by owner who are currently not connected to the focal person."""
    sql = """
        SELECT p.* FROM persons p
        WHERE p.owner_id = ? AND p.id != ?
        AND p.id NOT IN (
            SELECT CASE WHEN person_a_id = ? THEN person_b_id ELSE person_a_id END
            FROM relationships
            WHERE owner_id = ? AND (person_a_id = ? OR person_b_id = ?)
        )
        ORDER BY p.first_name ASC, p.last_name ASC;
    """
    cursor = conn.execute(
        sql,
        (owner_id, focal_person_id, focal_person_id, owner_id, focal_person_id, focal_person_id),
    )
    return [_row_to_person(row) for row in cursor.fetchall()]
