# Inner Circle — Prototype

Inner Circle is a personal relationship management prototype designed to help users organize, explore, maintain, and expand their professional network.

## Prerequisites & Environment

- **OS**: macOS (Apple Silicon M1/M2/M3/M4 or Intel) or Linux
- **Python**: 3.12 (managed automatically via `uv` or system Python 3.12)
- **Package Manager**: [uv](https://astral.sh/uv) (fast Python package and project manager)

### Installing `uv`

- **macOS** (Homebrew):
  ```bash
  brew install uv
  ```
  *or via standalone installer:*
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```
- **Linux**:
  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

## Setup & First Run (Mac / Linux)

```bash
# 1. Install dependencies into local .venv
uv sync

# 2. Start the local web application
uv run uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open http://127.0.0.1:8000 in your browser.

## Running Tests & Verifications

```bash
# Run Ruff linting and formatting checks
uv run --locked ruff check .
uv run --locked ruff format --check .

# Run strict type checking with mypy
uv run --locked mypy .

# Run automated tests with pytest
uv run --locked pytest
```

## Data & Safety

- Personal LinkedIn exports remain private in `data/reference/` (ignored by Git).
- Test fixtures and synthetic data reside in `data/fixtures/`.
- Tests run against isolated, disposable SQLite databases and never mutate development or personal data.
