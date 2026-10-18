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
    created_at: str
    updated_at: str

    @property
    def display_name(self) -> str:
        parts = [self.first_name, self.last_name]
        name = " ".join(p for p in parts if p).strip()
        return name if name else "Unnamed Contact"


@dataclass(frozen=True)
class Relationship:
    """An undirected relationship edge connecting two distinct people owned by the same owner."""

    id: str
    owner_id: str
    person_a_id: str
    person_b_id: str
    connected_on: date | None
    notes: str | None
    created_at: str
    updated_at: str

    def other_person_id(self, person_id: str) -> str:
        """Return the other endpoint of this edge given one endpoint."""
        if person_id == self.person_a_id:
            return self.person_b_id
        if person_id == self.person_b_id:
            return self.person_a_id
        raise ValueError(f"Person ID '{person_id}' is not an endpoint of relationship '{self.id}'")


@dataclass(frozen=True)
class ContactView:
    """View model representing a contact connected to the focal person."""

    person: Person
    relationship: Relationship
