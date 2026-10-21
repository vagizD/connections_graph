"""Domain dataclasses for Inner Circle."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Owner:
    """An application account owning a private contact graph."""

    id: str
    email: str | None
    focal_person_id: str | None
    created_at: str


@dataclass(frozen=True)
class Person:
    """A person node in an owner's contact graph."""

    id: str
    owner_id: str
    first_name: str
    last_name: str
    email: str | None
    profile_url: str | None
    company: str | None
    job_title: str | None
    location: str | None
    education: str | None
    interests: str | None
    created_at: str
    updated_at: str

    @property
    def display_name(self) -> str:
        parts = [self.first_name, self.last_name]
        name = " ".join(p for p in parts if p).strip()
        return name if name else "Unnamed Contact"

    @property
    def interest_list(self) -> list[str]:
        """Return parsed list of distinct interest tags."""
        if not self.interests:
            return []
        return [i.strip() for i in self.interests.split(",") if i.strip()]


@dataclass(frozen=True)
class Relationship:
    """An undirected relationship edge connecting two distinct people owned by the same owner.

    Represents the aggregate total state of the connection between the two people.
    """

    id: str
    owner_id: str
    person_a_id: str
    person_b_id: str
    connected_on: date | None
    notes: str | None
    context_labels: str | None
    shared_activities: str | None
    last_interaction_date: date | None
    summary: str | None
    created_at: str
    updated_at: str

    def other_person_id(self, person_id: str) -> str:
        """Return the other endpoint of this edge given one endpoint."""
        if person_id == self.person_a_id:
            return self.person_b_id
        if person_id == self.person_b_id:
            return self.person_a_id
        raise ValueError(f"Person ID '{person_id}' is not an endpoint of relationship '{self.id}'")

    @property
    def context_label_list(self) -> list[str]:
        """Return parsed list of relationship context labels (e.g. classmate, coworker)."""
        if not self.context_labels:
            return []
        return [lbl.strip() for lbl in self.context_labels.split(",") if lbl.strip()]


@dataclass(frozen=True)
class Interaction:
    """A discrete interaction event in the history sequence of a relationship (event log)."""

    id: str
    owner_id: str
    relationship_id: str
    interaction_date: date
    interaction_type: str  # 'meeting', 'call', 'audio_transcript', 'note', 'email'
    title: str | None
    content: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class ContactView:
    """View model representing a contact connected to the focal person."""

    person: Person
    relationship: Relationship


@dataclass(frozen=True)
class ContactFilterCriteria:
    """Filter parameters for querying contacts and edges."""

    search_query: str | None = None
    company: str | None = None
    location: str | None = None
    education: str | None = None
    interest: str | None = None
    context_label: str | None = None
    last_interaction: str | None = (
        None  # 'recent_30', 'recent_90', 'recent_365', 'older_365', 'unknown'
    )


@dataclass(frozen=True)
class ContactGroup:
    """A group of contacts grouped by an attribute dimension."""

    dimension_value: str
    contacts: list[ContactView]
    count: int


@dataclass(frozen=True)
class FilterGroupResult:
    """Result of multi-criteria filtering and grouping."""

    groups: list[ContactGroup]
    total_unique_contacts: int
    active_filters: dict[str, str]
    group_dimension: str


@dataclass(frozen=True)
class Task:
    """A next-step task or follow-up action item."""

    id: str
    owner_id: str
    title: str
    deadline: date
    status: str = "pending"  # 'pending', 'completed'
    contact_person_id: str | None = None
    relationship_id: str | None = None
    interaction_id: str | None = None
    completed_at: str | None = None
    created_at: str = ""
    updated_at: str = ""
    contact_name: str | None = None
    contact_company: str | None = None

    @property
    def is_completed(self) -> bool:
        return self.status == "completed"

    @property
    def is_overdue(self) -> bool:
        if self.is_completed:
            return False
        return self.deadline < date.today()

    @property
    def is_due_today(self) -> bool:
        if self.is_completed:
            return False
        return self.deadline == date.today()

    @property
    def days_until_deadline(self) -> int:
        return (self.deadline - date.today()).days


@dataclass(frozen=True)
class SplitGroupStats:
    """Statistical summary of an individual group in an AI split."""

    group_name: str
    description: str
    contact_count: int
    percentage_of_network: float
    companies: list[str]
    top_locations: list[str]
    top_interests: list[str]
    active_touchpoint_rate: float
    pending_tasks_count: int
    strategic_insight: str
    color: str


@dataclass(frozen=True)
class ContactSplitAssignment:
    """Individual contact assignment in an AI split."""

    person_id: str
    group_name: str
    rationale: str


@dataclass(frozen=True)
class NetworkSplit:
    """A full AI-driven grouping split with group definitions, assignments, and statistics."""

    id: str
    owner_id: str
    prompt: str
    title: str
    groups: list[SplitGroupStats]
    assignments: dict[str, ContactSplitAssignment]
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GroupedSplitView:
    """View model representing a split group and its associated contacts with rationales."""

    stats: SplitGroupStats
    contacts: list[tuple[ContactView, str]]  # (ContactView, rationale)
