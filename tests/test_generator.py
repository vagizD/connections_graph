"""Tests for the synthetic network generator and demo seeding."""

from pathlib import Path

from app.csv_parser import parse_connections_csv
from app.database import get_db
from app.generator import generate_fictional_contacts_csv
from app.services import ensure_dev_owner, list_contacts_connected_to_focal
from seed_demo import seed_database


def test_generator_deterministic() -> None:
    csv1 = generate_fictional_contacts_csv(count=50, seed=123)
    csv2 = generate_fictional_contacts_csv(count=50, seed=123)
    assert csv1 == csv2

    # Different seed yields different stream
    csv3 = generate_fictional_contacts_csv(count=50, seed=999)
    assert csv1 != csv3


def test_generator_parses_into_exact_count() -> None:
    csv_100 = generate_fictional_contacts_csv(count=100, seed=42)
    parsed = parse_connections_csv(csv_100)

    assert len(parsed.errors) == 0
    assert len(parsed.rows) == 100

    # Check variety
    companies = {r.company for r in parsed.rows if r.company}
    assert len(companies) > 5

    urls = [r.profile_url for r in parsed.rows if r.profile_url]
    assert len(urls) > 70  # majority have urls


def test_seed_database_flow(tmp_path: Path) -> None:
    demo_db = tmp_path / "custom_demo.db"
    seed_database(demo_db, count=100, seed=42, reset=True)
    assert demo_db.exists()

    with get_db(demo_db) as conn:
        owner, focal = ensure_dev_owner(conn, owner_id="demo-owner")
        contacts = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        assert len(contacts) == 100

    # Reseeding without reset is idempotent
    seed_database(demo_db, count=100, seed=42, reset=False)
    with get_db(demo_db) as conn:
        contacts2 = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        assert len(contacts2) == 100
