"""Tests for the 3 progressive prototype modes (A, B, C)."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import (
    get_default_prototype_mode,
    resolve_prototype_mode,
)
from app.database import get_db
from app.main import DEV_OWNER_ID, app
from app.migrations_runner import apply_migrations
from app.services import ensure_dev_owner


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    test_db = tmp_path / "test_modes.db"
    monkeypatch.setenv("INNER_CIRCLE_DB_PATH", str(test_db))
    apply_migrations(test_db)
    with get_db(test_db) as conn:
        ensure_dev_owner(conn, DEV_OWNER_ID)

    with TestClient(app) as test_client:
        yield test_client


def test_prototype_tiers_configuration() -> None:
    tier_a = resolve_prototype_mode("A")
    assert tier_a.mode == "A"
    assert tier_a.has_tasks is False
    assert tier_a.has_graph is False

    tier_b = resolve_prototype_mode("B")
    assert tier_b.mode == "B"
    assert tier_b.has_tasks is True
    assert tier_b.has_graph is False

    tier_c = resolve_prototype_mode("C")
    assert tier_c.mode == "C"
    assert tier_c.has_tasks is True
    assert tier_c.has_graph is True


def test_prototype_mode_a_features_gating(client: TestClient) -> None:
    # Load contacts tab under Mode A
    response = client.get("/?mode=A&tab=contacts")
    assert response.status_code == 200
    html = response.text

    # Mode A must have contacts list and smart grouping
    assert "Contacts Directory" in html
    assert "btn-toggle-split" in html  # Smart Grouping button
    assert "active-tier-badge" in html
    assert "Prototype A" in html

    # Mode A must NOT have tasks tab
    assert 'id="nav-tab-tasks"' not in html

    # Mode A must NOT have graph view switcher or canvas container
    assert 'id="btn-view-graph"' not in html
    assert 'id="contacts-graph-container"' not in html


def test_prototype_mode_b_features_gating(client: TestClient) -> None:
    # Load index under Mode B
    response = client.get("/?mode=B&tab=contacts")
    assert response.status_code == 200
    html = response.text

    # Mode B must have contacts list and smart grouping
    assert "Contacts Directory" in html
    assert "btn-toggle-split" in html
    assert "Prototype B" in html

    # Mode B MUST have tasks tab
    assert 'id="nav-tab-tasks"' in html

    # Mode B must NOT have graph view switcher or canvas container
    assert 'id="btn-view-graph"' not in html
    assert 'id="contacts-graph-container"' not in html


def test_prototype_mode_c_features_gating(client: TestClient) -> None:
    # Load index under Mode C
    response = client.get("/?mode=C&tab=contacts")
    assert response.status_code == 200
    html = response.text

    # Mode C (full) must have everything
    assert "Contacts Directory" in html
    assert "btn-toggle-split" in html
    assert "Prototype C" in html

    # Mode C MUST have tasks tab
    assert 'id="nav-tab-tasks"' in html

    # Mode C MUST have graph view switcher and canvas container
    assert 'id="btn-view-graph"' in html
    assert 'id="contacts-graph-container"' in html


def test_set_mode_endpoint(client: TestClient) -> None:
    # Switch to mode A
    res_a = client.get("/set-mode/A", follow_redirects=False)
    assert res_a.status_code == 303
    assert get_default_prototype_mode() == "A"

    # Switch back to mode C
    res_c = client.get("/set-mode/C", follow_redirects=False)
    assert res_c.status_code == 303
    assert get_default_prototype_mode() == "C"
