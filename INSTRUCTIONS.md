# Coding agent rules

Read this file and PLAN.md before working. This pair supersedes the earlier single-file instructions.md handoff. INSTRUCTIONS.md defines durable engineering rules; PLAN.md defines product behavior, sequencing, and acceptance criteria. The user's subsequent instructions take precedence.

## Work incrementally

- Inspect repository instructions, existing code, database, migrations, and tests before changing anything. Some contact functionality already exists; reuse it where practical.
- Explain the next small, user-visible outcome before implementing it. Keep changes focused and reviewable. Do not scaffold the whole roadmap in one pass.
- Work through the active milestone's steps without asking for approval for routine reversible edits. Stop at the milestone boundary with a working demonstration and a completion report. Do not implement later milestones unless requested.
- Keep PLAN.md status accurate. Mark a step complete only after verifying its acceptance criteria. Record blockers and deviations explicitly.
- Preserve existing code, data, and unrelated changes. Back up the development database before schema migrations. Never silently reset it.
- Choose concrete implementations first. Introduce interfaces, base classes, or generic frameworks only when actual multiple implementations justify them.
- Do not implement speculative performance optimizations, microservices, a graph database, or AI infrastructure.

## Reproducible environment

- Use uv as the single Python dependency/environment manager. If another manager already exists, migrate deliberately while preserving dependency choices; do not maintain competing authoritative lockfiles.
- Pin an exact compatible Python patch version in .python-version. Set the supported minor range in pyproject.toml. Record the uv version used in the README and any CI setup.
- Commit pyproject.toml, uv.lock, and .python-version. Include development tools in locked dependencies.
- Use `uv sync --locked` for setup and `uv run --locked ...` for routine execution and verification. Change dependencies intentionally and update the lockfile with the manifest.
- Preserve a suitable existing frontend and its package manager. If JavaScript/TypeScript is present, pin its runtime/package-manager versions, commit its lockfile, and use reproducible installation commands.
- Do not select framework versions from memory. Check compatibility when initializing or upgrading the actual project.

## Code quality

- All owned Python code, including tests, must be typed. Use mypy strict mode, explicit parameter/return types, and typed data structures at boundaries.
- Avoid propagating Any or unstructured dictionaries through application code. Isolate untyped third-party boundaries. Narrow exceptions require an explanation; blanket ignores are prohibited.
- Type checking does not replace runtime validation. Validate CSV input, HTTP payloads, dates, identifiers, and graph invariants.
- Use Ruff for linting/formatting and pytest for tests. Preserve compatible existing tooling where changing it provides no benefit.
- Use TypeScript strict mode if the project has a TypeScript frontend.
- Follow clear PEP 8 / Google-style naming and documentation. Explain non-obvious decisions, not every line of code.
- Keep domain operations separate from CSV parsing and UI handlers, but avoid unnecessary abstraction layers. UI and imports must use the same mutation rules.

## Architecture and data rules

- Build a web application that can run locally on Linux. Preserve the current viable stack. If no usable stack exists, start with a small Python web app, server-rendered pages, and SQLite; do not add a separate frontend merely for this milestone.
- Use a relational database and migrations. A graph domain model does not require a graph database. The future graph UI and current list UI must read the same data.
- An application account is distinct from a Person. Contacts do not need accounts.
- Give each owner's private graph one focal Person representing the owner. Scope records and queries to that owner. A shared database must not imply shared contact visibility.
- Person nodes hold facts about people. Relationship edges hold facts about relationships. Future interaction records reference relationships.
- Missing data is unknown. Do not invent locations, education, employment history, last-interaction dates, or relationship strength.
- Record creation time is not connection time. Connection time is not last-interaction time.
- For now, all information comes from user-imported files or user edits. LinkedIn profile URLs are optional references, not live integrations.
- Names are not unique identifiers. Do not merge people by name or merge private records across owners.
- Validate ownership, endpoints, uniqueness, and deletion behavior in application code and database constraints where practical.

## Local operation and data protection

- Keep real CSV exports, local databases, backups, recordings, credentials, and .venv out of Git. Commit only fictional fixtures and configuration examples.
- Use existing authentication if available. Otherwise use one fixed server-side development owner, bind to localhost, and document that external deployment requires authentication. Never trust arbitrary owner IDs from the browser.
- Keep development, demo, and test databases separate. Automated tests and seed/reset commands must never reset the user's real data.
- Do not upload contacts or notes to an AI provider or external enrichment service as an incidental implementation choice. Those integrations are outside the active milestone.

## Verification and reporting

- Test meaningful behavior: import correctness, repeat-import behavior, graph integrity, ownership isolation, mutations, and persistence.
- Database integration tests must run the actual schema/migrations and business logic against a disposable database, rather than mocking persistence.
- Add focused tests alongside features. Do not write tests that merely repeat implementation details or expand testing after the relevant risk is resolved.
- Run linting, formatting checks, strict typing, and relevant tests. Typical commands are `uv run --locked ruff check .`, `uv run --locked ruff format --check .`, `uv run --locked mypy .`, and `uv run --locked pytest`; adapt paths/configuration to the repository.
- Perform a browser smoke test for each completed UI workflow. Report anything not executed and why.
- Maintain a short README with exact Linux setup, migration, startup, import, demo-data, and verification commands.
- Report: behavior delivered, important changes, verification evidence, known limitations, and the next proposed step. Do not claim unimplemented behavior or unrun tests.
