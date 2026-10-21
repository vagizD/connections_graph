# Inner Circle — Relationship Manager Prototype

Inner Circle is a personal relationship management system designed to organize, explore, maintain, and expand professional networks. It includes contact directory management, AI-conditioned semantic grouping, actionable task tracking, and an interactive planar radial network graph with real-time physics.

---

## 3 Progressive Prototype Tiers

The application can be booted into any of 3 distinct progressive prototype modes:

| Mode | Name | Key Features |
| :--- | :--- | :--- |
| **A** | **Prototype A: Contacts & Smart Grouping** | • Contact operations (list view, add, edit, delete)<br>• CSV import (LinkedIn export format + synthetic test fixtures)<br>• Search, multi-facet filtering, and dimension grouping<br>• AI-conditioned Smart Grouping with aspect-level cross-attention |
| **B** | **Prototype B: + Task List** | • Everything from Prototype A<br>• Actionable task tracking and deadline management<br>• Dedicated Tasks tab, completion checkboxes, and contact-specific tasks |
| **C** | **Prototype C: + Network Graph (Full)** | • Everything from Prototype B<br>• Interactive Canvas Network Graph with planar radial layout<br>• Real-time spring-Coulomb physics relaxation<br>• Node hover tooltips, click drawer inspector, and cluster sector visualization |

> **Interactive Live Switcher**: You can select the prototype mode at application launch via terminal/Docker, or switch dynamically between **[A | B | C]** at any time using the quick-switch pills in the top-right navbar of the web UI.

---

## Running with Docker (Recommended)

Requires [Docker Desktop](https://www.docker.com/products/docker-desktop/) or Docker Engine. No Python installation or local dependencies required.

### 1. Start the Prototype (One Command)

From the project root:

```bash
docker compose up --build
```

Then open **http://localhost:8000** in your browser. By default, this launches **Prototype C** (the complete prototype).

### 2. Launch Specific Prototype Tiers via Terminal

To start a specific prototype mode directly from your terminal:

```bash
# Prototype A — Contacts Operations + Smart Grouping only:
PROTOTYPE_MODE=A docker compose up

# Prototype B — Contacts + Task List:
PROTOTYPE_MODE=B docker compose up

# Prototype C — Full Experience (Graph + Tasks + Contacts):
PROTOTYPE_MODE=C docker compose up
```

Alternatively, edit the `PROTOTYPE_MODE` variable inside [`docker-compose.yml`](docker-compose.yml):
```yaml
environment:
  - PROTOTYPE_MODE=A   # Choose A, B, or C
```

To stop the container:
```bash
docker compose down
```

---

## Running Locally without Docker (Alternative)

If you prefer running natively with Python on macOS (Apple Silicon / Intel) or Linux:

### 1. Install [`uv`](https://astral.sh/uv) (Package Manager)

- **macOS** (Homebrew):
  ```bash
  brew install uv
  ```
  *or via standalone curl installer:*
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

### 2. Install Dependencies & Launch

```bash
# Install locked dependencies into local .venv:
uv sync

# Run Prototype A (Contacts & Smart Grouping):
python run.py --mode A

# Run Prototype B (Adds Task List):
python run.py --mode B

# Run Prototype C (Full: Graph + Tasks):
python run.py --mode C
```

Options:
- `--host 0.0.0.0` to bind all interfaces
- `--port 8080` to run on a custom port
- `--reload` for auto-reloading during development

Then open **http://127.0.0.1:8000** in your browser.

---

## Quick Testing & Evaluation Guide

Once the web application is running:

1. **Load Test Connections**:
   - On the homepage / Profile tab, click **"Load 100 Connections (Full Network)"** or **"Load 5 Classmates (Focused Test)"** to instantly seed realistic data.
2. **Explore Contacts & Smart Grouping**:
   - Go to the **Contacts** tab.
   - Use the fast search bar (press `/` shortcut) or facet filters on the left.
   - Click **"Smart Grouping"**, enter an open-ended grouping prompt (e.g., *"Group by geographic location"*, *"Group by seniority level"*, or *"Group by industry"*), and click **Run Smart Grouping**.
3. **Inspect Tasks** *(Modes B & C)*:
   - Click the **Tasks** tab to review pending action items, toggle checkboxes to mark completed, or add a custom follow-up.
4. **Explore the Network Graph** *(Mode C)*:
   - Toggle to **Graph View** using the button in the Contacts toolbar.
   - Pan by dragging the canvas, zoom in/out with the mouse wheel or header buttons, hover nodes to highlight connections, and click any node to open the inspector drawer.
   - Toggle **Physics** to observe the spring-relaxation algorithm.

---

## Running Automated Tests

The repository contains an automated test suite (61 tests) covering parsing, clustering, graph generation, services, web endpoints, and prototype tier gating:

```bash
# Run tests locally:
uv run pytest

# Or run tests inside Docker:
docker compose run --rm inner-circle pytest
```

---

## Data Privacy & Safety

- Personal LinkedIn CSV exports in `data/reference/` are strictly ignored by `.gitignore` and `.dockerignore`.
- The repository only tracks synthetic test fixtures in `data/fixtures/` (`duke_classmates_5.csv`, `duke_international_100.csv`, etc.).
- SQLite databases are created locally on demand and are never committed to version control.
