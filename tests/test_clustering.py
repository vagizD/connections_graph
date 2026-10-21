"""Unit and integration tests for app.clustering (Approach 1 Smart Grouping).

Validates:
- Contact node & edge document serialization.
- Prompt-conditioned dense semantic embeddings and unit hypersphere normalization.
- Dynamic Agglomerative Clustering with Silhouette Score optimization and automatic k selection.
- Medoid identification and cluster representative selection.
- End-to-end execute_smart_grouping pipeline on diverse prompts and edge cases.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from app.clustering import (
    _find_cluster_medoid,
    dynamic_agglomerative_clustering,
    execute_smart_grouping,
    get_contact_embeddings,
    serialize_contact_document,
)


@dataclass
class DummyPerson:
    id: str
    first_name: str
    last_name: str
    job_title: str | None = None
    company: str | None = None
    location: str | None = None
    education: str | None = None
    interests: str | None = None

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    @property
    def interest_list(self) -> list[str]:
        if not self.interests:
            return []
        return [i.strip() for i in self.interests.split(",") if i.strip()]


@dataclass
class DummyRelationship:
    context_labels: str | None = None
    shared_activities: str | None = None
    summary: str | None = None
    notes: str | None = None
    last_interaction_date: date | None = None

    @property
    def context_label_list(self) -> list[str]:
        if not self.context_labels:
            return []
        return [c.strip() for c in self.context_labels.split(",") if c.strip()]


@dataclass
class DummyContact:
    person: DummyPerson
    relationship: DummyRelationship


def _build_dummy_contact(
    id_: str,
    first: str,
    last: str,
    role: str | None = None,
    comp: str | None = None,
    loc: str | None = None,
    edu: str | None = None,
    interests: str | None = None,
    ties: str | None = None,
    summary: str | None = None,
    last_touch: date | None = None,
) -> DummyContact:
    return DummyContact(
        person=DummyPerson(
            id=id_,
            first_name=first,
            last_name=last,
            job_title=role,
            company=comp,
            location=loc,
            education=edu,
            interests=interests,
        ),
        relationship=DummyRelationship(
            context_labels=ties,
            summary=summary,
            last_interaction_date=last_touch,
        ),
    )


def test_serialize_contact_document() -> None:
    c = _build_dummy_contact(
        id_="p-1",
        first="Elena",
        last="Rostova",
        role="Principal AI Researcher",
        comp="DeepMind",
        loc="London, UK",
        edu="Oxford University, PhD Computer Science",
        interests="Reinforcement Learning, Robotics",
        ties="Advisor, Co-Author",
        summary="Deep collaborator on autonomous robotics and model evaluation.",
        last_touch=date(2026, 9, 1),
    )
    doc = serialize_contact_document(c, task_count=3)
    assert "IDENTITY: Elena Rostova" in doc
    assert "JOB TITLE: Principal AI Researcher" in doc
    assert "ORGANIZATION: DeepMind" in doc
    assert "LOCATION: London, UK" in doc
    assert "EDUCATION & ALMA MATER: Oxford University" in doc
    assert "INTERESTS & TOPICS: Reinforcement Learning, Robotics" in doc
    assert "RELATIONSHIP TIES: Advisor, Co-Author" in doc
    assert "RELATIONSHIP SUMMARY: Deep collaborator" in doc
    assert "PENDING ACTION ITEMS: 3" in doc


def test_get_contact_embeddings_normalized() -> None:
    contacts = [
        _build_dummy_contact("1", "Alice", "Founder", "CEO", "TechCo", edu="Stanford"),
        _build_dummy_contact("2", "Bob", "Investor", "Partner", "VC Fund", edu="Harvard"),
        _build_dummy_contact("3", "Charlie", "Student", "MBA", "Duke", edu="Duke Fuqua"),
    ]
    embeds = get_contact_embeddings(contacts, "Founders and Investors", {})
    assert isinstance(embeds, np.ndarray)
    assert embeds.shape[0] == 3
    # Check unit hypersphere normalization (L2 norm == 1.0)
    norms = np.linalg.norm(embeds, axis=1)
    np.testing.assert_allclose(norms, [1.0, 1.0, 1.0], atol=1e-5)


def test_dynamic_agglomerative_clustering_small_sets() -> None:
    # 0 or 1 item
    X0 = np.empty((0, 10))
    k0, labels0, score0 = dynamic_agglomerative_clustering(X0)
    assert k0 == 1
    assert len(labels0) == 0

    X1 = np.ones((1, 10))
    X1 /= np.linalg.norm(X1)
    k1, labels1, score1 = dynamic_agglomerative_clustering(X1)
    assert k1 == 1
    assert list(labels1) == [0]

    # 2 items
    X2 = np.array([[1.0, 0.0], [0.0, 1.0]])
    k2, labels2, score2 = dynamic_agglomerative_clustering(X2)
    assert k2 == 2
    assert len(set(labels2)) == 2


def test_dynamic_agglomerative_clustering_clusters_cohesive_groups() -> None:
    # Create 2 dense clusters in 4D space
    # Cluster A: 3 items near [1, 0, 0, 0]
    cA = np.array(
        [
            [0.98, 0.02, 0.01, 0.0],
            [0.97, 0.03, 0.00, 0.0],
            [0.99, 0.01, 0.02, 0.0],
        ]
    )
    cA /= np.linalg.norm(cA, axis=1, keepdims=True)

    # Cluster B: 3 items near [0, 0, 1, 0]
    cB = np.array(
        [
            [0.01, 0.0, 0.98, 0.02],
            [0.02, 0.0, 0.97, 0.03],
            [0.00, 0.0, 0.99, 0.01],
        ]
    )
    cB /= np.linalg.norm(cB, axis=1, keepdims=True)

    X = np.vstack([cA, cB])
    k, labels, score = dynamic_agglomerative_clustering(X, min_k=2, max_k=4)
    assert k == 2
    assert score > 0.8
    # All of A should share a label, all of B should share a label
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4] == labels[5]
    assert labels[0] != labels[3]


def test_find_cluster_medoid() -> None:
    # Point 0: [0.99, 0.01] (centroid)
    # Point 1: [0.90, 0.10]
    # Point 2: [0.92, -0.05]
    vecs = np.array(
        [
            [0.99, 0.01],
            [0.90, 0.10],
            [0.92, -0.05],
        ]
    )
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    medoid_idx = _find_cluster_medoid([0, 1, 2], vecs)
    assert medoid_idx == 0


def test_execute_smart_grouping_empty_and_single() -> None:
    res_empty = execute_smart_grouping([], "test prompt", {})
    assert res_empty["groups"] == []
    assert res_empty["assignments"] == {}

    single = [_build_dummy_contact("1", "Solo", "Founder", "CEO", "Startup")]
    res_single = execute_smart_grouping(single, "tech split", {})
    assert len(res_single["groups"]) == 1
    assert "1" in res_single["assignments"]


def test_execute_smart_grouping_university_cohorts() -> None:
    contacts = [
        _build_dummy_contact(
            "1", "Rohan", "Gupta", edu="Duke University - Fuqua School of Business", comp="Ventures"
        ),
        _build_dummy_contact(
            "2",
            "Ananya",
            "Patel",
            edu="Duke University - Fuqua School of Business",
            comp="Consulting",
        ),
        _build_dummy_contact(
            "3", "Maya", "Lin", edu="Duke University - Pratt School of Engineering", comp="RoboTech"
        ),
        _build_dummy_contact(
            "4", "David", "Kim", edu="Duke University - Pratt School of Engineering", comp="BioLab"
        ),
        _build_dummy_contact("5", "Alex", "Chen", edu="Stanford University", comp="AI Lab"),
    ]
    res = execute_smart_grouping(contacts, "university grouping", {})
    assert res["title"] == "University Grouping"
    assert len(res["groups"]) >= 2

    group_names = [g["group_name"] for g in res["groups"]]
    # Should identify Fuqua, Pratt, and external institutions
    assert any("Fuqua" in g for g in group_names)
    assert any("Pratt" in g for g in group_names)

    # Check metrics
    total_assigned = sum(g["contact_count"] for g in res["groups"])
    assert total_assigned == 5
    for g in res["groups"]:
        assert g["percentage_of_network"] > 0
        assert g["color"].startswith("#")
        assert len(g["strategic_insight"]) > 0

    # Check assignments
    assert len(res["assignments"]) == 5
    assert "Fuqua" in res["assignments"]["1"]["group_name"]
    assert "Fuqua" in res["assignments"]["2"]["group_name"]
    assert "Pratt" in res["assignments"]["3"]["group_name"]
    assert "Pratt" in res["assignments"]["4"]["group_name"]


def test_execute_smart_grouping_roles_and_investors() -> None:
    contacts = [
        _build_dummy_contact("1", "Sarah", "Connor", role="Founder & CEO", comp="CyberTech"),
        _build_dummy_contact("2", "John", "Connor", role="Co-Founder & CTO", comp="CyberTech"),
        _build_dummy_contact(
            "3", "Victor", "Vance", role="General Partner", comp="Sequoia Capital"
        ),
        _build_dummy_contact(
            "4", "Rachel", "Green", role="Angel Investor", comp="Venture Syndicate"
        ),
        _build_dummy_contact("5", "Monica", "Geller", role="MBA Candidate", edu="Duke Fuqua"),
    ]
    task_counts = {"1": 2, "3": 1}
    res = execute_smart_grouping(contacts, "Founders, Investors, Classmates", task_counts)
    assert len(res["groups"]) >= 2
    assert len(res["assignments"]) == 5

    # Check that pending tasks aggregated correctly across groups
    total_tasks_in_groups = sum(g["pending_tasks_count"] for g in res["groups"])
    assert total_tasks_in_groups == 3

    # Check that names are distinct
    group_names = [g["group_name"] for g in res["groups"]]
    assert len(group_names) == len(set(group_names))


def test_universal_pipeline_group_by_geographic_location() -> None:
    """Ensure 'Group by geographic location' clusters by geography, never by company or degree."""
    contacts = [
        # London cohort (diverse companies and educations)
        _build_dummy_contact(
            "1",
            "Elena",
            "Rostova",
            role="Co-Founder & CEO",
            comp="DeepMind",
            loc="London, UK",
            edu="Oxford University",
        ),
        _build_dummy_contact(
            "2",
            "Arthur",
            "Pendleton",
            role="Senior Consultant",
            comp="McKinsey & Company",
            loc="London, UK",
            edu="BS in Computer Science",
        ),
        # San Francisco cohort (diverse companies and educations)
        _build_dummy_contact(
            "3",
            "Marcus",
            "Sterling",
            role="General Partner",
            comp="Sequoia Capital",
            loc="San Francisco, CA",
            edu="Stanford University",
        ),
        _build_dummy_contact(
            "4",
            "Chloe",
            "Zhao",
            role="Senior Software Engineer",
            comp="Google",
            loc="San Francisco, CA",
            edu="MIT",
        ),
        # Durham cohort
        _build_dummy_contact(
            "5",
            "Devon",
            "Archer",
            role="MBA Candidate",
            comp="Duke University",
            loc="Durham, NC",
            edu="Duke Fuqua School of Business",
        ),
    ]

    res = execute_smart_grouping(contacts, "Group by geographic location", {})
    assert res["title"] == "Geographic Location Grouping"
    assert len(res["groups"]) >= 2

    all_group_names = [g["group_name"] for g in res["groups"]]
    for name in all_group_names:
        # Crucial checks against previous garbage outputs
        assert "aligned network" not in name.lower()
        assert "bs in computer science" not in name.lower()
        assert "mckinsey" not in name.lower()

        # Labels must represent geographic locations
        assert any(
            loc in name
            for loc in (
                "London",
                "San Francisco",
                "Durham",
                "Hub",
                "Network",
            )
        ), f"Group name is not geographic: {name}"

    # Verify Elena and Arthur (both London) are in the SAME cluster
    assert res["assignments"]["1"]["group_name"] == res["assignments"]["2"]["group_name"]
    # Verify Marcus and Chloe (both San Francisco) are in the SAME cluster
    assert res["assignments"]["3"]["group_name"] == res["assignments"]["4"]["group_name"]
    # Verify London and San Francisco are in DIFFERENT clusters
    assert res["assignments"]["1"]["group_name"] != res["assignments"]["3"]["group_name"]


def test_universal_pipeline_group_by_seniority_level() -> None:
    """Ensure 'Group by Seniority level' clusters by experience and labels describe seniority."""
    contacts = [
        _build_dummy_contact("1", "Alice", "Founder", role="Co-Founder & CEO", comp="AI Startup"),
        _build_dummy_contact("2", "Bob", "Exec", role="Managing Partner", comp="Venture Fund"),
        _build_dummy_contact(
            "3", "Carol", "VP", role="Vice President of Engineering", comp="TechCo"
        ),
        _build_dummy_contact("4", "Dave", "Director", role="Director of Product", comp="BigTech"),
        _build_dummy_contact(
            "5", "Eve", "Specialist", role="Senior Software Engineer", comp="Enterprise"
        ),
        _build_dummy_contact("6", "Frank", "Associate", role="Software Engineer", comp="Startup"),
        _build_dummy_contact(
            "7", "Grace", "Student", role="MBA Candidate & Fellow", edu="Duke University"
        ),
    ]
    res = execute_smart_grouping(contacts, "Group by Seniority level", {})
    assert res["title"] == "Seniority & Experience Grouping"
    assert len(res["groups"]) >= 2

    all_group_names = [g["group_name"] for g in res["groups"]]
    for name in all_group_names:
        assert "aligned network" not in name.lower()
        has_experience_marker = any(
            marker in name.lower()
            for marker in (
                "experience",
                "c-suite",
                "executive",
                "leadership",
                "directors",
                "specialists",
                "leads",
                "associates",
                "fellows",
                "students",
                "level",
            )
        )
        assert has_experience_marker, f"Group label does not describe seniority/experience: {name}"

    # Alice (CEO) and Bob (Managing Partner) are Executive/C-Suite
    alice_group = res["assignments"]["1"]["group_name"]
    bob_group = res["assignments"]["2"]["group_name"]
    grace_group = res["assignments"]["7"]["group_name"]

    assert alice_group == bob_group
    # Executive should be separated from student/fellow
    assert alice_group != grace_group


def test_universal_pipeline_group_by_industry() -> None:
    """Ensure 'Group by industry' clusters by domain sector and never outputs 'Aligned Network'."""
    contacts = [
        _build_dummy_contact(
            "1",
            "Ilya",
            "Sutskever",
            role="Chief Scientist",
            comp="OpenAI",
            interests="Artificial Intelligence, Deep Learning",
        ),
        _build_dummy_contact(
            "2",
            "Demis",
            "Hassabis",
            role="CEO",
            comp="Google DeepMind",
            interests="Artificial Intelligence, Neural Networks",
        ),
        _build_dummy_contact(
            "3",
            "Roelof",
            "Botha",
            role="Managing Partner",
            comp="Sequoia Capital",
            interests="Venture Capital, Early-Stage Startups",
        ),
        _build_dummy_contact(
            "4",
            "Doug",
            "Leone",
            role="General Partner",
            comp="Bessemer Venture Partners",
            interests="Venture Capital, Growth Equity",
        ),
    ]
    res = execute_smart_grouping(contacts, "Group by industry", {})
    assert res["title"] == "Industry & Organization Grouping"
    assert len(res["groups"]) >= 2

    all_group_names = [g["group_name"] for g in res["groups"]]
    for name in all_group_names:
        assert "aligned network" not in name.lower()

    # Ilya and Demis should share the same AI industry cohort
    assert res["assignments"]["1"]["group_name"] == res["assignments"]["2"]["group_name"]
    # Roelof and Doug should share the same VC industry cohort
    assert res["assignments"]["3"]["group_name"] == res["assignments"]["4"]["group_name"]
    # AI and VC should be separated
    assert res["assignments"]["1"]["group_name"] != res["assignments"]["3"]["group_name"]


def test_universal_pipeline_prompt_orthogonality() -> None:
    """Verify that different prompts produce orthogonal cuts on identical contacts."""
    contacts = [
        # Elena: Senior in AI, in London, Oxford
        _build_dummy_contact(
            "1",
            "Elena",
            "Rostova",
            role="Co-Founder & CEO",
            comp="DeepMind",
            loc="London, UK",
            edu="Oxford University",
            interests="Artificial Intelligence, Deep Learning",
        ),
        # Toby: Junior in AI, in London, Oxford
        _build_dummy_contact(
            "2",
            "Toby",
            "Vance",
            role="Research Intern",
            comp="DeepMind",
            loc="London, UK",
            edu="Oxford University",
            interests="Artificial Intelligence, Deep Learning",
        ),
        # Marcus: Senior in VC, in San Francisco, Stanford
        _build_dummy_contact(
            "3",
            "Marcus",
            "Sterling",
            role="General Partner",
            comp="Sequoia Capital",
            loc="San Francisco, CA",
            edu="Stanford University",
            interests="Venture Capital, Early-Stage Deals",
        ),
        # Chloe: Junior in VC, in San Francisco, Stanford
        _build_dummy_contact(
            "4",
            "Chloe",
            "Zhao",
            role="Investment Analyst",
            comp="Sequoia Capital",
            loc="San Francisco, CA",
            edu="Stanford University",
            interests="Venture Capital, Early-Stage Deals",
        ),
    ]

    res_loc = execute_smart_grouping(contacts, "Group by geographic location", {})
    res_sen = execute_smart_grouping(contacts, "Group by Seniority level", {})
    res_ind = execute_smart_grouping(contacts, "Group by industry", {})
    res_edu = execute_smart_grouping(contacts, "Group by university", {})

    # 1. Location grouping:
    # Elena & Toby (London) together; Marcus & Chloe (San Francisco) together
    assert res_loc["assignments"]["1"]["group_name"] == res_loc["assignments"]["2"]["group_name"]
    assert res_loc["assignments"]["3"]["group_name"] == res_loc["assignments"]["4"]["group_name"]
    assert res_loc["assignments"]["1"]["group_name"] != res_loc["assignments"]["3"]["group_name"]

    # 2. Seniority grouping:
    # Elena & Marcus (Executives) together; Toby & Chloe separated from Executives
    assert res_sen["assignments"]["1"]["group_name"] == res_sen["assignments"]["3"]["group_name"]
    assert res_sen["assignments"]["1"]["group_name"] != res_sen["assignments"]["2"]["group_name"]
    assert res_sen["assignments"]["1"]["group_name"] != res_sen["assignments"]["4"]["group_name"]

    # 3. Industry grouping:
    # Elena & Toby (AI) together; Marcus & Chloe (VC) together
    assert res_ind["assignments"]["1"]["group_name"] == res_ind["assignments"]["2"]["group_name"]
    assert res_ind["assignments"]["3"]["group_name"] == res_ind["assignments"]["4"]["group_name"]
    assert res_ind["assignments"]["1"]["group_name"] != res_ind["assignments"]["3"]["group_name"]

    # 4. University grouping:
    # Elena & Toby (Oxford) together; Marcus & Chloe (Stanford) together
    assert res_edu["assignments"]["1"]["group_name"] == res_edu["assignments"]["2"]["group_name"]
    assert res_edu["assignments"]["3"]["group_name"] == res_edu["assignments"]["4"]["group_name"]
    assert res_edu["assignments"]["1"]["group_name"] != res_edu["assignments"]["3"]["group_name"]
