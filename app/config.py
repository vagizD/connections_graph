"""Configuration and feature-tier gating for Inner Circle prototypes.

Supports 3 progressive prototype modes:
  - Mode A: Only contacts operations (list view) + filtering & smart grouping
  - Mode B: Everything from A + task list and follow-ups
  - Mode C (default): Everything from B + interactive planar radial network graph
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

PrototypeMode = Literal["A", "B", "C"]


@dataclass(frozen=True)
class PrototypeTierInfo:
    mode: PrototypeMode
    name: str
    label: str
    description: str
    has_tasks: bool
    has_graph: bool


TIERS: dict[PrototypeMode, PrototypeTierInfo] = {
    "A": PrototypeTierInfo(
        mode="A",
        name="Prototype A",
        label="Contacts & Smart Grouping",
        description=(
            "Core contacts operations (list view), search, faceted filtering, "
            "and AI-conditioned smart grouping."
        ),
        has_tasks=False,
        has_graph=False,
    ),
    "B": PrototypeTierInfo(
        mode="B",
        name="Prototype B",
        label="+ Task List",
        description=(
            "Everything from Prototype A, plus task tracking, actionable follow-ups, "
            "and contact deadlines."
        ),
        has_tasks=True,
        has_graph=False,
    ),
    "C": PrototypeTierInfo(
        mode="C",
        name="Prototype C",
        label="+ Network Graph (Full)",
        description=(
            "Everything from Prototype B, plus interactive planar radial network graph "
            "with real-time physics."
        ),
        has_tasks=True,
        has_graph=True,
    ),
}

_GLOBAL_OVERRIDE_MODE: PrototypeMode | None = None


def get_default_prototype_mode() -> PrototypeMode:
    """Read the default configured prototype mode from environment or global override."""
    if _GLOBAL_OVERRIDE_MODE is not None:
        return _GLOBAL_OVERRIDE_MODE

    raw = os.environ.get("PROTOTYPE_MODE", "C").strip().upper()
    if raw in ("A", "B", "C"):
        return raw  # type: ignore[return-value]
    return "C"


def set_global_prototype_mode(mode: str) -> PrototypeMode:
    """Update global prototype mode at runtime (e.g. from CLI runner)."""
    global _GLOBAL_OVERRIDE_MODE
    cleaned = mode.strip().upper()
    if cleaned in ("A", "B", "C"):
        valid_mode: PrototypeMode = cleaned  # type: ignore[assignment]
        _GLOBAL_OVERRIDE_MODE = valid_mode
        os.environ["PROTOTYPE_MODE"] = valid_mode
        return valid_mode
    _GLOBAL_OVERRIDE_MODE = "C"
    os.environ["PROTOTYPE_MODE"] = "C"
    return "C"


def resolve_prototype_mode(requested_mode: str | None = None) -> PrototypeTierInfo:
    """Resolve prototype tier, allowing optional per-request override (?mode=A)."""
    if requested_mode:
        cleaned = requested_mode.strip().upper()
        if cleaned in ("A", "B", "C"):
            return TIERS[cleaned]  # type: ignore[index]

    default_mode = get_default_prototype_mode()
    return TIERS[default_mode]
