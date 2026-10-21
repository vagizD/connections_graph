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
    assert "Network Ready to Grow" in response.text


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


def test_instant_import_test_table(client: TestClient) -> None:
    # Verify empty state contains instant import button
    res_index = client.get("/")
    assert res_index.status_code == 200
    assert "empty-import-test-btn" in res_index.text

    # Trigger instant import of test table
    res_import = client.post("/import/test-table", follow_redirects=True)
    assert res_import.status_code == 200
    assert "Test table imported: 10 contacts created" in res_import.text
    assert "Alice Smith" in res_import.text
    assert "Kenji Sato" in res_import.text
    assert "Bob Jones" in res_import.text
    assert "btn-quick-import-test" in res_import.text

    # Re-importing same test table is idempotent
    res_reimport = client.post("/import/test-table", follow_redirects=True)
    assert res_reimport.status_code == 200
    assert "0 contacts created" in res_reimport.text
    assert "10 already connected" in res_reimport.text


def test_m2_web_filtering_and_interactions(client: TestClient) -> None:
    # 1. Create enriched contact with M2 fields
    res_create = client.post(
        "/contacts/new",
        data={
            "first_name": "Elena",
            "last_name": "Rostova",
            "company": "AI Horizons",
            "job_title": "Director of ML",
            "location": "London",
            "education": "Duke University",
            "interests": "Robotics, Autonomous Vehicles",
            "context_labels": "Classmate, Collaborator",
            "summary": "Key research partner for AI",
            "notes": "Met at Duke Tech Talk",
        },
        follow_redirects=True,
    )
    assert res_create.status_code == 200
    assert "Elena Rostova" in res_create.text
    assert "Duke University" in res_create.text

    import re

    match = re.search(r'/contacts/([a-f0-9\-]+)" class="contact-name"', res_create.text)
    assert match is not None
    person_id = match.group(1)

    # 2. View detail page and verify enriched fields and timeline
    res_detail = client.get(f"/contacts/{person_id}")
    assert res_detail.status_code == 200
    assert "Robotics" in res_detail.text
    assert "Classmate" in res_detail.text
    assert "Key research partner for AI" in res_detail.text

    # 3. Log an interaction (e.g. meeting / audio transcript)
    res_int = client.post(
        f"/contacts/{person_id}/interactions",
        data={
            "interaction_date": "2026-10-18",
            "interaction_type": "audio_transcript",
            "title": "Strategy Sync Call",
            "content": "Discussed angel syndicate roadmap and prototype demo.",
        },
        follow_redirects=True,
    )
    assert res_int.status_code == 200
    assert "Interaction added" in res_int.text

    # 4. Group by education on index page
    res_grouped = client.get("/?group_by=education")
    assert res_grouped.status_code == 200
    assert "Duke University" in res_grouped.text

    # 5. Filter by interest on index page
    res_filtered = client.get("/?interest=Robotics")
    assert res_filtered.status_code == 200
    assert "Elena Rostova" in res_filtered.text
    assert "Robotics" in res_filtered.text


def test_tabs_and_profile_entrance(client: TestClient) -> None:
    # 1. Entrance tab defaults to Profile
    res_home = client.get("/")
    assert res_home.status_code == 200
    assert "focal-profile-card" in res_home.text
    assert "btn-toggle-edit-profile" in res_home.text
    assert "Connected Contacts" in res_home.text

    # 2. Edit profile facts
    res_edit = client.post(
        "/profile/edit",
        data={
            "first_name": "Vagiz",
            "last_name": "Daudov",
            "job_title": "Founder & Managing Director",
            "company": "Duke Business Fund",
            "location": "Durham, NC",
            "education": "Duke University",
            "interests": "Venture Capital, Artificial Intelligence, Robotics",
            "email": "vagiz@duke.edu",
        },
        follow_redirects=True,
    )
    assert res_edit.status_code == 200
    assert "Profile updated successfully" in res_edit.text
    assert "Managing Director" in res_edit.text
    assert "Duke Business Fund" in res_edit.text
    assert "Venture Capital" in res_edit.text
    assert "Robotics" in res_edit.text

    # 3. Contacts tab has clean header with collapsed filters by default
    res_contacts = client.get("/?tab=contacts")
    assert res_contacts.status_code == 200
    assert 'id="filter-dashboard" style="display: none;"' in res_contacts.text
    assert "btn-toggle-filters" in res_contacts.text
    assert "btn-add-contact" in res_contacts.text
    assert "btn-import-csv" in res_contacts.text
    assert "btn-quick-import-test" in res_contacts.text
    assert "contacts-search-input" in res_contacts.text
    assert "btn-fast-search" in res_contacts.text


def test_conceptual_test_cases_100_and_5_flow(client: TestClient) -> None:
    # 1. Verify import page describes both conceptual test cases
    res_import_page = client.get("/import")
    assert res_import_page.status_code == 200
    assert "btn-import-case-100" in res_import_page.text
    assert "btn-import-case-5" in res_import_page.text
    assert "Case 1" in res_import_page.text
    assert "Case 2" in res_import_page.text

    # 2. Test Case 2: Load 5 classmates
    res_5 = client.post("/import/test-5", follow_redirects=True)
    assert res_5.status_code == 200
    assert "Imported 5 classmates" in res_5.text
    assert "Sophia Ramirez" in res_5.text
    assert "Liam Walker" in res_5.text
    assert "Elena Rostova" in res_5.text

    # 3. Test Reset Network
    res_reset = client.post("/network/reset", follow_redirects=True)
    assert res_reset.status_code == 200
    assert "Network reset" in res_reset.text
    assert "btn-import-test-100" in res_reset.text
    assert "btn-import-test-5" in res_reset.text

    # 4. Test Case 1: Load 100 connections
    res_100 = client.post("/import/test-100", follow_redirects=True)
    assert res_100.status_code == 200
    assert "Imported 100 connections" in res_100.text
    assert "100" in res_100.text

    # Verify contacts directory has contacts and filters
    res_contacts = client.get("/?tab=contacts")
    assert res_contacts.status_code == 200
    assert "Matching: <strong>100</strong>" in res_contacts.text

    # Filter by search term across whole graph
    res_filter_london = client.get("/?tab=contacts&q=London")
    assert res_filter_london.status_code == 200
    assert "London" in res_filter_london.text

    # Filter by Duke context
    res_filter_duke = client.get("/?tab=contacts&q=Duke")
    assert res_filter_duke.status_code == 200
    assert "Duke" in res_filter_duke.text

    # 5. Reset again to leave clean state
    res_final_reset = client.post("/network/reset", follow_redirects=True)
    assert res_final_reset.status_code == 200
    assert "removed 100 contacts" in res_final_reset.text


def test_ai_audio_summarization_workflow(client: TestClient) -> None:
    # 1. Load 5 classmates
    res_import = client.post("/import/test-5", follow_redirects=True)
    assert res_import.status_code == 200

    # 2. Verify test tasks were seeded
    res_tasks = client.get("/?tab=tasks")
    assert res_tasks.status_code == 200
    assert "Review Duke Student Angel Syndicate investment memo draft" in res_tasks.text

    # 3. Find a contact ID from directory
    res_contacts = client.get("/?tab=contacts")
    assert res_contacts.status_code == 200
    assert "Sophia Ramirez" in res_contacts.text

    import re

    match = re.search(r'/contacts/([a-f0-9\-]+)" class="contact-name"', res_contacts.text)
    assert match is not None
    person_id = match.group(1)

    # 3. View detail page: verify unified interaction form and voice recorder button are rendered
    res_detail = client.get(f"/contacts/{person_id}")
    assert res_detail.status_code == 200
    assert "panel-add-interaction" in res_detail.text
    assert "Record Voice Note" in res_detail.text
    assert "btn-voice-record" in res_detail.text

    # 4. Submit conversation notes to AI summarizer
    notes = (
        "Coffee meeting with Sophia Ramirez at Duke Brodhead Center.\n"
        "Discussed Duke Business Fund term sheet and angel syndicate structure.\n"
        "Action item: send revised LP agreement draft.\n"
        "Action item: follow up on venture partner introductions."
    )
    res_ai_text = client.post(
        f"/contacts/{person_id}/interactions/audio",
        data={
            "raw_text": notes,
            "interaction_date": "2026-10-21",
            "auto_update_edge_state": "true",
        },
        follow_redirects=True,
    )
    assert res_ai_text.status_code == 200
    assert "AI conversation summary recorded and relationship state updated" in res_ai_text.text
    assert "Executive Summary:" in res_ai_text.text
    assert "Action Items" in res_ai_text.text
    assert "send revised LP agreement draft" in res_ai_text.text
    assert "2026-10-21" in res_ai_text.text

    # 5. Submit audio file to AI summarizer
    fake_audio = b"\x00\x01\x02\x03" * 256
    res_ai_audio = client.post(
        f"/contacts/{person_id}/interactions/audio",
        data={
            "interaction_date": "2026-10-22",
            "auto_update_edge_state": "true",
        },
        files={
            "audio_file": ("meeting_memo.webm", fake_audio, "audio/webm"),
        },
        follow_redirects=True,
    )
    assert res_ai_audio.status_code == 200
    assert "AI conversation summary recorded" in res_ai_audio.text
    assert "Direct voice sync with" in res_ai_audio.text
    assert "2026-10-22" in res_ai_audio.text

    # Clean up test database
    client.post("/network/reset")


def test_ai_edge_summary_and_interactions_flow(client: TestClient) -> None:
    # 1. Create a new contact
    res_create = client.post(
        "/contacts/new",
        data={
            "first_name": "Jordan",
            "last_name": "Hayes",
            "company": "Pinnacle Ventures",
            "job_title": "Founding Partner",
            "location": "New York, NY",
            "education": "Duke University",
            "interests": "Fintech, Angel Syndicate",
            "context_labels": "Advisor, Alumni",
            "notes": "Met at Duke in NY event",
        },
        follow_redirects=True,
    )
    assert res_create.status_code == 200

    import re

    match = re.search(r'/contacts/([a-f0-9\-]+)" class="contact-name"', res_create.text)
    assert match is not None
    person_id = match.group(1)

    # 2. Inspect detail page: verify summary is read-only (no manual editable input)
    res_detail = client.get(f"/contacts/{person_id}")
    assert res_detail.status_code == 200
    assert 'name="summary"' not in res_detail.text
    assert "Summary" in res_detail.text
    assert "No summary yet" in res_detail.text

    # 3. Add strategic thoughts on how Jordan could be beneficial
    res_thought = client.post(
        f"/contacts/{person_id}/interactions",
        data={
            "interaction_date": "2026-10-10",
            "interaction_type": "thought",
            "title": "Thoughts on Jordan advising our fund",
            "content": (
                "Jordan has deep relationships with East Coast institutional LPs and "
                "angel syndicates. Could be a fantastic mentor for student founders."
            ),
        },
        follow_redirects=True,
    )
    assert res_thought.status_code == 200
    assert "Interaction added" in res_thought.text
    assert "Strategic Thought" in res_thought.text
    # Verify AI Edge Summary updated automatically
    assert "Strategic potential with Jordan Hayes" in res_thought.text
    assert "East Coast institutional LPs" in res_thought.text

    # 4. Add talking to someone who knows the connection
    res_mutual = client.post(
        f"/contacts/{person_id}/interactions",
        data={
            "interaction_date": "2026-10-14",
            "interaction_type": "mutual_connection",
            "title": "Spoke with Sarah regarding Jordan",
            "content": (
                "Sarah confirmed Jordan is enthusiastic about supporting "
                "student entrepreneurs and co-investing."
            ),
        },
        follow_redirects=True,
    )
    assert res_mutual.status_code == 200
    assert "Mutual Connection" in res_mutual.text
    assert "Referral context on Jordan Hayes" in res_mutual.text

    # 5. Add a phone call
    res_call = client.post(
        f"/contacts/{person_id}/interactions",
        data={
            "interaction_date": "2026-10-18",
            "interaction_type": "call",
            "title": "Introductory sync call",
            "content": (
                "Had an encouraging 25-minute call. Jordan agreed to review "
                "3 portfolio companies next month."
            ),
        },
        follow_redirects=True,
    )
    assert res_call.status_code == 200
    assert "Phone Call" in res_call.text
    assert "Direct call with Jordan Hayes" in res_call.text

    # 6. Re-synthesize summary across all 3 interactions
    res_resynth = client.post(
        f"/contacts/{person_id}/summary/resynthesize",
        follow_redirects=True,
    )
    assert res_resynth.status_code == 200
    assert "Summary updated" in res_resynth.text
    assert "3 recorded touchpoint(s)" in res_resynth.text

    # 7. Edit contact details and ensure the AI summary is preserved (not erased)
    res_edit = client.post(
        f"/contacts/{person_id}/edit",
        data={
            "first_name": "Jordan",
            "last_name": "Hayes",
            "company": "Pinnacle Capital Partners",  # updated company
            "job_title": "Founding Partner",
            "location": "New York, NY",
        },
        follow_redirects=True,
    )
    assert res_edit.status_code == 200
    assert "Pinnacle Capital Partners" in res_edit.text
    # Summary still preserved!
    assert "3 recorded touchpoint(s)" in res_edit.text


def test_tasks_tab_and_action_items_lifecycle(client: TestClient) -> None:
    # 1. Access Tasks tab when empty
    res_tasks_empty = client.get("/?tab=tasks")
    assert res_tasks_empty.status_code == 200
    assert "nav-tab-tasks" in res_tasks_empty.text
    assert "All Tasks (Sorted by Deadline)" in res_tasks_empty.text

    # 2. Create contact Anika Bhardwaj
    res_create = client.post(
        "/contacts/new",
        data={
            "first_name": "Anika",
            "last_name": "Bhardwaj",
            "company": "NextGen Bio",
            "job_title": "Co-Founder",
            "location": "Durham, NC",
            "education": "Duke University",
        },
        follow_redirects=True,
    )
    assert res_create.status_code == 200
    import re

    match = re.search(r'/contacts/([a-f0-9\-]+)" class="contact-name"', res_create.text)
    assert match is not None
    person_id = match.group(1)

    # 3. Add interaction with Anika that produces action items
    res_int = client.post(
        f"/contacts/{person_id}/interactions",
        data={
            "interaction_date": "2026-10-20",
            "interaction_type": "meeting",
            "title": "Syndicate Strategy Catchup",
            "content": (
                "Met with Anika at Duke. Discussed our angel syndicate structure.\n"
                "Action item: Send revised LP agreement draft.\n"
                "Action item: Follow up on introduction to venture partner."
            ),
        },
        follow_redirects=True,
    )
    assert res_int.status_code == 200
    assert "Interaction added" in res_int.text

    # 4. Verify Tasks tab has auto-generated tasks with 1-week deadline
    res_tasks = client.get("/?tab=tasks")
    assert res_tasks.status_code == 200
    assert "Send revised LP agreement draft" in res_tasks.text
    assert "Anika Bhardwaj" in res_tasks.text
    # 2026-10-20 + 7 days = 2026-10-27
    assert "Oct 27, 2026" in res_tasks.text or "Oct 27" in res_tasks.text

    # 5. Extract task ID and toggle completion
    task_match = re.search(r"/tasks/([a-f0-9\-]+)/toggle", res_tasks.text)
    assert task_match is not None
    task_id = task_match.group(1)

    res_toggle = client.post(f"/tasks/{task_id}/toggle", follow_redirects=True)
    assert res_toggle.status_code == 200
    assert "Task updated" in res_toggle.text
    assert "Completed" in res_toggle.text

    # 6. Add manual task with earlier deadline to verify sorting
    res_manual = client.post(
        "/tasks/new",
        data={
            "title": "Immediate priority call with Anika",
            "deadline": "2026-10-22",
            "contact_person_id": person_id,
        },
        follow_redirects=True,
    )
    assert res_manual.status_code == 200
    assert "Task added" in res_manual.text
    assert "Immediate priority call with Anika" in res_manual.text

    # 7. Check contact detail page also displays tasks for Anika
    res_detail = client.get(f"/contacts/{person_id}")
    assert res_detail.status_code == 200
    assert "section-contact-tasks" in res_detail.text
    assert "Immediate priority call with Anika" in res_detail.text

    # 8. Delete manual task
    manual_match = re.search(r"/tasks/([a-f0-9\-]+)/delete", res_detail.text)
    assert manual_match is not None
    del_id = manual_match.group(1)

    res_del = client.post(
        f"/tasks/{del_id}/delete",
        data={"redirect_to": f"/contacts/{person_id}"},
        follow_redirects=True,
    )
    assert res_del.status_code == 200
    assert "Task deleted" in res_del.text


def test_network_graph_view_and_api(client: TestClient) -> None:
    """Verify graph view toggle in HTML and JSON API endpoint for planar network graph."""
    # 1. Populate contacts with test table
    res_import = client.post("/import/test-table", follow_redirects=True)
    assert res_import.status_code == 200

    # 2. Test HTML view toggle: List and Graph modes
    res_list = client.get("/?tab=contacts&view=list")
    assert res_list.status_code == 200
    assert "btn-view-list" in res_list.text
    assert "btn-view-graph" in res_list.text
    assert "contacts-graph-container" in res_list.text
    assert "network-graph-canvas" in res_list.text

    res_graph = client.get("/?tab=contacts&view=graph")
    assert res_graph.status_code == 200
    assert "Network View" in res_graph.text

    # 3. Test GET /api/graph endpoint
    res_api = client.get("/api/graph")
    assert res_api.status_code == 200
    data = res_api.json()

    assert "focal_person_id" in data
    assert "nodes" in data
    assert "edges" in data
    assert "clusters" in data
    assert data["total_nodes"] >= 11  # Focal + 10 test contacts

    # Verify focal node at origin
    focal_id = data["focal_person_id"]
    focal_node = next(n for n in data["nodes"] if n["id"] == focal_id)
    assert focal_node["is_focal"] is True
    assert focal_node["x"] == 0.0
    assert focal_node["y"] == 0.0
    assert focal_node["cluster"] == "Center"

    # Verify contact nodes
    contact_nodes = [n for n in data["nodes"] if not n["is_focal"]]
    assert len(contact_nodes) >= 10
    for cn in contact_nodes:
        assert cn["radius"] > 100.0  # Distributed on planar radial orbits
        assert "avatar" in cn
        assert "cluster_color" in cn

    # 4. Test GET /api/graph with filter parameters
    res_filtered = client.get("/api/graph?q=Elena")
    assert res_filtered.status_code == 200
    filtered_data = res_filtered.json()
    # Should only return Focal person and Elena
    matching_names = [n["name"] for n in filtered_data["nodes"] if not n["is_focal"]]
    assert any("Elena" in name for name in matching_names)
    assert len(matching_names) == 1


def test_ai_split_workflow_and_statistics(client: TestClient) -> None:
    """Test creating an AI split, viewing group statistics, and clearing the split."""
    # 1. Populate test network
    res_import = client.post("/import/test-table", follow_redirects=True)
    assert res_import.status_code == 200

    # 2. Initially no split is active
    res_initial = client.get("/api/split")
    assert res_initial.status_code == 200
    assert res_initial.json() == {"active": False, "split": None}

    # 3. Create an AI split with custom prompt
    prompt = "Founders & Executives, Investors, Duke Classmates"
    res_create = client.post(
        "/splits/create",
        data={"prompt": prompt},
        follow_redirects=True,
    )
    assert res_create.status_code == 200

    # Verify active split banner in HTML
    assert "active-split-banner" in res_create.text
    assert "Active Grouping:" in res_create.text
    assert "Founders & Executives" in res_create.text or "Duke Classmates" in res_create.text

    # Verify Group Statistics Card and Metrics
    assert "split-stats-card" in res_create.text
    assert "split-metrics-grid" in res_create.text
    assert "Touchpoint Rate (90d)" in res_create.text
    assert "Open Follow-ups" in res_create.text
    assert "split-rationale-pill" in res_create.text

    # 4. Test GET /api/split JSON endpoint
    res_api = client.get("/api/split")
    assert res_api.status_code == 200
    api_payload = res_api.json()
    assert api_payload["active"] is True
    split_data = api_payload["split"]
    assert split_data is not None
    assert split_data["prompt"] == prompt
    assert len(split_data["groups"]) >= 2
    for g in split_data["groups"]:
        assert "group_name" in g
        assert "contact_count" in g
        assert "percentage_of_network" in g
        assert "active_touchpoint_rate" in g
        assert "strategic_insight" in g

    # 5. Test Graph API returns split clusters and rationales
    res_graph = client.get("/api/graph")
    assert res_graph.status_code == 200
    graph_data = res_graph.json()
    contact_nodes = [n for n in graph_data["nodes"] if not n["is_focal"]]
    assert len(contact_nodes) >= 10
    has_rationale = any(n.get("split_rationale") is not None for n in contact_nodes)
    assert has_rationale is True

    # 6. Clear active split
    res_clear = client.post("/splits/clear", follow_redirects=True)
    assert res_clear.status_code == 200
    assert "active-split-banner" not in res_clear.text

    res_cleared_api = client.get("/api/split")
    assert res_cleared_api.status_code == 200
    assert res_cleared_api.json() == {"active": False, "split": None}
