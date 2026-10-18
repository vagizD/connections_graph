"""Typed CSV parser for LinkedIn Connections exports."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime
from typing import TextIO

REQUIRED_COLUMNS: tuple[str, ...] = (
    "First Name",
    "Last Name",
    "URL",
    "Email Address",
    "Company",
    "Position",
    "Connected On",
)


@dataclass(frozen=True)
class ParsedContactRow:
    """A validated contact row extracted from a CSV export."""

    row_number: int
    first_name: str
    last_name: str
    profile_url: str | None
    email: str | None
    company: str | None
    job_title: str | None
    connected_on: date | None

    @property
    def display_name(self) -> str:
        parts = [self.first_name, self.last_name]
        return " ".join(p for p in parts if p).strip()


@dataclass(frozen=True)
class ParseError:
    """An error encountered on a specific CSV row."""

    row_number: int
    message: str


@dataclass(frozen=True)
class ParseResult:
    """The complete outcome of a CSV parsing run."""

    rows: list[ParsedContactRow]
    errors: list[ParseError]
    header_row_number: int


def _parse_date(date_str: str) -> date:
    """Parse date from expected formats (e.g. '16 Oct 2026' or '2026-10-16')."""
    clean_str = date_str.strip()
    # Format: 16 Oct 2026
    try:
        return datetime.strptime(clean_str, "%d %b %Y").date()
    except ValueError:
        pass

    # Format: 2026-10-16
    try:
        return datetime.strptime(clean_str, "%Y-%m-%d").date()
    except ValueError:
        pass

    raise ValueError(
        f"Unrecognized date format '{date_str}'. Expected 'DD Mon YYYY' "
        f"(e.g. 16 Oct 2026) or 'YYYY-MM-DD'."
    )


def _normalize_optional(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped if stripped else None


def _normalize_email(value: str | None) -> str | None:
    norm = _normalize_optional(value)
    return norm.lower() if norm else None


def _extract_cell(row: list[str], indices: dict[str, int], col_name: str) -> str:
    idx = indices[col_name]
    return row[idx].strip() if idx < len(row) else ""


def parse_connections_csv(stream_or_str: str | bytes | TextIO) -> ParseResult:
    """Parse a LinkedIn Connections CSV from text, bytes, or a file-like stream.

    Detects the header dynamically by scanning rows for required column names,
    skipping preamble lines and metadata notes. Handles UTF-8 BOM, quoted values,
    and missing fields. Never writes to the database.
    """
    if isinstance(stream_or_str, bytes):
        text = stream_or_str.decode("utf-8-sig")
        file_obj: TextIO = io.StringIO(text)
    elif isinstance(stream_or_str, str):
        # Handle string with potential BOM marker
        text = stream_or_str.removeprefix("\ufeff")
        file_obj = io.StringIO(text)
    else:
        file_obj = stream_or_str

    reader = csv.reader(file_obj)

    header_row_number: int | None = None
    column_indices: dict[str, int] = {}
    current_line = 0

    # Scan for header row
    for row in reader:
        current_line += 1
        stripped_row = [col.strip() for col in row]
        if all(req in stripped_row for req in REQUIRED_COLUMNS):
            header_row_number = current_line
            column_indices = {col: stripped_row.index(col) for col in REQUIRED_COLUMNS}
            break

    if header_row_number is None:
        return ParseResult(
            rows=[],
            errors=[
                ParseError(
                    row_number=0,
                    message=(
                        "Could not locate required CSV header row containing all expected columns: "
                        f"{', '.join(REQUIRED_COLUMNS)}."
                    ),
                )
            ],
            header_row_number=0,
        )

    parsed_rows: list[ParsedContactRow] = []
    errors: list[ParseError] = []

    for raw_row in reader:
        current_line += 1
        # Skip completely empty lines
        if not raw_row or all(not cell.strip() for cell in raw_row):
            continue

        first_name = _extract_cell(raw_row, column_indices, "First Name")
        last_name = _extract_cell(raw_row, column_indices, "Last Name")
        raw_url = _extract_cell(raw_row, column_indices, "URL")
        raw_email = _extract_cell(raw_row, column_indices, "Email Address")
        raw_company = _extract_cell(raw_row, column_indices, "Company")
        raw_position = _extract_cell(raw_row, column_indices, "Position")
        raw_connected = _extract_cell(raw_row, column_indices, "Connected On")

        if not first_name and not last_name:
            errors.append(
                ParseError(
                    row_number=current_line,
                    message=(
                        "Missing both First Name and Last Name. "
                        "At least one name component is required."
                    ),
                )
            )
            continue

        parsed_date: date | None = None
        if raw_connected:
            try:
                parsed_date = _parse_date(raw_connected)
            except ValueError as ex:
                errors.append(ParseError(row_number=current_line, message=str(ex)))
                continue

        parsed_rows.append(
            ParsedContactRow(
                row_number=current_line,
                first_name=first_name,
                last_name=last_name,
                profile_url=_normalize_optional(raw_url),
                email=_normalize_email(raw_email),
                company=_normalize_optional(raw_company),
                job_title=_normalize_optional(raw_position),
                connected_on=parsed_date,
            )
        )

    return ParseResult(
        rows=parsed_rows,
        errors=errors,
        header_row_number=header_row_number,
    )
