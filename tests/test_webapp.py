"""Integration tests for the web interface endpoints using FastAPI TestClient."""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import get_db
from app.main import DEV_OWNER_ID, app
from app.migrations_runner import apply_migrations
from app.services import ensure_dev_owner

FIXTURES_DIR = Path(__file__).parent.parent / "data" / "fixtures"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    test_db = tmp_path / "test_web.db"
    monkeypatch.setenv("INNER_CIRCLE_DB_PATH", str(test_db))
    apply_migrations(test_db)
    with get_db(test_db) as conn:
        ensure_dev_owner(conn, DEV_OWNER_ID)

    with TestClient(app) as test_client:
        yield test_client


def test_index_empty_state(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "Inner Circle" in response.text
    assert "Your Network is Ready to Grow" in response.text


def test_full_contact_lifecycle_via_web(client: TestClient) -> None:
    # 1. Add contact
    res_create = client.post(
        "/contacts/new",
        data={
            "first_name": "Maya",
            "last_name": "Lin",
            "company": "Transit Dynamics",
            "job_title": "Urban Researcher",
            "location": "Durham, NC",
            "email": "maya@example.com",
            "profile_url": "https://www.linkedin.com/in/mayalin",
            "connected_on": "2026-10-14",
            "notes": "Discussed rapid transit report.",
        },
        follow_redirects=True,
    )
    assert res_create.status_code == 200
    assert "Maya Lin" in res_create.text
    assert "Transit Dynamics" in res_create.text
    assert "Durham, NC" in res_create.text

    # Extract person id from index HTML link
    import re

    match = re.search(r'/contacts/([a-f0-9\-]+)" class="contact-name"', res_create.text)
    assert match is not None
    person_id = match.group(1)

    # 2. View detail page
    res_detail = client.get(f"/contacts/{person_id}")
    assert res_detail.status_code == 200
    assert "Maya Lin" in res_detail.text
    assert "Discussed rapid transit report." in res_detail.text

    # 3. Edit contact
    res_edit = client.post(
        f"/contacts/{person_id}/edit",
        data={
            "first_name": "Maya",
            "last_name": "Lin",
            "company": "Metropolitan Transit Authority",
            "job_title": "Director of Planning",
            "location": "Washington, DC",
            "email": "maya.lin@mta.example.gov",
            "profile_url": "https://www.linkedin.com/in/mayalin",
            "connected_on": "2026-10-14",
            "notes": "Updated: promised to share fellowship details.",
        },
        follow_redirects=True,
    )
    assert res_edit.status_code == 200
    assert "Metropolitan Transit Authority" in res_edit.text
    assert "Washington, DC" in res_edit.text
    assert "promised to share fellowship details" in res_edit.text

    # 4. Disconnect contact
    res_disconnect = client.post(f"/contacts/{person_id}/disconnect", follow_redirects=True)
    assert res_disconnect.status_code == 200
    # In index, Maya Lin is now in disconnected list, not active connected grid
    assert "Disconnected People in Database" in res_disconnect.text
    assert f"btn-reconnect-{person_id}" in res_disconnect.text

    # 5. Reconnect contact
    res_reconnect = client.post(f"/contacts/{person_id}/reconnect", follow_redirects=True)
    assert res_reconnect.status_code == 200
    assert "Connected" in res_reconnect.text

    # 6. Delete contact
    res_delete = client.post(f"/contacts/{person_id}/delete", follow_redirects=True)
    assert res_delete.status_code == 200
    assert "Permanently deleted Maya Lin" in res_delete.text
    # Completely removed from both lists
    assert f"contact-card-{person_id}" not in res_delete.text
    assert f"disconnected-row-{person_id}" not in res_delete.text


def test_import_flow_via_web(client: TestClient) -> None:
    csv_file = FIXTURES_DIR / "fictional_contacts_10.csv"
    with csv_file.open("rb") as f:
        res_preview = client.post(
            "/import/preview",
            files={"file": ("fictional_contacts_10.csv", f, "text/csv")},
        )
    assert res_preview.status_code == 200
    assert "Import Preview & Validation Report" in res_preview.text
    assert "CREATE" in res_preview.text
    assert "Alice Smith" in res_preview.text

    # Extract base64 payload from form
    import re

    match = re.search(r'name="csv_payload" value="([^"]+)"', res_preview.text)
    assert match is not None
    payload = match.group(1)

    # Commit import
    res_commit = client.post(
        "/import/commit",
        data={"csv_payload": payload},
        follow_redirects=True,
    )
    assert res_commit.status_code == 200
    assert "Import successful: 10 created" in res_commit.text
    assert "Alice Smith" in res_commit.text
    assert "Kenji Sato" in res_commit.text
