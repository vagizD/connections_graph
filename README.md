# Inner Circle — Relationship Manager Prototype

Inner Circle is a personal relationship management system designed to organize, explore, maintain, and expand professional networks.

---

## Prototypes

The application supports 3 progressive prototype modes:

| Mode | Name | Features |
| :--- | :--- | :--- |
| **A** | **Prototype A** | Contact operations (list view, add, edit, delete) + faceted search & filtering + AI-conditioned Smart Grouping |
| **B** | **Prototype B** | Everything from **A** + Task list & deadline follow-ups |
| **C** | **Prototype C (Full)** | Everything from **B** + Interactive Canvas Network Graph (planar radial layout + physics) |

*(Note: You can also switch between modes live in the app using the `[A | B | C]` toggle in the top-right navbar).*

---

## Quick Launch (Docker)

### 1. Install Docker
Download and install [Docker Desktop](https://www.docker.com/products/docker-desktop/) (macOS, Windows, or Linux).

### 2. Clone the Repository
```bash
git clone git@github.com:vagizD/connections_graph.git
# or: git clone https://github.com/vagizD/connections_graph.git
```

### 3. Navigate into Directory
```bash
cd connections_graph
```

### 4. Launch Application

```bash
# Launch Prototype C (Full version, default):
docker compose up --build

# Or launch a specific prototype tier:
PROTOTYPE_MODE=A docker compose up --build   # Mode A (Contacts + Grouping)
PROTOTYPE_MODE=B docker compose up --build   # Mode B (+ Tasks)
PROTOTYPE_MODE=C docker compose up --build   # Mode C (+ Graph)
```

Open **http://localhost:8000** in your browser.
