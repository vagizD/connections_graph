#!/usr/bin/env python3
"""CLI Entrypoint for Inner Circle Relationship Manager Prototypes.

Progressive prototype tiers:
    - Mode A: Only contacts operations (list view) + filtering & smart grouping
    - Mode B: Everything from A + task list
    - Mode C (default): Everything from B + interactive planar radial network graph

Usage examples:
    # Run Prototype A (Contacts & Smart Grouping only):
    python run.py --mode A

    # Run Prototype B (Adds Task List):
    python run.py --mode B

    # Run Prototype C (Full: Graph + Tasks + Contacts):
    python run.py --mode C

    # Custom port or auto-reload:
    python run.py --mode A --port 8080 --reload
"""

from __future__ import annotations

import argparse
import os

from app.config import TIERS, set_global_prototype_mode


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inner Circle Relationship Manager Prototype Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Feature Tiers:
  A: Contacts operations (list view) + faceted filtering + AI smart grouping
  B: Prototype A + task list and follow-ups
  C: Prototype B + interactive planar radial network graph with real-time physics (full)
""",
    )
    parser.add_argument(
        "-m",
        "--mode",
        choices=["A", "B", "C", "a", "b", "c"],
        default=os.environ.get("PROTOTYPE_MODE", "C"),
        help="Prototype mode to run (default: C or PROTOTYPE_MODE env var)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("HOST", "0.0.0.0"),
        help="Host address to bind (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", "8000")),
        help="Port to listen on (default: 8000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        default=False,
        help="Enable auto-reload on code changes (development)",
    )

    args = parser.parse_args()

    mode = set_global_prototype_mode(args.mode)
    tier = TIERS[mode]

    print("\n" + "=" * 76)
    print("  INNER CIRCLE — RELATIONSHIP MANAGEMENT PROTOTYPE")
    print(f"  Tier:        {tier.name} — {tier.label}")
    print(f"  Scope:       {tier.description}")
    print(f"  Tasks:       {'[ENABLED]' if tier.has_tasks else '[DISABLED]'}")
    print(f"  Graph View:  {'[ENABLED]' if tier.has_graph else '[DISABLED]'}")
    url_host = "127.0.0.1" if args.host == "0.0.0.0" else args.host
    print(f"  Server URL:  http://{url_host}:{args.port}")
    print("=" * 76 + "\n")

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    main()
