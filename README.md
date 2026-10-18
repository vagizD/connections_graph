# Inner Circle — Prototype

Inner Circle is a personal relationship management prototype designed to help users organize, explore, maintain, and expand their professional network.

## Prerequisites & Environment

- **OS**: Linux
- **Python**: 3.12 (pinned to `3.12.3` in `.python-version`)
- **Package Manager**: [uv](https://astral.sh/uv) (version `0.13.1`)

## Setup

```bash
# Ensure uv is installed and in your PATH:
export PATH="$HOME/.local/bin:$PATH"

# Install locked dependencies into local .venv
uv sync --locked
```

## Running the Application

```bash
# Start the local development web server
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
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
