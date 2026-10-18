"""Safe CSV import preview and commit engine for Inner Circle."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Literal

from app.csv_parser import ParsedContactRow
from app.models import Person
from app.services import (
    create_contact_with_focal_edge,
    create_relationship,
    get_owner,
    get_person,
    get_relationship_between,
    update_person,
    update_relationship,
)

RowAction = Literal["CREATE", "UPDATE", "UNCHANGED", "CONFLICT", "ERROR"]


@dataclass(frozen=True)
class FieldConflict:
    field_name: str
    existing_value: str | date | None
    imported_value: str | date | None


@dataclass(frozen=True)
class RowPreview:
    row_number: int
    action: RowAction
    display_name: str
    matched_person_id: str | None
    matched_by: str | None  # "url", "email", "fingerprint", or None
    filled_fields: dict[str, str | date]
    conflicts: list[FieldConflict]
    error_message: str | None
    row_data: ParsedContactRow


@dataclass(frozen=True)
class ImportPreview:
    owner_id: str
    focal_person_id: str
    total_rows: int
    rows: list[RowPreview]
    create_count: int
    update_count: int
    unchanged_count: int
    conflict_count: int
    error_count: int

    @property
    def can_commit(self) -> bool:
        return self.conflict_count == 0 and self.error_count == 0 and self.total_rows > 0


@dataclass(frozen=True)
class ImportCommitResult:
    created_count: int
    updated_count: int
    unchanged_count: int
    total_processed: int


def _compute_fingerprint(
    first_name: str, last_name: str, company: str | None, job_title: str | None
) -> str:
    """Compute deterministic SHA-256 fingerprint for identifier-free contacts."""
    first = first_name.strip().lower()
    last = last_name.strip().lower()
    comp = (company or "").strip().lower()
    title = (job_title or "").strip().lower()
    key = f"{first}|{last}|{comp}|{title}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def _normalize_profile_url(url: str | None) -> str | None:
    if not url:
        return None
    cleaned = url.strip().rstrip("/")
    return cleaned if cleaned else None


def _find_matching_person(
    conn: sqlite3.Connection,
    owner_id: str,
    row: ParsedContactRow,
) -> tuple[Person | None, str | None, str | None]:
    """Find matching person within owner's graph by profile_url, email, or fingerprint.

    Returns: (matched_person, matched_by_reason, conflict_error_message)
    """
    norm_url = _normalize_profile_url(row.profile_url)
    norm_email = row.email

    url_match: Person | None = None
    email_match: Person | None = None

    if norm_url:
        cursor = conn.execute(
            "SELECT * FROM persons WHERE owner_id = ? AND profile_url = ?;",
            (owner_id, norm_url),
        )
        url_rows = cursor.fetchall()
        if len(url_rows) > 1:
            return None, None, f"Profile URL '{norm_url}' matches multiple existing contacts."
        if len(url_rows) == 1:
            url_match = get_person(conn, owner_id, str(url_rows[0]["id"]))

    if norm_email:
        cursor = conn.execute(
            "SELECT * FROM persons WHERE owner_id = ? AND email = ?;",
            (owner_id, norm_email),
        )
        email_rows = cursor.fetchall()
        if len(email_rows) > 1:
            return None, None, f"Email '{norm_email}' matches multiple existing contacts."
        if len(email_rows) == 1:
            email_match = get_person(conn, owner_id, str(email_rows[0]["id"]))

    # If both identifiers matched different people, it's an ambiguous conflict
    if url_match and email_match and url_match.id != email_match.id:
        return (
            None,
            None,
            (
                f"Identifier conflict: URL matches '{url_match.display_name}' "
                f"but email matches '{email_match.display_name}'."
            ),
        )

    if url_match:
        return url_match, "url", None
    if email_match:
        return email_match, "email", None

    # Check fingerprint bookkeeping for rows without URL or email
    if not norm_url and not norm_email:
        fp = _compute_fingerprint(row.first_name, row.last_name, row.company, row.job_title)
        cursor = conn.execute(
            "SELECT person_id FROM import_fingerprints "
            "WHERE owner_id = ? AND fingerprint_hash = ?;",
            (owner_id, fp),
        )
        fp_row = cursor.fetchone()
        if fp_row:
            p = get_person(conn, owner_id, str(fp_row["person_id"]))
            if p:
                return p, "fingerprint", None

    return None, None, None


def _evaluate_field(
    field_name: str,
    existing_val: str | None,
    imported_val: str | None,
    filled: dict[str, str | date],
    conflicts: list[FieldConflict],
) -> bool:
    """Compare an existing and imported string field.

    Fills missing values or flags conflicts. Blank input never erases existing data.
    """
    if imported_val is None or imported_val == "":
        return False
    if existing_val is None or existing_val == "":
        filled[field_name] = imported_val
        return True
    if existing_val != imported_val:
        conflicts.append(
            FieldConflict(
                field_name=field_name,
                existing_value=existing_val,
                imported_value=imported_val,
            )
        )
    return False


def preview_import(
    conn: sqlite3.Connection,
    owner_id: str,
    parsed_rows: list[ParsedContactRow],
) -> ImportPreview:
    """Analyze parsed rows against current DB state without mutating database."""
    owner = get_owner(conn, owner_id)
    if not owner or not owner.focal_person_id:
        raise ValueError(f"Owner '{owner_id}' or focal person not found.")

    focal_id = owner.focal_person_id
    previews: list[RowPreview] = []
    create_count = 0
    update_count = 0
    unchanged_count = 0
    conflict_count = 0
    error_count = 0

    for row in parsed_rows:
        matched_person, matched_by, err = _find_matching_person(conn, owner_id, row)

        if err:
            previews.append(
                RowPreview(
                    row_number=row.row_number,
                    action="ERROR",
                    display_name=row.display_name,
                    matched_person_id=None,
                    matched_by=None,
                    filled_fields={},
                    conflicts=[],
                    error_message=err,
                    row_data=row,
                )
            )
            error_count += 1
            continue

        if not matched_person:
            # Completely new contact
            previews.append(
                RowPreview(
                    row_number=row.row_number,
                    action="CREATE",
                    display_name=row.display_name,
                    matched_person_id=None,
                    matched_by=None,
                    filled_fields={},
                    conflicts=[],
                    error_message=None,
                    row_data=row,
                )
            )
            create_count += 1
            continue

        # Existing matched contact: evaluate fields and merge policy
        conflicts: list[FieldConflict] = []
        filled: dict[str, str | date] = {}
        has_differences = False

        if _evaluate_field(
            "first_name", matched_person.first_name, row.first_name, filled, conflicts
        ):
            has_differences = True
        if _evaluate_field("last_name", matched_person.last_name, row.last_name, filled, conflicts):
            has_differences = True
        if _evaluate_field("company", matched_person.company, row.company, filled, conflicts):
            has_differences = True
        if _evaluate_field("job_title", matched_person.job_title, row.job_title, filled, conflicts):
            has_differences = True
        if _evaluate_field("email", matched_person.email, row.email, filled, conflicts):
            has_differences = True
        if _evaluate_field(
            "profile_url", matched_person.profile_url, row.profile_url, filled, conflicts
        ):
            has_differences = True

        # Check relationship edge with focal person
        existing_rel = get_relationship_between(conn, owner_id, focal_id, matched_person.id)
        if not existing_rel:
            if row.connected_on:
                filled["connected_on"] = row.connected_on
            has_differences = True
        elif row.connected_on is not None:
            if existing_rel.connected_on is None:
                filled["connected_on"] = row.connected_on
                has_differences = True
            elif existing_rel.connected_on != row.connected_on:
                conflicts.append(
                    FieldConflict(
                        field_name="connected_on",
                        existing_value=existing_rel.connected_on,
                        imported_value=row.connected_on,
                    )
                )

        if conflicts:
            action: RowAction = "CONFLICT"
            conflict_count += 1
        elif has_differences:
            action = "UPDATE"
            update_count += 1
        else:
            action = "UNCHANGED"
            unchanged_count += 1

        previews.append(
            RowPreview(
                row_number=row.row_number,
                action=action,
                display_name=matched_person.display_name,
                matched_person_id=matched_person.id,
                matched_by=matched_by,
                filled_fields=filled,
                conflicts=conflicts,
                error_message=None,
                row_data=row,
            )
        )

    return ImportPreview(
        owner_id=owner_id,
        focal_person_id=focal_id,
        total_rows=len(parsed_rows),
        rows=previews,
        create_count=create_count,
        update_count=update_count,
        unchanged_count=unchanged_count,
        conflict_count=conflict_count,
        error_count=error_count,
    )


def commit_import(
    conn: sqlite3.Connection,
    owner_id: str,
    preview: ImportPreview,
) -> ImportCommitResult:
    """Commit the previewed import within a single atomic transaction.

    Fails and rolls back if preview has any conflicts or errors.
    """
    if not preview.can_commit:
        raise ValueError(
            f"Cannot commit import: preview contains {preview.conflict_count} conflicts "
            f"and {preview.error_count} errors."
        )

    focal_id = preview.focal_person_id
    created_count = 0
    updated_count = 0
    unchanged_count = 0

    for item in preview.rows:
        row = item.row_data

        if item.action == "UNCHANGED":
            unchanged_count += 1
            continue

        if item.action == "CREATE":
            person, _ = create_contact_with_focal_edge(
                conn=conn,
                owner_id=owner_id,
                focal_person_id=focal_id,
                first_name=row.first_name,
                last_name=row.last_name,
                email=row.email,
                profile_url=_normalize_profile_url(row.profile_url),
                company=row.company,
                job_title=row.job_title,
                location=None,
                connected_on=row.connected_on,
                notes=None,
            )
            # Store fingerprint if row lacks profile_url and email
            if not row.profile_url and not row.email:
                fp = _compute_fingerprint(row.first_name, row.last_name, row.company, row.job_title)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO import_fingerprints (
                        id, owner_id, person_id, fingerprint_hash
                    ) VALUES (?, ?, ?, ?);
                    """,
                    (str(uuid.uuid4()), owner_id, person.id, fp),
                )
            created_count += 1

        elif item.action == "UPDATE":
            assert item.matched_person_id is not None
            current_p = get_person(conn, owner_id, item.matched_person_id)
            assert current_p is not None

            # Merge filled fields
            new_first = str(item.filled_fields.get("first_name", current_p.first_name))
            new_last = str(item.filled_fields.get("last_name", current_p.last_name))
            new_email = (
                str(item.filled_fields["email"])
                if "email" in item.filled_fields
                else current_p.email
            )
            new_url = (
                str(item.filled_fields["profile_url"])
                if "profile_url" in item.filled_fields
                else current_p.profile_url
            )
            new_company = (
                str(item.filled_fields["company"])
                if "company" in item.filled_fields
                else current_p.company
            )
            new_title = (
                str(item.filled_fields["job_title"])
                if "job_title" in item.filled_fields
                else current_p.job_title
            )

            update_person(
                conn=conn,
                owner_id=owner_id,
                person_id=current_p.id,
                first_name=new_first,
                last_name=new_last,
                email=new_email,
                profile_url=new_url,
                company=new_company,
                job_title=new_title,
                location=current_p.location,
            )

            # Check / update edge to focal person
            edge = get_relationship_between(conn, owner_id, focal_id, current_p.id)
            if not edge:
                create_relationship(
                    conn=conn,
                    owner_id=owner_id,
                    person_1_id=focal_id,
                    person_2_id=current_p.id,
                    connected_on=row.connected_on,
                    notes=None,
                )
            elif "connected_on" in item.filled_fields:
                new_date = item.filled_fields["connected_on"]
                assert isinstance(new_date, date)
                update_relationship(
                    conn=conn,
                    owner_id=owner_id,
                    relationship_id=edge.id,
                    connected_on=new_date,
                    notes=edge.notes,
                )

            updated_count += 1

    return ImportCommitResult(
        created_count=created_count,
        updated_count=updated_count,
        unchanged_count=unchanged_count,
        total_processed=len(preview.rows),
    )
