"""Domain services for owners, persons, relationships, and network grouping."""

from __future__ import annotations

import contextlib
import json
import math
import re
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from app.ai_summarizer import (
    SummarizedInteractionResult,
    process_text_interaction,
    resynthesize_edge_summary_from_all_interactions,
)
from app.models import (
    ContactFilterCriteria,
    ContactGroup,
    ContactSplitAssignment,
    ContactView,
    FilterGroupResult,
    GroupedSplitView,
    Interaction,
    NetworkSplit,
    Owner,
    Person,
    Relationship,
    SplitGroupStats,
    Task,
)


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
    keys = row.keys()
    education = (
        str(row["education"]) if "education" in keys and row["education"] is not None else None
    )
    interests = (
        str(row["interests"]) if "interests" in keys and row["interests"] is not None else None
    )

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
        education=education,
        interests=interests,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_relationship(row: sqlite3.Row) -> Relationship:
    keys = row.keys()
    connected_str = row["connected_on"] if "connected_on" in keys else None
    connected_date: date | None = None
    if connected_str:
        connected_date = datetime.strptime(str(connected_str), "%Y-%m-%d").date()

    last_str = row["last_interaction_date"] if "last_interaction_date" in keys else None
    last_date: date | None = None
    if last_str:
        last_date = datetime.strptime(str(last_str), "%Y-%m-%d").date()

    context_labels = (
        str(row["context_labels"])
        if "context_labels" in keys and row["context_labels"] is not None
        else None
    )
    shared_activities = (
        str(row["shared_activities"])
        if "shared_activities" in keys and row["shared_activities"] is not None
        else None
    )
    summary = str(row["summary"]) if "summary" in keys and row["summary"] is not None else None
    notes = str(row["notes"]) if "notes" in keys and row["notes"] is not None else None

    return Relationship(
        id=str(row["id"]),
        owner_id=str(row["owner_id"]),
        person_a_id=str(row["person_a_id"]),
        person_b_id=str(row["person_b_id"]),
        connected_on=connected_date,
        notes=notes,
        context_labels=context_labels,
        shared_activities=shared_activities,
        last_interaction_date=last_date,
        summary=summary,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def _row_to_interaction(row: sqlite3.Row) -> Interaction:
    idate = datetime.strptime(str(row["interaction_date"]), "%Y-%m-%d").date()
    return Interaction(
        id=str(row["id"]),
        owner_id=str(row["owner_id"]),
        relationship_id=str(row["relationship_id"]),
        interaction_date=idate,
        interaction_type=str(row["interaction_type"]),
        title=str(row["title"]) if row["title"] is not None else None,
        content=str(row["content"]),
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
    row = cursor.fetchone()

    if row and row["focal_person_id"]:
        focal_cursor = conn.execute(
            "SELECT * FROM persons WHERE id = ? AND owner_id = ?;",
            (row["focal_person_id"], owner_id),
        )
        focal_row = focal_cursor.fetchone()
        if focal_row:
            return _row_to_owner(row), _row_to_person(focal_row)

    now = datetime.now().isoformat()
    if not row:
        conn.execute(
            "INSERT INTO owners (id, email, focal_person_id, created_at) VALUES (?, ?, NULL, ?);",
            (owner_id, owner_email, now),
        )

    focal_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO persons (
            id, owner_id, first_name, last_name, email,
            profile_url, company, job_title, location, education,
            interests, created_at, updated_at
        ) VALUES (
            ?, ?, ?, ?, ?, NULL, 'Inner Circle', 'System Focal User',
            'Local', NULL, NULL, ?, ?
        );
        """,
        (focal_id, owner_id, focal_first_name, focal_last_name, owner_email, now, now),
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
    education: str | None = None,
    interests: str | None = None,
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
    clean_edu = education.strip() if education and education.strip() else None
    clean_interests = interests.strip() if interests and interests.strip() else None

    conn.execute(
        """
        INSERT INTO persons (
            id, owner_id, first_name, last_name, email,
            profile_url, company, job_title, location, education, interests, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
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
            clean_edu,
            clean_interests,
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
    education: str | None = None,
    interests: str | None = None,
) -> Person:
    """Update person fields. Scoped to owner_id."""
    clean_first = first_name.strip()
    clean_last = last_name.strip()
    if not clean_first and not clean_last:
        raise ValueError("At least one non-empty name component (first or last) is required.")

    existing = get_person(conn, owner_id, person_id)
    if not existing:
        raise ValueError(f"Person '{person_id}' not found for owner '{owner_id}'.")

    now = datetime.now().isoformat()
    clean_email = email.strip().lower() if email and email.strip() else None
    clean_url = profile_url.strip() if profile_url and profile_url.strip() else None
    clean_company = company.strip() if company and company.strip() else None
    clean_title = job_title.strip() if job_title and job_title.strip() else None
    clean_loc = location.strip() if location and location.strip() else None
    clean_edu = education.strip() if education and education.strip() else None
    clean_interests = interests.strip() if interests and interests.strip() else None

    conn.execute(
        """
        UPDATE persons
        SET first_name = ?, last_name = ?, email = ?, profile_url = ?,
            company = ?, job_title = ?, location = ?, education = ?, interests = ?, updated_at = ?
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
            clean_edu,
            clean_interests,
            now,
            person_id,
            owner_id,
        ),
    )

    updated = get_person(conn, owner_id, person_id)
    assert updated is not None
    return updated


def delete_person(conn: sqlite3.Connection, owner_id: str, person_id: str) -> int:
    """Delete a person node and all their incident edges atomically.

    Cannot delete the owner's focal person.
    Returns: count of edges deleted.
    """
    owner = get_owner(conn, owner_id)
    if owner and owner.focal_person_id == person_id:
        raise GraphIntegrityError("Cannot delete the focal Person representing network origin.")

    existing = get_person(conn, owner_id, person_id)
    if not existing:
        return 0

    edge_cursor = conn.execute(
        """
        SELECT COUNT(*) as edge_count FROM relationships
        WHERE owner_id = ? AND (person_a_id = ? OR person_b_id = ?);
        """,
        (owner_id, person_id, person_id),
    )
    edge_count = int(edge_cursor.fetchone()["edge_count"])

    conn.execute("DELETE FROM persons WHERE id = ? AND owner_id = ?;", (person_id, owner_id))
    return edge_count


def create_relationship(
    conn: sqlite3.Connection,
    owner_id: str,
    person_1_id: str,
    person_2_id: str,
    connected_on: date | None = None,
    notes: str | None = None,
    context_labels: str | None = None,
    shared_activities: str | None = None,
    last_interaction_date: date | None = None,
    summary: str | None = None,
) -> Relationship:
    """Create an undirected relationship edge between two distinct people owned by owner_id."""
    person_a, person_b = _normalize_pair(person_1_id, person_2_id)

    p_a = get_person(conn, owner_id, person_a)
    p_b = get_person(conn, owner_id, person_b)
    if not p_a or not p_b:
        raise OwnerIsolationError(
            f"Both relationship endpoints must exist and belong to owner '{owner_id}'."
        )

    existing = get_relationship_between(conn, owner_id, person_a, person_b)
    if existing:
        raise GraphIntegrityError(
            f"A relationship already exists between person '{person_a}' and person '{person_b}'."
        )

    rel_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    conn_str = connected_on.isoformat() if connected_on else None
    last_str = last_interaction_date.isoformat() if last_interaction_date else None
    clean_notes = notes.strip() if notes and notes.strip() else None
    clean_labels = context_labels.strip() if context_labels and context_labels.strip() else None
    clean_activities = (
        shared_activities.strip() if shared_activities and shared_activities.strip() else None
    )
    clean_summary = summary.strip() if summary and summary.strip() else None

    conn.execute(
        """
        INSERT INTO relationships (
            id, owner_id, person_a_id, person_b_id, connected_on, notes,
            context_labels, shared_activities, last_interaction_date,
            summary, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            rel_id,
            owner_id,
            person_a,
            person_b,
            conn_str,
            clean_notes,
            clean_labels,
            clean_activities,
            last_str,
            clean_summary,
            now,
            now,
        ),
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
    context_labels: str | None = None,
    shared_activities: str | None = None,
    last_interaction_date: date | None = None,
    summary: str | None = None,
) -> Relationship:
    """Update relationship metadata (aggregate total state)."""
    rel = get_relationship_by_id(conn, owner_id, relationship_id)
    if not rel:
        raise ValueError(f"Relationship '{relationship_id}' not found for owner '{owner_id}'.")

    now = datetime.now().isoformat()
    conn_str = connected_on.isoformat() if connected_on else None
    last_str = last_interaction_date.isoformat() if last_interaction_date else None
    clean_notes = notes.strip() if notes and notes.strip() else None
    clean_labels = context_labels.strip() if context_labels and context_labels.strip() else None
    clean_activities = (
        shared_activities.strip() if shared_activities and shared_activities.strip() else None
    )
    clean_summary = summary.strip() if summary and summary.strip() else None

    conn.execute(
        """
        UPDATE relationships
        SET connected_on = ?, notes = ?, context_labels = ?, shared_activities = ?,
            last_interaction_date = ?, summary = ?, updated_at = ?
        WHERE id = ? AND owner_id = ?;
        """,
        (
            conn_str,
            clean_notes,
            clean_labels,
            clean_activities,
            last_str,
            clean_summary,
            now,
            relationship_id,
            owner_id,
        ),
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
    education: str | None = None,
    interests: str | None = None,
    connected_on: date | None = None,
    notes: str | None = None,
    context_labels: str | None = None,
    shared_activities: str | None = None,
    last_interaction_date: date | None = None,
    summary: str | None = None,
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
        education=education,
        interests=interests,
    )
    edge = create_relationship(
        conn=conn,
        owner_id=owner_id,
        person_1_id=focal_person_id,
        person_2_id=person.id,
        connected_on=connected_on,
        notes=notes,
        context_labels=context_labels,
        shared_activities=shared_activities,
        last_interaction_date=last_interaction_date,
        summary=summary,
    )
    return person, edge


# --- Interaction Sequence (Event Log) Services ---


def create_interaction(
    conn: sqlite3.Connection,
    owner_id: str,
    relationship_id: str,
    interaction_date: date,
    interaction_type: str = "note",
    title: str | None = None,
    content: str = "",
) -> Interaction:
    """Add a discrete event to the interaction sequence for a relationship."""
    rel = get_relationship_by_id(conn, owner_id, relationship_id)
    if not rel:
        raise ValueError(f"Relationship '{relationship_id}' not found for owner '{owner_id}'.")

    clean_content = content.strip()
    if not clean_content:
        raise ValueError("Interaction content/notes cannot be empty.")

    interaction_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    clean_title = title.strip() if title and title.strip() else None

    conn.execute(
        """
        INSERT INTO interactions (
            id, owner_id, relationship_id, interaction_date,
            interaction_type, title, content, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            interaction_id,
            owner_id,
            relationship_id,
            interaction_date.isoformat(),
            interaction_type,
            clean_title,
            clean_content,
            now,
            now,
        ),
    )

    # Automatically advance relationship's last_interaction_date if this interaction is newer
    if not rel.last_interaction_date or interaction_date > rel.last_interaction_date:
        update_relationship(
            conn=conn,
            owner_id=owner_id,
            relationship_id=relationship_id,
            connected_on=rel.connected_on,
            notes=rel.notes,
            context_labels=rel.context_labels,
            shared_activities=rel.shared_activities,
            last_interaction_date=interaction_date,
            summary=rel.summary,
        )

    cursor = conn.execute(
        "SELECT * FROM interactions WHERE id = ? AND owner_id = ?;", (interaction_id, owner_id)
    )
    row = cursor.fetchone()
    assert row is not None
    return _row_to_interaction(row)


def list_interactions_for_relationship(
    conn: sqlite3.Connection, owner_id: str, relationship_id: str
) -> list[Interaction]:
    """Retrieve interaction event sequence for relationship, chronologically descending."""
    cursor = conn.execute(
        """
        SELECT * FROM interactions
        WHERE owner_id = ? AND relationship_id = ?
        ORDER BY interaction_date DESC, created_at DESC;
        """,
        (owner_id, relationship_id),
    )
    return [_row_to_interaction(row) for row in cursor.fetchall()]


def delete_interaction(conn: sqlite3.Connection, owner_id: str, interaction_id: str) -> bool:
    """Delete an interaction event."""
    cursor = conn.execute(
        "DELETE FROM interactions WHERE id = ? AND owner_id = ?;", (interaction_id, owner_id)
    )
    return cursor.rowcount > 0


def _merge_comma_separated(existing: str | None, additions: list[str]) -> str | None:
    tokens: list[str] = []
    if existing:
        for t in existing.split(","):
            cleaned = t.strip()
            if cleaned and cleaned not in tokens:
                tokens.append(cleaned)
    for add in additions:
        cleaned = add.strip().lstrip("#")
        if cleaned and cleaned not in tokens:
            tokens.append(cleaned)
    return ", ".join(tokens) if tokens else None


def record_summarized_interaction(
    conn: sqlite3.Connection,
    owner_id: str,
    relationship_id: str,
    summary_result: SummarizedInteractionResult,
    auto_update_edge_state: bool = True,
    interaction_type: str = "audio_transcript",
) -> tuple[Interaction, Relationship]:
    """Save an AI-summarized interaction and optionally update the relationship total state."""
    rel = get_relationship_by_id(conn, owner_id, relationship_id)
    if not rel:
        raise ValueError(f"Relationship '{relationship_id}' not found for owner '{owner_id}'.")

    # Build rich formatted content
    blocks: list[str] = [
        f"**Executive Summary:** {summary_result.executive_summary}\n",
    ]

    if summary_result.key_takeaways:
        blocks.append("**Key Takeaways:**")
        for item in summary_result.key_takeaways:
            blocks.append(f"- {item}")
        blocks.append("")

    if summary_result.action_items:
        blocks.append("**Action Items & Next Steps:**")
        for act in summary_result.action_items:
            blocks.append(f"- [ ] {act}")
        blocks.append("")

    if summary_result.transcript:
        blocks.append(f"**Transcript / Notes:**\n{summary_result.transcript}\n")

    formatted_content = "\n".join(blocks).strip()

    interaction = create_interaction(
        conn=conn,
        owner_id=owner_id,
        relationship_id=relationship_id,
        interaction_date=summary_result.interaction_date,
        interaction_type=interaction_type,
        title=summary_result.title,
        content=formatted_content,
    )

    updated_rel = rel
    if auto_update_edge_state:
        merged_labels = _merge_comma_separated(
            rel.context_labels, summary_result.discovered_context_labels
        )
        merged_activities = _merge_comma_separated(
            rel.shared_activities, summary_result.discovered_shared_activities
        )
        new_summary = summary_result.suggested_total_state_summary

        # Check recency date
        new_last_date = summary_result.interaction_date
        if rel.last_interaction_date and rel.last_interaction_date > new_last_date:
            new_last_date = rel.last_interaction_date

        updated_rel = update_relationship(
            conn=conn,
            owner_id=owner_id,
            relationship_id=relationship_id,
            connected_on=rel.connected_on,
            notes=rel.notes,
            context_labels=merged_labels,
            shared_activities=merged_activities,
            last_interaction_date=new_last_date,
            summary=new_summary,
        )

    # Auto-generate tasks from action items
    if summary_result.action_items:
        contact_person_id: str | None = None
        owner = get_owner(conn, owner_id)
        if owner and owner.focal_person_id:
            with contextlib.suppress(ValueError):
                contact_person_id = rel.other_person_id(owner.focal_person_id)

        for act in summary_result.action_items:
            clean_act = re.sub(r"^(?:-\s*\[\s*\]\s*|-\s*)", "", act).strip()
            if clean_act:
                deadline = parse_action_item_deadline(clean_act, summary_result.interaction_date)
                create_task(
                    conn=conn,
                    owner_id=owner_id,
                    title=clean_act,
                    deadline=deadline,
                    contact_person_id=contact_person_id,
                    relationship_id=relationship_id,
                    interaction_id=interaction.id,
                )

    return interaction, updated_rel


def log_interaction_with_edge_synthesis(
    conn: sqlite3.Connection,
    owner_id: str,
    relationship_id: str,
    contact_person: Person,
    interaction_date: date,
    interaction_type: str = "note",
    title: str | None = None,
    content: str = "",
    auto_update_edge_state: bool = True,
    api_key: str | None = None,
) -> tuple[Interaction, Relationship]:
    """Process any interaction (thought, call, chat, mutual connection note, etc.),
    record it in history, and automatically synthesize an updated single Edge Summary.
    """
    rel = get_relationship_by_id(conn, owner_id, relationship_id)
    if not rel:
        raise ValueError(f"Relationship '{relationship_id}' not found for owner '{owner_id}'.")

    # Run AI summarizer / synthesis on this interaction
    summary_result = process_text_interaction(
        text_content=content.strip(),
        contact_name=contact_person.display_name,
        interaction_date=interaction_date,
        company=contact_person.company,
        job_title=contact_person.job_title,
        current_edge_summary=rel.summary,
        interaction_type=interaction_type,
        title_hint=title.strip() if title and title.strip() else None,
        api_key=api_key,
    )

    return record_summarized_interaction(
        conn=conn,
        owner_id=owner_id,
        relationship_id=relationship_id,
        summary_result=summary_result,
        auto_update_edge_state=auto_update_edge_state,
        interaction_type=interaction_type,
    )


def resynthesize_relationship_summary(
    conn: sqlite3.Connection,
    owner_id: str,
    relationship_id: str,
    contact_person: Person,
    api_key: str | None = None,
) -> Relationship:
    """Re-synthesize the overall Relationship Edge Summary across all logged interactions."""
    rel = get_relationship_by_id(conn, owner_id, relationship_id)
    if not rel:
        raise ValueError(f"Relationship '{relationship_id}' not found for owner '{owner_id}'.")

    interactions = list_interactions_for_relationship(conn, owner_id, relationship_id)
    new_summary = resynthesize_edge_summary_from_all_interactions(
        contact_name=contact_person.display_name,
        company=contact_person.company,
        job_title=contact_person.job_title,
        interactions=interactions,
        api_key=api_key,
    )

    return update_relationship(
        conn=conn,
        owner_id=owner_id,
        relationship_id=relationship_id,
        connected_on=rel.connected_on,
        notes=rel.notes,
        context_labels=rel.context_labels,
        shared_activities=rel.shared_activities,
        last_interaction_date=rel.last_interaction_date,
        summary=new_summary,
    )


# --- Multi-Criteria Filtering & Grouping Services ---


def list_contacts_connected_to_focal(
    conn: sqlite3.Connection,
    owner_id: str,
    focal_person_id: str,
    search_query: str | None = None,
) -> list[ContactView]:
    """List all contacts connected directly to the focal person with their relationship details."""
    sql = """
        SELECT p.*, r.id as rel_id, r.connected_on as rel_connected_on,
               r.notes as rel_notes, r.context_labels as rel_context_labels,
               r.shared_activities as rel_shared_activities,
               r.last_interaction_date as rel_last_interaction_date,
               r.summary as rel_summary,
               r.created_at as rel_created_at,
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
                LOWER(COALESCE(p.location, '')) LIKE ? OR
                LOWER(COALESCE(p.education, '')) LIKE ? OR
                LOWER(COALESCE(p.interests, '')) LIKE ? OR
                LOWER(COALESCE(r.context_labels, '')) LIKE ? OR
                LOWER(COALESCE(r.summary, '')) LIKE ?
            )
        """
        params.extend([q, q, q, q, q, q, q, q, q, q])

    sql += " ORDER BY p.first_name ASC, p.last_name ASC;"

    cursor = conn.execute(sql, params)
    views: list[ContactView] = []
    for row in cursor.fetchall():
        person = _row_to_person(row)
        connected_str = row["rel_connected_on"]
        connected_date: date | None = None
        if connected_str:
            connected_date = datetime.strptime(str(connected_str), "%Y-%m-%d").date()

        last_str = row["rel_last_interaction_date"]
        last_date: date | None = None
        if last_str:
            last_date = datetime.strptime(str(last_str), "%Y-%m-%d").date()

        rel = Relationship(
            id=str(row["rel_id"]),
            owner_id=owner_id,
            person_a_id=str(row["person_a_id"]),
            person_b_id=str(row["person_b_id"]),
            connected_on=connected_date,
            notes=str(row["rel_notes"]) if row["rel_notes"] is not None else None,
            context_labels=(
                str(row["rel_context_labels"]) if row["rel_context_labels"] is not None else None
            ),
            shared_activities=(
                str(row["rel_shared_activities"])
                if row["rel_shared_activities"] is not None
                else None
            ),
            last_interaction_date=last_date,
            summary=str(row["rel_summary"]) if row["rel_summary"] is not None else None,
            created_at=str(row["rel_created_at"]),
            updated_at=str(row["rel_updated_at"]),
        )
        views.append(ContactView(person=person, relationship=rel))

    return views


def filter_and_group_contacts(
    conn: sqlite3.Connection,
    owner_id: str,
    focal_person_id: str,
    criteria: ContactFilterCriteria,
    group_dimension: str = "none",
) -> FilterGroupResult:
    """Filter contacts across node and edge criteria, and group by selected dimension.

    Handles multi-valued attributes (interests, context labels) with accurate per-group
    membership while preserving unique total contact count. Explicitly displays unknown values.
    """
    contacts = list_contacts_connected_to_focal(
        conn=conn,
        owner_id=owner_id,
        focal_person_id=focal_person_id,
        search_query=criteria.search_query,
    )

    today = date.today()
    filtered: list[ContactView] = []

    active_filters: dict[str, str] = {}
    if criteria.search_query:
        active_filters["Search"] = criteria.search_query
    if criteria.company:
        active_filters["Company"] = criteria.company
    if criteria.location:
        active_filters["Location"] = criteria.location
    if criteria.education:
        active_filters["Education"] = criteria.education
    if criteria.interest:
        active_filters["Interest"] = criteria.interest
    if criteria.context_label:
        active_filters["Context"] = criteria.context_label
    if criteria.last_interaction:
        active_filters["Last Interaction"] = criteria.last_interaction

    for cv in contacts:
        p = cv.person
        r = cv.relationship

        # Filter: Company
        if criteria.company:
            p_comp = (p.company or "").strip().lower()
            if criteria.company.strip().lower() not in p_comp:
                continue

        # Filter: Location
        if criteria.location:
            p_loc = (p.location or "").strip().lower()
            if criteria.location.strip().lower() not in p_loc:
                continue

        # Filter: Education
        if criteria.education:
            p_edu = (p.education or "").strip().lower()
            if criteria.education.strip().lower() not in p_edu:
                continue

        # Filter: Interest (node attribute)
        if criteria.interest:
            target_interest = criteria.interest.strip().lower()
            interests_lower = [i.lower() for i in p.interest_list]
            if not any(target_interest in i for i in interests_lower):
                continue

        # Filter: Context Label (edge attribute to focal person)
        if criteria.context_label:
            target_label = criteria.context_label.strip().lower()
            labels_lower = [lbl.lower() for lbl in r.context_label_list]
            if not any(target_label in lbl for lbl in labels_lower):
                continue

        # Filter: Last Interaction Date
        if criteria.last_interaction:
            mode = criteria.last_interaction.strip().lower()
            if mode == "unknown" and r.last_interaction_date is not None:
                continue
            if mode == "recent_30" and (
                not r.last_interaction_date
                or (today - r.last_interaction_date) > timedelta(days=30)
            ):
                continue
            if mode == "recent_90" and (
                not r.last_interaction_date
                or (today - r.last_interaction_date) > timedelta(days=90)
            ):
                continue
            if mode == "recent_365" and (
                not r.last_interaction_date
                or (today - r.last_interaction_date) > timedelta(days=365)
            ):
                continue
            if mode == "older_365" and (
                not r.last_interaction_date
                or (today - r.last_interaction_date) <= timedelta(days=365)
            ):
                continue

        filtered.append(cv)

    total_unique = len(filtered)

    # Grouping
    dim = group_dimension.strip().lower() if group_dimension else "none"
    groups_dict: dict[str, list[ContactView]] = {}

    if dim in ("none", "", "all"):
        groups = [
            ContactGroup(
                dimension_value="All Matching Contacts",
                contacts=filtered,
                count=len(filtered),
            )
        ]
        return FilterGroupResult(
            groups=groups,
            total_unique_contacts=total_unique,
            active_filters=active_filters,
            group_dimension="none",
        )

    for cv in filtered:
        p = cv.person
        r = cv.relationship

        if dim == "company":
            key = p.company.strip() if p.company and p.company.strip() else "Unknown Company"
            groups_dict.setdefault(key, []).append(cv)

        elif dim == "location":
            key = p.location.strip() if p.location and p.location.strip() else "Unknown Location"
            groups_dict.setdefault(key, []).append(cv)

        elif dim == "education":
            key = (
                p.education.strip() if p.education and p.education.strip() else "Unknown Education"
            )
            groups_dict.setdefault(key, []).append(cv)

        elif dim == "context_label":
            labels = r.context_label_list
            if not labels:
                groups_dict.setdefault("Unlabeled Relationship Context", []).append(cv)
            else:
                for lbl in labels:
                    groups_dict.setdefault(lbl, []).append(cv)

        elif dim == "interest":
            interests = p.interest_list
            if not interests:
                groups_dict.setdefault("Unspecified Interests", []).append(cv)
            else:
                for it in interests:
                    groups_dict.setdefault(it, []).append(cv)

        elif dim == "last_interaction":
            if not r.last_interaction_date:
                key = "Unknown / No Recorded Interaction"
            else:
                diff = (today - r.last_interaction_date).days
                if diff <= 30:
                    key = "Past 30 Days"
                elif diff <= 90:
                    key = "1 to 3 Months Ago"
                elif diff <= 365:
                    key = "3 to 12 Months Ago"
                else:
                    key = "Over 1 Year Ago"
            groups_dict.setdefault(key, []).append(cv)

        else:
            groups_dict.setdefault("All Contacts", []).append(cv)

    # Sort groups alphabetically by dimension value, but put "Unknown..." at the end
    def _group_sort_key(name: str) -> tuple[int, str]:
        is_unknown = 1 if name.startswith(("Unknown", "Unlabeled", "Unspecified")) else 0
        return (is_unknown, name.lower())

    sorted_keys = sorted(groups_dict.keys(), key=_group_sort_key)
    result_groups = [
        ContactGroup(
            dimension_value=k,
            contacts=groups_dict[k],
            count=len(groups_dict[k]),
        )
        for k in sorted_keys
    ]

    return FilterGroupResult(
        groups=result_groups,
        total_unique_contacts=total_unique,
        active_filters=active_filters,
        group_dimension=dim,
    )


def get_filter_facet_options(
    conn: sqlite3.Connection,
    owner_id: str,
    focal_person_id: str,
) -> dict[str, list[str]]:
    """Return distinct options for filter dropdowns based on existing connected contacts."""
    contacts = list_contacts_connected_to_focal(conn, owner_id, focal_person_id)

    companies: set[str] = set()
    locations: set[str] = set()
    educations: set[str] = set()
    interests: set[str] = set()
    context_labels: set[str] = set()

    for cv in contacts:
        if cv.person.company:
            companies.add(cv.person.company)
        if cv.person.location:
            locations.add(cv.person.location)
        if cv.person.education:
            educations.add(cv.person.education)
        for it in cv.person.interest_list:
            interests.add(it)
        for lbl in cv.relationship.context_label_list:
            context_labels.add(lbl)

    return {
        "companies": sorted(companies),
        "locations": sorted(locations),
        "educations": sorted(educations),
        "interests": sorted(interests),
        "context_labels": sorted(context_labels),
    }


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


def reset_network_for_owner(
    conn: sqlite3.Connection,
    owner_id: str,
    focal_person_id: str,
) -> int:
    """Remove all persons, edges, and interactions for owner except the focal person.

    Returns the count of deleted non-focal persons.
    """
    conn.execute("DELETE FROM tasks WHERE owner_id = ?;", (owner_id,))
    conn.execute("DELETE FROM interactions WHERE owner_id = ?;", (owner_id,))
    conn.execute("DELETE FROM relationships WHERE owner_id = ?;", (owner_id,))
    conn.execute("DELETE FROM import_fingerprints WHERE owner_id = ?;", (owner_id,))
    cursor = conn.execute(
        "DELETE FROM persons WHERE owner_id = ? AND id != ?;",
        (owner_id, focal_person_id),
    )
    return cursor.rowcount


# ============================================================================
# Task & Action Item Services
# ============================================================================


def parse_action_item_deadline(text: str, base_date: date) -> date:
    """Extract a deadline date from action item text, defaulting to 1 week from base_date."""
    lower = text.lower()

    # 1. Match ISO date YYYY-MM-DD
    iso_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    if iso_match:
        try:
            return date.fromisoformat(iso_match.group(1))
        except ValueError:
            pass

    # 2. Match "in X days" or "within X days"
    days_match = re.search(r"\b(?:in|within)\s+(\d+)\s+days?\b", lower)
    if days_match:
        days = int(days_match.group(1))
        return base_date + timedelta(days=days)

    # 3. Match "in X weeks" or "within X weeks"
    weeks_match = re.search(r"\b(?:in|within)\s+(\d+)\s+weeks?\b", lower)
    if weeks_match:
        weeks = int(weeks_match.group(1))
        return base_date + timedelta(days=7 * weeks)

    # 4. Match "tomorrow"
    if "tomorrow" in lower:
        return base_date + timedelta(days=1)

    # 5. Match month and day like "by Oct 25", "by October 25th", "Nov 1", etc.
    month_names = {
        "jan": 1,
        "january": 1,
        "feb": 2,
        "february": 2,
        "mar": 3,
        "march": 3,
        "apr": 4,
        "april": 4,
        "may": 5,
        "jun": 6,
        "june": 6,
        "jul": 7,
        "july": 7,
        "aug": 8,
        "august": 8,
        "sep": 9,
        "sept": 9,
        "september": 9,
        "oct": 10,
        "october": 10,
        "nov": 11,
        "november": 11,
        "dec": 12,
        "december": 12,
    }
    month_pattern = (
        r"\b(?:by|on|before)?\s*"
        r"(jan|january|feb|february|mar|march|apr|april|may|jun|june|jul|july|"
        r"aug|august|sep|sept|september|oct|october|nov|november|dec|december)\s+"
        r"(\d{1,2})(?:st|nd|rd|th)?(?:\s*,?\s*(20\d{2}))?\b"
    )
    m_match = re.search(month_pattern, lower)
    if m_match:
        m_name = m_match.group(1)
        d_day = int(m_match.group(2))
        m_num = month_names.get(m_name, base_date.month)
        y_num = int(m_match.group(3)) if m_match.group(3) else base_date.year
        try:
            parsed = date(y_num, m_num, d_day)
            if parsed < base_date and not m_match.group(3):
                parsed = date(y_num + 1, m_num, d_day)
            return parsed
        except ValueError:
            pass

    # Default fallback: 1 week (7 days)
    return base_date + timedelta(days=7)


def create_task(
    conn: sqlite3.Connection,
    owner_id: str,
    title: str,
    deadline: date,
    contact_person_id: str | None = None,
    relationship_id: str | None = None,
    interaction_id: str | None = None,
    status: str = "pending",
) -> Task:
    """Create a new task with assigned deadline."""
    task_id = str(uuid.uuid4())
    clean_title = title.strip()
    if not clean_title:
        raise ValueError("Task title cannot be empty")

    conn.execute(
        """
        INSERT INTO tasks (
            id, owner_id, contact_person_id, relationship_id, interaction_id,
            title, deadline, status
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """,
        (
            task_id,
            owner_id,
            contact_person_id,
            relationship_id,
            interaction_id,
            clean_title,
            deadline.isoformat(),
            status,
        ),
    )
    task = get_task(conn, owner_id, task_id)
    assert task is not None
    return task


def get_task(conn: sqlite3.Connection, owner_id: str, task_id: str) -> Task | None:
    """Retrieve a single task by ID."""
    row = conn.execute(
        """
        SELECT
            t.id, t.owner_id, t.title, t.deadline, t.status,
            t.contact_person_id, t.relationship_id, t.interaction_id,
            t.completed_at, t.created_at, t.updated_at,
            p.first_name, p.last_name, p.company
        FROM tasks t
        LEFT JOIN persons p ON t.contact_person_id = p.id
        WHERE t.id = ? AND t.owner_id = ?;
        """,
        (task_id, owner_id),
    ).fetchone()
    if not row:
        return None
    return _row_to_task(row)


def list_tasks(
    conn: sqlite3.Connection,
    owner_id: str,
    status: str | None = None,
    contact_person_id: str | None = None,
) -> list[Task]:
    """List tasks for owner sorted by deadlines (pending first, closest deadline first)."""
    query = """
        SELECT
            t.id, t.owner_id, t.title, t.deadline, t.status,
            t.contact_person_id, t.relationship_id, t.interaction_id,
            t.completed_at, t.created_at, t.updated_at,
            p.first_name, p.last_name, p.company
        FROM tasks t
        LEFT JOIN persons p ON t.contact_person_id = p.id
        WHERE t.owner_id = ?
    """
    params: list[Any] = [owner_id]
    if status:
        query += " AND t.status = ?"
        params.append(status)
    if contact_person_id:
        query += " AND t.contact_person_id = ?"
        params.append(contact_person_id)

    query += """
        ORDER BY
            CASE WHEN t.status = 'pending' THEN 0 ELSE 1 END ASC,
            CASE WHEN t.status = 'pending' THEN t.deadline END ASC,
            t.completed_at DESC,
            t.deadline ASC;
    """
    rows = conn.execute(query, params).fetchall()
    return [_row_to_task(r) for r in rows]


def toggle_task_status(conn: sqlite3.Connection, owner_id: str, task_id: str) -> Task | None:
    """Toggle a task between pending and completed."""
    task = get_task(conn, owner_id, task_id)
    if not task:
        return None
    new_status = "completed" if task.status == "pending" else "pending"
    completed_at = datetime.now().isoformat() if new_status == "completed" else None
    now = datetime.now().isoformat()
    conn.execute(
        """
        UPDATE tasks
        SET status = ?, completed_at = ?, updated_at = ?
        WHERE id = ? AND owner_id = ?;
        """,
        (new_status, completed_at, now, task_id, owner_id),
    )
    return get_task(conn, owner_id, task_id)


def delete_task(conn: sqlite3.Connection, owner_id: str, task_id: str) -> bool:
    """Permanently delete a task."""
    cursor = conn.execute("DELETE FROM tasks WHERE id = ? AND owner_id = ?;", (task_id, owner_id))
    return cursor.rowcount > 0


def count_pending_tasks(conn: sqlite3.Connection, owner_id: str) -> int:
    """Count pending tasks for owner."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE owner_id = ? AND status = 'pending';",
        (owner_id,),
    ).fetchone()
    return int(row["cnt"]) if row else 0


def backfill_tasks_from_existing_interactions(conn: sqlite3.Connection, owner_id: str) -> int:
    """Scan existing interactions for action items and create tasks if not already created."""
    rows = conn.execute(
        """
        SELECT i.id, i.relationship_id, i.interaction_date, i.content,
               r.person_a_id, r.person_b_id
        FROM interactions i
        JOIN relationships r ON i.relationship_id = r.id
        WHERE i.owner_id = ?;
        """,
        (owner_id,),
    ).fetchall()

    owner = get_owner(conn, owner_id)
    focal_id = owner.focal_person_id if owner else None

    count = 0
    for row in rows:
        interaction_id = row["id"]
        existing = conn.execute(
            "SELECT COUNT(*) as c FROM tasks WHERE interaction_id = ?;", (interaction_id,)
        ).fetchone()
        if existing and existing["c"] > 0:
            continue

        content = str(row["content"])
        ai_match = re.search(
            r"\*\*Action Items & Next Steps:\*\*\s*([\s\S]*?)(?:\n\n\*\*|\Z)", content
        )
        if ai_match:
            lines = ai_match.group(1).strip().splitlines()
            for line in lines:
                clean = re.sub(r"^(?:-\s*\[\s*\]\s*|-\s*)", "", line).strip()
                if clean:
                    contact_id = None
                    if focal_id:
                        if row["person_a_id"] == focal_id:
                            contact_id = row["person_b_id"]
                        elif row["person_b_id"] == focal_id:
                            contact_id = row["person_a_id"]

                    idate_str = row["interaction_date"]
                    idate = (
                        date.fromisoformat(idate_str[:10])
                        if isinstance(idate_str, str)
                        else date.today()
                    )
                    deadline = parse_action_item_deadline(clean, idate)
                    create_task(
                        conn=conn,
                        owner_id=owner_id,
                        title=clean,
                        deadline=deadline,
                        contact_person_id=contact_id,
                        relationship_id=row["relationship_id"],
                        interaction_id=interaction_id,
                    )
                    count += 1
    return count


def _row_to_task(row: sqlite3.Row) -> Task:
    deadline_val = row["deadline"]
    if isinstance(deadline_val, str):
        parsed_deadline = date.fromisoformat(deadline_val[:10])
    elif isinstance(deadline_val, date):
        parsed_deadline = deadline_val
    else:
        parsed_deadline = date.today() + timedelta(days=7)

    parts = [row["first_name"] or "", row["last_name"] or ""]
    cname = " ".join(p for p in parts if p).strip() or None

    return Task(
        id=str(row["id"]),
        owner_id=str(row["owner_id"]),
        title=str(row["title"]),
        deadline=parsed_deadline,
        status=str(row["status"]),
        contact_person_id=str(row["contact_person_id"])
        if row["contact_person_id"] is not None
        else None,
        relationship_id=str(row["relationship_id"]) if row["relationship_id"] is not None else None,
        interaction_id=str(row["interaction_id"]) if row["interaction_id"] is not None else None,
        completed_at=str(row["completed_at"]) if row["completed_at"] is not None else None,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        contact_name=cname,
        contact_company=str(row["company"]) if row["company"] is not None else None,
    )


def seed_sample_tasks_for_test_contacts(conn: sqlite3.Connection, owner_id: str) -> list[Task]:
    """Seed ~5 realistic follow-up tasks with deadlines across the upcoming month."""
    owner = get_owner(conn, owner_id)
    focal_id = owner.focal_person_id if owner else None

    sample_specs = [
        (
            "Sophia",
            "Review Duke Student Angel Syndicate investment memo draft",
            3,
        ),
        (
            "Liam",
            "Share research paper draft on robotics sensor fusion",
            7,
        ),
        (
            "Tariq",
            "Sync on Fuqua portfolio modeling milestone & code review",
            12,
        ),
        (
            "Elena",
            "Schedule coffee chat to discuss computer vision internship opportunities",
            18,
        ),
        (
            "Kenji",
            "Follow up on real-time sensor pipeline architecture specs",
            26,
        ),
    ]

    today = date.today()
    created_tasks: list[Task] = []

    persons = conn.execute(
        "SELECT id, first_name, last_name FROM persons WHERE owner_id = ? AND id != ?;",
        (owner_id, focal_id or ""),
    ).fetchall()
    if not persons:
        return []

    persons_by_first = {p["first_name"].strip().lower(): p["id"] for p in persons}

    for spec_first, title, days_offset in sample_specs:
        target_person_id = persons_by_first.get(spec_first.lower())
        if not target_person_id:
            target_person_id = persons[len(created_tasks) % len(persons)]["id"]

        existing = conn.execute(
            "SELECT COUNT(*) as c FROM tasks WHERE owner_id = ? AND title = ?;",
            (owner_id, title),
        ).fetchone()
        if existing and existing["c"] > 0:
            continue

        rel_id = None
        if target_person_id and focal_id:
            rel = get_relationship_between(conn, owner_id, focal_id, target_person_id)
            if rel:
                rel_id = rel.id

        task = create_task(
            conn=conn,
            owner_id=owner_id,
            title=title,
            deadline=today + timedelta(days=days_offset),
            contact_person_id=target_person_id,
            relationship_id=rel_id,
        )
        created_tasks.append(task)

    return created_tasks


GRAPH_CLUSTER_PALETTE = [
    "#2563eb",  # Calm Blue
    "#0d9488",  # Muted Teal
    "#475569",  # Slate
    "#059669",  # Sage Green
    "#d97706",  # Warm Amber
    "#7c3aed",  # Soft Violet
    "#0891b2",  # Ocean
    "#4b5563",  # Cool Gray
]


def get_network_graph_payload(
    conn: sqlite3.Connection,
    owner_id: str,
    focal_person: Person,
    filter_result: FilterGroupResult,
    active_split: NetworkSplit | None = None,
) -> dict[str, Any]:
    """Generate structured nodes, edges, and planar radial coordinates for network visualization."""
    # 1. Fetch pending task counts per contact
    pending_tasks = conn.execute(
        """
        SELECT contact_person_id, COUNT(*) as c
        FROM tasks
        WHERE owner_id = ? AND status = 'pending' AND contact_person_id IS NOT NULL
        GROUP BY contact_person_id;
        """,
        (owner_id,),
    ).fetchall()
    task_counts = {str(r["contact_person_id"]): int(r["c"]) for r in pending_tasks}

    # 2. Collect unique contacts and assign primary clusters
    seen_ids: set[str] = set()
    unique_contacts: list[tuple[ContactView, str]] = []

    for grp in filter_result.groups:
        cluster_from_group = grp.dimension_value if filter_result.group_dimension != "none" else ""
        for c in grp.contacts:
            if c.person.id not in seen_ids:
                seen_ids.add(c.person.id)
                if active_split and c.person.id in active_split.assignments:
                    resolved_cluster = active_split.assignments[c.person.id].group_name
                else:
                    resolved_cluster = cluster_from_group
                    if not resolved_cluster:
                        if c.person.company and c.person.company.strip():
                            resolved_cluster = c.person.company.strip()
                        elif c.relationship.context_label_list:
                            resolved_cluster = c.relationship.context_label_list[0].strip()
                        elif c.person.location and c.person.location.strip():
                            resolved_cluster = c.person.location.strip()
                        elif c.person.education and c.person.education.strip():
                            resolved_cluster = c.person.education.strip()
                        else:
                            resolved_cluster = "Network Connections"
                unique_contacts.append((c, resolved_cluster))

    # 3. Group by cluster to assign angular sectors
    cluster_buckets: dict[str, list[ContactView]] = {}
    for c, cl in unique_contacts:
        cluster_buckets.setdefault(cl, []).append(c)

    # Sort clusters by size descending
    sorted_clusters = sorted(cluster_buckets.items(), key=lambda kv: len(kv[1]), reverse=True)

    cluster_color_map: dict[str, str] = {}
    cluster_meta: list[dict[str, Any]] = []

    total_member_count = max(1, sum(len(members) for _, members in sorted_clusters))
    current_angle = 0.0

    split_colors = {g.group_name: g.color for g in active_split.groups} if active_split else {}

    cluster_sectors: dict[str, tuple[float, float]] = {}
    for i, (cluster_name, members) in enumerate(sorted_clusters):
        color = split_colors.get(
            cluster_name, GRAPH_CLUSTER_PALETTE[i % len(GRAPH_CLUSTER_PALETTE)]
        )
        cluster_color_map[cluster_name] = color
        count = len(members)
        angular_span = (2.0 * math.pi * count) / total_member_count
        start_angle = current_angle
        end_angle = current_angle + angular_span
        current_angle = end_angle

        cluster_sectors[cluster_name] = (start_angle, end_angle)
        cluster_meta.append(
            {
                "name": cluster_name,
                "color": color,
                "count": count,
                "start_angle": round(start_angle, 3),
                "end_angle": round(end_angle, 3),
            }
        )

    # 4. Construct Nodes: Focal Person (center) + Contact Nodes
    focal_avatar = (focal_person.first_name[0] if focal_person.first_name else "") + (
        focal_person.last_name[0] if focal_person.last_name else ""
    )
    nodes: list[dict[str, Any]] = [
        {
            "id": focal_person.id,
            "name": focal_person.display_name,
            "first_name": focal_person.first_name,
            "last_name": focal_person.last_name,
            "job_title": focal_person.job_title or "Focal Person",
            "company": focal_person.company or "",
            "location": focal_person.location or "",
            "education": focal_person.education or "",
            "interests": focal_person.interest_list,
            "context_labels": [],
            "last_interaction": None,
            "summary": "Network center.",
            "tasks_count": 0,
            "avatar": focal_avatar.upper() if focal_avatar else "ME",
            "is_focal": True,
            "cluster": "Center",
            "cluster_color": "#64748b",  # Calm slate
            "split_rationale": None,
            "x": 0.0,
            "y": 0.0,
            "radius": 0.0,
            "angle": 0.0,
        }
    ]

    # Planar radial coordinates for contacts
    base_radius = 210.0
    for cluster_name, members in sorted_clusters:
        start_angle, end_angle = cluster_sectors[cluster_name]
        m = len(members)
        for j, c in enumerate(members):
            # Angular position within wedge
            angle = start_angle + (end_angle - start_angle) * ((j + 0.5) / max(1, m))
            # Stagger radial distance to avoid collisions if multiple nodes in sector
            orbit_tier = j % 3
            radius = base_radius + orbit_tier * 65.0
            x = round(radius * math.cos(angle), 2)
            y = round(radius * math.sin(angle), 2)

            contact_avatar = (c.person.first_name[0] if c.person.first_name else "") + (
                c.person.last_name[0] if c.person.last_name else ""
            )
            last_date_str = (
                c.relationship.last_interaction_date.strftime("%b %d, %Y")
                if c.relationship.last_interaction_date
                else None
            )

            split_info = active_split.assignments.get(c.person.id) if active_split else None
            split_rationale = split_info.rationale if split_info else None

            nodes.append(
                {
                    "id": c.person.id,
                    "name": c.person.display_name,
                    "first_name": c.person.first_name,
                    "last_name": c.person.last_name,
                    "job_title": c.person.job_title or "",
                    "company": c.person.company or "",
                    "location": c.person.location or "",
                    "education": c.person.education or "",
                    "interests": c.person.interest_list,
                    "context_labels": c.relationship.context_label_list,
                    "last_interaction": last_date_str,
                    "summary": c.relationship.summary or "",
                    "tasks_count": task_counts.get(c.person.id, 0),
                    "avatar": contact_avatar.upper(),
                    "is_focal": False,
                    "cluster": cluster_name,
                    "cluster_color": cluster_color_map[cluster_name],
                    "split_rationale": split_rationale,
                    "x": x,
                    "y": y,
                    "radius": round(radius, 2),
                    "angle": round(angle, 3),
                }
            )

    # 5. Construct Edges: Direct focal edges + inter-contact edges
    edges: list[dict[str, Any]] = []
    # Direct focal edges
    for c, _ in unique_contacts:
        edges.append(
            {
                "id": f"edge-focal-{c.person.id}",
                "source": focal_person.id,
                "target": c.person.id,
                "connected_on": (
                    c.relationship.connected_on.strftime("%b %d, %Y")
                    if c.relationship.connected_on
                    else None
                ),
                "context": (
                    c.relationship.context_label_list[0]
                    if c.relationship.context_label_list
                    else "Connection"
                ),
                "is_focal_edge": True,
            }
        )

    # Cross-contact edges (if any exist between displayed contacts in the database)
    if len(seen_ids) >= 2:
        id_list = list(seen_ids)
        placeholders = ",".join("?" for _ in id_list)
        cross_rows = conn.execute(
            f"""
            SELECT id, person_a_id, person_b_id, connected_on, context_labels, summary
            FROM relationships
            WHERE owner_id = ?
              AND person_a_id IN ({placeholders})
              AND person_b_id IN ({placeholders})
              AND person_a_id != ? AND person_b_id != ?;
            """,
            [owner_id, *id_list, *id_list, focal_person.id, focal_person.id],
        ).fetchall()

        for r in cross_rows:
            p_a = str(r["person_a_id"])
            p_b = str(r["person_b_id"])
            if p_a in seen_ids and p_b in seen_ids:
                ctx_raw = str(r["context_labels"]) if r["context_labels"] else ""
                first_ctx = [x.strip() for x in ctx_raw.split(",") if x.strip()]
                edges.append(
                    {
                        "id": str(r["id"]),
                        "source": p_a,
                        "target": p_b,
                        "connected_on": str(r["connected_on"]) if r["connected_on"] else None,
                        "context": first_ctx[0] if first_ctx else "Mutual Connection",
                        "is_focal_edge": False,
                    }
                )

    return {
        "focal_person_id": focal_person.id,
        "nodes": nodes,
        "edges": edges,
        "clusters": cluster_meta,
        "total_nodes": len(nodes),
        "total_edges": len(edges),
    }


def _row_to_network_split(row: sqlite3.Row) -> NetworkSplit:
    """Map database row to NetworkSplit dataclass."""
    data = json.loads(str(row["split_data"]))
    groups = [
        SplitGroupStats(
            group_name=g["group_name"],
            description=g["description"],
            contact_count=int(g["contact_count"]),
            percentage_of_network=float(g["percentage_of_network"]),
            companies=list(g.get("companies", [])),
            top_locations=list(g.get("top_locations", [])),
            top_interests=list(g.get("top_interests", [])),
            active_touchpoint_rate=float(g.get("active_touchpoint_rate", 0.0)),
            pending_tasks_count=int(g.get("pending_tasks_count", 0)),
            strategic_insight=g.get("strategic_insight", ""),
            color=g.get("color", "#6366f1"),
        )
        for g in data.get("groups", [])
    ]
    assignments = {
        pid: ContactSplitAssignment(
            person_id=item["person_id"],
            group_name=item["group_name"],
            rationale=item.get("rationale", ""),
        )
        for pid, item in data.get("assignments", {}).items()
    }
    return NetworkSplit(
        id=str(row["id"]),
        owner_id=str(row["owner_id"]),
        prompt=str(row["prompt"]),
        title=str(row["title"]),
        groups=groups,
        assignments=assignments,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


def save_network_split(
    conn: sqlite3.Connection,
    owner_id: str,
    prompt: str,
    split_payload: dict[str, Any],
) -> NetworkSplit:
    """Save an AI-generated network split and mark it as active for the owner."""
    split_id = str(uuid.uuid4())
    now = datetime.now().isoformat()
    title = split_payload.get("title", "Network Split")
    data_json = json.dumps(split_payload)

    conn.execute(
        """
        INSERT INTO network_splits (id, owner_id, prompt, title, split_data, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?);
        """,
        (split_id, owner_id, prompt, title, data_json, now, now),
    )

    # Set as active split
    conn.execute(
        """
        INSERT INTO active_splits (owner_id, split_id, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(owner_id) DO UPDATE SET
            split_id = excluded.split_id,
            updated_at = excluded.updated_at;
        """,
        (owner_id, split_id, now),
    )
    conn.commit()

    row = conn.execute("SELECT * FROM network_splits WHERE id = ?;", (split_id,)).fetchone()
    assert row is not None
    return _row_to_network_split(row)


def get_active_network_split(
    conn: sqlite3.Connection,
    owner_id: str,
) -> NetworkSplit | None:
    """Get the currently active network split for the owner, if one exists."""
    cursor = conn.execute(
        """
        SELECT ns.* FROM network_splits ns
        JOIN active_splits a ON ns.id = a.split_id
        WHERE a.owner_id = ?;
        """,
        (owner_id,),
    )
    row = cursor.fetchone()
    return _row_to_network_split(row) if row else None


def clear_active_network_split(conn: sqlite3.Connection, owner_id: str) -> None:
    """Clear active network split, returning the user to standard grouping/flat view."""
    conn.execute("DELETE FROM active_splits WHERE owner_id = ?;", (owner_id,))
    conn.commit()


def get_grouped_split_view(
    active_split: NetworkSplit,
    contacts: list[ContactView],
) -> list[GroupedSplitView]:
    """Group contact views according to the active AI split and include rationales."""
    contact_map = {c.person.id: c for c in contacts}
    group_buckets: dict[str, list[tuple[ContactView, str]]] = {
        g.group_name: [] for g in active_split.groups
    }

    for pid, assign in active_split.assignments.items():
        if pid in contact_map:
            c = contact_map[pid]
            g_name = assign.group_name
            if g_name in group_buckets:
                group_buckets[g_name].append((c, assign.rationale))
            elif active_split.groups:
                group_buckets.setdefault(active_split.groups[0].group_name, []).append(
                    (c, assign.rationale)
                )

    result: list[GroupedSplitView] = []
    for g_stats in active_split.groups:
        c_list = group_buckets.get(g_stats.group_name, [])
        result.append(GroupedSplitView(stats=g_stats, contacts=c_list))
    return result
