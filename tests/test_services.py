"""Tests for persistence, graph integrity, and owner isolation in app/services.py."""

from datetime import date
from pathlib import Path

import pytest

from app.ai_summarizer import generate_ai_network_split
from app.database import get_db
from app.migrations_runner import apply_migrations
from app.models import ContactFilterCriteria
from app.services import (
    GraphIntegrityError,
    OwnerIsolationError,
    clear_active_network_split,
    create_contact_with_focal_edge,
    create_interaction,
    create_person,
    create_relationship,
    delete_interaction,
    delete_person,
    delete_relationship,
    ensure_dev_owner,
    filter_and_group_contacts,
    get_active_network_split,
    get_grouped_split_view,
    get_network_graph_payload,
    get_person,
    get_relationship_between,
    list_contacts_connected_to_focal,
    list_disconnected_persons,
    list_interactions_for_relationship,
    save_network_split,
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


def test_node_and_edge_enrichment(disposable_db: Path) -> None:
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn)
        contact, edge = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Elena",
            last_name="Rostova",
            company="AI Horizons",
            job_title="Director of ML",
            location="London",
            education="Duke University",
            interests="Robotics, Computer Vision, AI",
            connected_on=date(2026, 1, 15),
            notes="Initial conference contact",
            context_labels="Classmate, Collaborator",
            shared_activities="Robotics Lab Capstone",
            last_interaction_date=date(2026, 9, 20),
            summary="Key research partner for autonomous systems",
        )

        assert contact.education == "Duke University"
        assert contact.interests == "Robotics, Computer Vision, AI"
        assert contact.interest_list == ["Robotics", "Computer Vision", "AI"]

        assert edge.context_labels == "Classmate, Collaborator"
        assert edge.context_label_list == ["Classmate", "Collaborator"]
        assert edge.shared_activities == "Robotics Lab Capstone"
        assert edge.last_interaction_date == date(2026, 9, 20)
        assert edge.summary == "Key research partner for autonomous systems"

        # Update node facts
        updated_p = update_person(
            conn=conn,
            owner_id=owner.id,
            person_id=contact.id,
            first_name="Elena",
            last_name="Rostova",
            education="Stanford University",
            interests="Venture Capital, Robotics",
        )
        assert updated_p.education == "Stanford University"
        assert updated_p.interest_list == ["Venture Capital", "Robotics"]

        # Update edge total state
        updated_e = update_relationship(
            conn=conn,
            owner_id=owner.id,
            relationship_id=edge.id,
            context_labels="Advisor, Investor",
            shared_activities="Board Meeting 2026",
            last_interaction_date=date(2026, 10, 1),
            summary="Angel syndicate investor",
        )
        assert updated_e.context_label_list == ["Advisor", "Investor"]
        assert updated_e.summary == "Angel syndicate investor"
        assert updated_e.last_interaction_date == date(2026, 10, 1)


def test_interaction_sequence_lifecycle(disposable_db: Path) -> None:
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn)
        contact, edge = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="David",
            last_name="Miller",
            company="Duke University",
        )

        # Log first interaction (coffee meeting)
        int1 = create_interaction(
            conn=conn,
            owner_id=owner.id,
            relationship_id=edge.id,
            interaction_date=date(2026, 8, 10),
            interaction_type="meeting",
            title="Coffee at Duke Perk",
            content="Discussed robotics grant opportunities and fellowship.",
        )
        assert int1.title == "Coffee at Duke Perk"
        assert int1.interaction_type == "meeting"

        # Check edge last_interaction_date was auto-advanced
        refreshed_edge = get_relationship_between(conn, owner.id, focal.id, contact.id)
        assert refreshed_edge is not None
        assert refreshed_edge.last_interaction_date == date(2026, 8, 10)

        # Log a newer audio transcript interaction
        int2 = create_interaction(
            conn=conn,
            owner_id=owner.id,
            relationship_id=edge.id,
            interaction_date=date(2026, 10, 12),
            interaction_type="audio_transcript",
            title="Voice Memo Sync",
            content="Audio transcript: Promised to introduce him to robotics founder in Durham.",
        )
        assert int2.interaction_type == "audio_transcript"

        # Edge last_interaction_date advanced to newer date
        refreshed_edge2 = get_relationship_between(conn, owner.id, focal.id, contact.id)
        assert refreshed_edge2 is not None
        assert refreshed_edge2.last_interaction_date == date(2026, 10, 12)

        # List interactions chronologically descending
        interactions = list_interactions_for_relationship(conn, owner.id, edge.id)
        assert len(interactions) == 2
        assert interactions[0].id == int2.id
        assert interactions[1].id == int1.id

        # Delete an interaction
        deleted = delete_interaction(conn, owner.id, int1.id)
        assert deleted is True
        assert len(list_interactions_for_relationship(conn, owner.id, edge.id)) == 1


def test_filter_and_group_acceptance(disposable_db: Path) -> None:
    """Tests all Milestone 2 acceptance criteria specified in PLAN.md."""
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn)

        # Contact 1: London, Acme Corp, Classmate, Robotics interest, last interaction recent
        c1, _ = create_contact_with_focal_edge(
            conn,
            owner.id,
            focal.id,
            "Alice",
            "Smith",
            company="Acme Corp",
            location="London",
            education="Duke University",
            interests="Robotics, AI",
            context_labels="Classmate, Coworker",
            last_interaction_date=date(2026, 10, 15),
        )

        # Contact 2: London, InnoTech, Classmate, Robotics interest, NO interaction date (unknown)
        c2, _ = create_contact_with_focal_edge(
            conn,
            owner.id,
            focal.id,
            "Bob",
            "Jones",
            company="InnoTech",
            location="London",
            education="MIT",
            interests="Robotics",
            context_labels="Classmate",
            last_interaction_date=None,
        )

        # Contact 3: Durham, Duke University, Advisor, Biotech, last touch > 1 yr ago
        c3, _ = create_contact_with_focal_edge(
            conn,
            owner.id,
            focal.id,
            "Carol",
            "Taylor",
            company="Duke University",
            location="Durham, NC",
            education="Duke University",
            interests="Biotech",
            context_labels="Advisor",
            last_interaction_date=date(2024, 1, 1),
        )

        # Contact 4: London, Acme Corp, Investor, CleanTech, last touch > 1 yr ago
        c4, _ = create_contact_with_focal_edge(
            conn,
            owner.id,
            focal.id,
            "David",
            "Miller",
            company="Acme Corp",
            location="London",
            education=None,
            interests="CleanTech",
            context_labels="Investor",
            last_interaction_date=date(2023, 5, 10),
        )

        # --- Acceptance 1: Filter to London, then group by company ---
        res_london = filter_and_group_contacts(
            conn,
            owner.id,
            focal.id,
            criteria=ContactFilterCriteria(location="London"),
            group_dimension="company",
        )
        assert res_london.total_unique_contacts == 3  # Alice, Bob, David
        comp_groups = {g.dimension_value: g.count for g in res_london.groups}
        assert comp_groups["Acme Corp"] == 2  # Alice and David
        assert comp_groups["InnoTech"] == 1  # Bob

        # --- Acceptance 2: Filter to robotics & classmate, inspect last-interaction recency ---
        res_robotics_classmate = filter_and_group_contacts(
            conn,
            owner.id,
            focal.id,
            criteria=ContactFilterCriteria(interest="Robotics", context_label="Classmate"),
            group_dimension="last_interaction",
        )
        assert res_robotics_classmate.total_unique_contacts == 2  # Alice and Bob
        recency_groups = {
            g.dimension_value: [c.person.first_name for c in g.contacts]
            for g in res_robotics_classmate.groups
        }
        # Bob has unknown last interaction date
        assert "Unknown / No Recorded Interaction" in recency_groups
        assert "Bob" in recency_groups["Unknown / No Recorded Interaction"]
        # Alice has interaction in past 30 days
        assert "Past 30 Days" in recency_groups
        assert "Alice" in recency_groups["Past 30 Days"]

        # --- Acceptance 3: Editing a person's company updates their group membership ---
        update_person(
            conn,
            owner.id,
            c2.id,
            first_name="Bob",
            last_name="Jones",
            company="Acme Corp",
            location="London",
        )
        res_after_edit = filter_and_group_contacts(
            conn,
            owner.id,
            focal.id,
            criteria=ContactFilterCriteria(location="London"),
            group_dimension="company",
        )
        comp_groups_edited = {g.dimension_value: g.count for g in res_after_edit.groups}
        assert comp_groups_edited["Acme Corp"] == 3  # Alice, David, and now Bob!

        # --- Acceptance 4: Clearing filters restores contacts, multi-valued context overlaps ---
        res_all_grouped_context = filter_and_group_contacts(
            conn,
            owner.id,
            focal.id,
            criteria=ContactFilterCriteria(),
            group_dimension="context_label",
        )
        assert res_all_grouped_context.total_unique_contacts == 4  # Total unique people preserved
        context_groups = {g.dimension_value: g.count for g in res_all_grouped_context.groups}
        assert "Classmate" in context_groups
        assert "Coworker" in context_groups
        assert "Advisor" in context_groups
        assert "Investor" in context_groups


def test_get_network_graph_payload_planar_layout(disposable_db: Path) -> None:
    """Verify that network graph payload generates center focal node and planar coordinates."""
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn, "owner-graph-test")

        # Create contacts across two companies and contexts
        c1, _ = create_contact_with_focal_edge(
            conn,
            owner.id,
            focal.id,
            first_name="Elena",
            last_name="Rostova",
            company="DeepMind",
            location="London",
            context_labels="Coworker, AI Researcher",
        )
        c2, _ = create_contact_with_focal_edge(
            conn,
            owner.id,
            focal.id,
            first_name="Kenji",
            last_name="Sato",
            company="DeepMind",
            location="Tokyo",
            context_labels="Coworker",
        )
        c3, _ = create_contact_with_focal_edge(
            conn,
            owner.id,
            focal.id,
            first_name="Sophia",
            last_name="Vance",
            company="Sequoia",
            location="San Francisco",
            context_labels="Investor, Classmate",
        )

        # Cross-contact edge between Elena and Kenji (both at DeepMind)
        create_relationship(
            conn,
            owner.id,
            c1.id,
            c2.id,
            notes="Collaborated on neural networks.",
            context_labels="Colleagues",
        )

        # Query all contacts grouped by company
        filter_res = filter_and_group_contacts(
            conn,
            owner.id,
            focal.id,
            criteria=ContactFilterCriteria(),
            group_dimension="company",
        )

        payload = get_network_graph_payload(conn, owner.id, focal, filter_res)

        # 1. Focal person is center node (x=0, y=0, is_focal=True)
        assert payload["focal_person_id"] == focal.id
        nodes = payload["nodes"]
        assert len(nodes) == 4  # Focal + 3 contacts

        focal_node = next(n for n in nodes if n["id"] == focal.id)
        assert focal_node["is_focal"] is True
        assert focal_node["x"] == 0.0
        assert focal_node["y"] == 0.0
        assert focal_node["cluster"] == "Center"
        assert focal_node["cluster_color"] == "#64748b"

        # 2. Contact nodes have planar radial coordinates
        contact_nodes = [n for n in nodes if not n["is_focal"]]
        assert len(contact_nodes) == 3
        for cn in contact_nodes:
            assert cn["radius"] > 100.0  # Placed radially outward from center
            assert cn["cluster"] in ("DeepMind", "Sequoia")
            assert cn["cluster_color"].startswith("#")

        # 3. Edges include direct focal edges AND cross-contact edge
        edges = payload["edges"]
        assert len(edges) == 4  # 3 focal edges + 1 cross edge (Elena <-> Kenji)

        focal_edges = [e for e in edges if e["is_focal_edge"]]
        assert len(focal_edges) == 3

        cross_edges = [e for e in edges if not e["is_focal_edge"]]
        assert len(cross_edges) == 1
        assert (cross_edges[0]["source"] == c1.id and cross_edges[0]["target"] == c2.id) or (
            cross_edges[0]["source"] == c2.id and cross_edges[0]["target"] == c1.id
        )

        # 4. Clusters metadata
        clusters = payload["clusters"]
        assert len(clusters) == 2
        cluster_names = {cl["name"] for cl in clusters}
        assert "DeepMind" in cluster_names
        assert "Sequoia" in cluster_names


def test_generate_ai_network_split_and_persistence(disposable_db: Path) -> None:
    """Test AI split generation, group statistics, active split persistence and cleanup."""
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn, owner_id="dev-owner")

        # Create diverse contacts
        c1, _ = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Alice",
            last_name="Founder",
            job_title="Founder & CEO",
            company="NexGen AI",
            location="San Francisco, CA",
        )
        c2, _ = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Bob",
            last_name="Investor",
            job_title="Partner",
            company="Sequoia Capital",
            location="Menlo Park, CA",
        )
        c3, _ = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Charlie",
            last_name="Duke",
            job_title="MBA Candidate",
            education="Duke University - Fuqua School of Business",
            location="Durham, NC",
        )

        contacts = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        assert len(contacts) == 3

        # 1. Generate split
        split_payload = generate_ai_network_split(
            contacts=contacts,
            split_prompt="Founders, Investors, Duke Classmates",
            task_counts={},
        )

        assert split_payload["prompt"] == "Founders, Investors, Duke Classmates"
        assert len(split_payload["groups"]) >= 2
        assert len(split_payload["assignments"]) == 3

        # Check each assignment has person_id, group_name, and rationale
        for pid in (c1.id, c2.id, c3.id):
            assert pid in split_payload["assignments"]
            assign = split_payload["assignments"][pid]
            assert assign["group_name"]
            assert len(assign["rationale"]) > 0

        # Check group statistics
        total_assigned_count = sum(g["contact_count"] for g in split_payload["groups"])
        assert total_assigned_count == 3
        for g in split_payload["groups"]:
            assert g["percentage_of_network"] >= 0.0
            assert g["color"].startswith("#")
            assert len(g["strategic_insight"]) > 0

        # 2. Persistence: Save as active split
        split = save_network_split(
            conn=conn,
            owner_id=owner.id,
            prompt="Founders, Investors, Duke Classmates",
            split_payload=split_payload,
        )

        active = get_active_network_split(conn, owner.id)
        assert active is not None
        assert active.id == split.id
        assert active.prompt == split.prompt
        assert len(active.groups) == len(split.groups)

        # 3. Grouped view for presentation
        grouped_view = get_grouped_split_view(active, contacts)
        assert len(grouped_view) == len(active.groups)
        total_view_contacts = sum(len(gv.contacts) for gv in grouped_view)
        assert total_view_contacts == 3

        # 4. Clear active split
        clear_active_network_split(conn, owner.id)
        assert get_active_network_split(conn, owner.id) is None


def test_network_graph_payload_with_active_split(disposable_db: Path) -> None:
    """Verify graph payload seamlessly utilizes active AI split groups, colors, and rationales."""
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn, owner_id="dev-owner")
        c1, _ = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Elena",
            last_name="Rostova",
            job_title="Research Scientist",
            company="DeepMind",
        )
        c2, _ = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Kenji",
            last_name="Sato",
            job_title="Managing Partner",
            company="Sequoia",
        )

        contacts = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        split_payload = generate_ai_network_split(
            contacts=contacts,
            split_prompt="Founders & Scientists, Investors",
            task_counts={},
        )
        split = save_network_split(
            conn=conn,
            owner_id=owner.id,
            prompt="Founders & Scientists, Investors",
            split_payload=split_payload,
        )

        filter_res = filter_and_group_contacts(
            conn, owner.id, focal.id, criteria=ContactFilterCriteria(), group_dimension="none"
        )
        payload = get_network_graph_payload(conn, owner.id, focal, filter_res, active_split=split)

        nodes = payload["nodes"]
        contact_nodes = [n for n in nodes if not n["is_focal"]]
        assert len(contact_nodes) == 2

        # Nodes should have split_rationale and split cluster names
        for cn in contact_nodes:
            assert cn["split_rationale"] is not None
            assert len(cn["split_rationale"]) > 0
            assert cn["cluster"] in {g.group_name for g in split.groups}


def test_university_grouping_split(disposable_db: Path) -> None:
    """Verify that 'university grouping' organizes contacts by school with accurate rationales."""
    with get_db(disposable_db) as conn:
        owner, focal = ensure_dev_owner(conn, owner_id="owner-uni-test")
        c1, _ = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Rohan",
            last_name="Gupta",
            education="Duke University - Fuqua School of Business",
            company="NextGen Ventures",
        )
        c2, _ = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Maya",
            last_name="Lin",
            education="Duke University - Pratt School of Engineering",
            company="RoboTech",
        )
        c3, _ = create_contact_with_focal_edge(
            conn=conn,
            owner_id=owner.id,
            focal_person_id=focal.id,
            first_name="Alex",
            last_name="Chen",
            education="Stanford University",
            company="DeepMind",
        )

        contacts = list_contacts_connected_to_focal(conn, owner.id, focal.id)
        split_payload = generate_ai_network_split(
            contacts=contacts,
            split_prompt="university grouping",
            task_counts={},
        )
        group_names = [g["group_name"] for g in split_payload["groups"]]
        assert any("Fuqua" in name for name in group_names)
        assert any("Pratt" in name for name in group_names)

        assignments = split_payload["assignments"]
        rohan_assign = assignments[c1.id]
        assert "Fuqua" in rohan_assign["group_name"]
        assert "Fuqua" in rohan_assign["rationale"]

        maya_assign = assignments[c2.id]
        assert "Pratt" in maya_assign["group_name"]
        assert "Pratt" in maya_assign["rationale"]

        alex_assign = assignments[c3.id]
        assert (
            "Other Universities" in alex_assign["group_name"]
            or "Stanford" in alex_assign["group_name"]
        )
