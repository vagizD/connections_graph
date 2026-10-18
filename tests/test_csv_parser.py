"""Unit tests for the LinkedIn CSV parser."""

from datetime import date
from pathlib import Path

from app.csv_parser import parse_connections_csv

FIXTURES_DIR = Path(__file__).parent.parent / "data" / "fixtures"
REFERENCE_DIR = Path(__file__).parent.parent / "data" / "reference"


def test_parse_fictional_contacts_10() -> None:
    fixture_path = FIXTURES_DIR / "fictional_contacts_10.csv"
    assert fixture_path.exists(), f"Fixture missing: {fixture_path}"

    with fixture_path.open("rb") as f:
        result = parse_connections_csv(f.read())

    assert len(result.errors) == 0, f"Unexpected errors: {result.errors}"
    assert len(result.rows) == 10
    assert result.header_row_number == 4

    first = result.rows[0]
    assert first.first_name == "Alice"
    assert first.last_name == "Smith"
    assert first.profile_url == "https://www.linkedin.com/in/alicesmith"
    assert first.email == "alice@example.com"
    assert first.company == "Acme Corp"
    assert first.job_title == "Product Manager"
    assert first.connected_on == date(2026, 10, 15)

    last = result.rows[-1]
    assert last.first_name == "Kenji"
    assert last.last_name == "Sato"
    assert last.company == "Tokyo Robotics"
    assert last.connected_on == date(2022, 8, 1)


def test_parse_malformed_csv_returns_row_numbers_and_reasons() -> None:
    fixture_path = FIXTURES_DIR / "fictional_malformed.csv"
    assert fixture_path.exists(), f"Fixture missing: {fixture_path}"

    with fixture_path.open("r", encoding="utf-8") as f:
        result = parse_connections_csv(f)

    assert result.header_row_number == 4
    # Rows:
    # Row 5: Missing both first and last name
    # Row 6: Invalid date format
    # Row 7: Blank row (skipped)
    # Row 8: Valid row with OnlyFirstName
    assert len(result.errors) == 2

    err1 = result.errors[0]
    assert err1.row_number == 5
    assert "Missing both First Name and Last Name" in err1.message

    err2 = result.errors[1]
    assert err2.row_number == 6
    assert "Unrecognized date format 'invalid-date-string'" in err2.message

    # The one valid row was still parsed
    assert len(result.rows) == 1
    assert result.rows[0].first_name == "OnlyFirstName"
    assert result.rows[0].connected_on == date(2026, 10, 5)


def test_parse_reference_connections_if_present() -> None:
    """Acceptance criteria: the supplied real reference file parses into 20 contact rows."""
    reference_path = REFERENCE_DIR / "Connections.csv"
    if not reference_path.exists():
        return

    with reference_path.open("rb") as f:
        result = parse_connections_csv(f.read())

    assert len(result.errors) == 0, f"Parsing errors on reference data: {result.errors}"
    assert len(result.rows) == 20
    assert result.header_row_number == 4

    names = [r.display_name for r in result.rows]
    assert "Michael Chertushkin" in names
    assert "Julian XIANG" in names
    assert "Rishat Sultanov" in names


def test_parse_bom_and_quoted_commas() -> None:
    csv_text = (
        "\ufeffPreamble text\n\n"
        "First Name,Last Name,URL,Email Address,Company,Position,Connected On\n"
        'Brinnae,"Bent, PhD",https://example.com,,Duke University,'
        '"Director, AI Initiative",06 Sep 2026\n'
    )
    result = parse_connections_csv(csv_text)
    assert len(result.errors) == 0
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.first_name == "Brinnae"
    assert row.last_name == "Bent, PhD"
    assert row.job_title == "Director, AI Initiative"
    assert row.connected_on == date(2026, 9, 6)
