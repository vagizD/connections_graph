"""Deterministic fictional contact network generator for testing and demo databases."""

from __future__ import annotations

import csv
import io
import random
from datetime import date, timedelta
from pathlib import Path
from typing import TextIO

FIRST_NAMES: list[str] = [
    "Alice",
    "Bob",
    "Carol",
    "David",
    "Elena",
    "Frank",
    "Grace",
    "Hassan",
    "Iris",
    "Kenji",
    "Liam",
    "Maya",
    "Noah",
    "Olivia",
    "Priya",
    "Quinn",
    "Rafael",
    "Sophia",
    "Tariq",
    "Uma",
    "Victor",
    "Wendy",
    "Xavier",
    "Yara",
    "Zack",
    "Alex",
    "Jordan",
    "Taylor",
    "Sam",
    "Morgan",
]

LAST_NAMES: list[str] = [
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
    "Rodriguez",
    "Martinez",
    "Hernandez",
    "Lopez",
    "Gonzalez",
    "Wilson",
    "Anderson",
    "Thomas",
    "Taylor",
    "Moore",
    "Jackson",
    "Martin",
    "Chen",
    "Wong",
    "Sato",
    "Rostova",
    "El-Sayed",
    "Dupont",
    "Kim",
    "Patel",
]

COMPANIES: list[str] = [
    "Duke University",
    "Google",
    "IBM",
    "Acme Corp",
    "Transit Dynamics",
    "InnoTech Solutions",
    "Stanford University",
    "Alternative Data Lab",
    "Global Robotics",
    "Venture Works",
    "Amazon",
    "Third Bridge",
    "Althing Ventures",
]

ROLES: list[str] = [
    "Software Engineer",
    "Lead Robotics Engineer",
    "Product Manager",
    "Research Assistant",
    "Director of Machine Learning",
    "Data Scientist",
    "Associate Partner",
    "Urban Mobility Specialist",
    "Systems Architect",
    "Autonomous Navigation Lead",
    "AI Engineer",
    "Economics Researcher",
]

LOCATIONS: list[str] = [
    "Durham, NC",
    "Raleigh, NC",
    "New York, NY",
    "San Francisco, CA",
    "London, UK",
    "Washington, DC",
    "Tokyo, Japan",
    "Paris, France",
    "Boston, MA",
    "Seattle, WA",
    "Chicago, IL",
]


def generate_fictional_contacts_csv(
    count: int = 100,
    seed: int = 42,
    output_stream: TextIO | None = None,
) -> str:
    """Generate deterministic fictional LinkedIn Connections CSV export.

    Uses a fixed random seed. Includes repeated companies/locations, varied dates,
    missing values, occasional duplicate names, and example-domain emails.
    """
    rng = random.Random(seed)
    buffer = io.StringIO() if output_stream is None else None
    target_stream = buffer if buffer is not None else output_stream
    assert target_stream is not None

    writer = csv.writer(target_stream)

    # Write LinkedIn preamble
    target_stream.write("Notes:\n")
    target_stream.write(
        '"When exporting your connection data, you may notice that some '
        'of the email addresses are missing."\n\n'
    )

    # Header
    writer.writerow(
        ["First Name", "Last Name", "URL", "Email Address", "Company", "Position", "Connected On"]
    )

    base_date = date(2026, 10, 1)

    for i in range(count):
        first = rng.choice(FIRST_NAMES)
        last = rng.choice(LAST_NAMES)

        # 85% have profile URL, 15% missing
        has_url = rng.random() < 0.85
        slug = f"{first.lower()}-{last.lower()}-{i}"
        url = f"https://www.linkedin.com/in/{slug}" if has_url else ""

        # 40% have email, 60% missing (typical LinkedIn export)
        has_email = rng.random() < 0.40
        domain = rng.choice(["example.com", "example.org", "example.edu"])
        email = f"{first.lower()}.{last.lower()}{i}@{domain}" if has_email else ""

        # 90% have company, 10% missing
        company = rng.choice(COMPANIES) if rng.random() < 0.90 else ""

        # 90% have position, 10% missing
        position = rng.choice(ROLES) if rng.random() < 0.90 else ""

        # Connected date: between 30 days and 1500 days ago
        days_ago = rng.randint(1, 1500)
        conn_date = base_date - timedelta(days=days_ago)
        connected_str = conn_date.strftime("%d %b %Y")

        writer.writerow([first, last, url, email, company, position, connected_str])

    if buffer is not None:
        return buffer.getvalue()
    return ""


def generate_duke_classmates_5_csv() -> str:
    """Return the CSV text for the 5-classmates focused test case."""
    path = Path(__file__).resolve().parent.parent / "data" / "fixtures" / "duke_classmates_5.csv"
    if path.exists():
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Fixture not found at {path}")


def generate_duke_international_100_csv() -> str:
    """Return the CSV text for the 100-connection Duke international student test case."""
    path = (
        Path(__file__).resolve().parent.parent / "data" / "fixtures" / "duke_international_100.csv"
    )
    if path.exists():
        return path.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Fixture not found at {path}")
