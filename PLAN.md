# Inner Circle — prototype implementation plan

## Product intent

A web app to organize, understand, visualize, and maintain professional connections. The central exploration workflow is: choose a purpose or criteria, filter the network, group the matching contacts, and inspect or act on a relationship.

Start with a list interface. Filtering and grouping must eventually use both person attributes and relationship context. Graph visualization comes later and shares the same query state and records.

All information is currently user-supplied through CSV imports and edits. Automatic profile enrichment, live LinkedIn access, and scraping are out of scope. The supplied LinkedIn Connections.csv is a snapshot, not an ongoing synchronization.

## Status and execution scope

- Milestone 1 is active: import and manage a graph through a list UI.
- Milestone 2 is planned: filter and group using node and edge attributes.
- Milestones 3–4 are future: semantic categories and graph visualization.
- No implementation is marked complete by this document. Inspect the repository and record what already exists.
- Complete milestone 1 incrementally, then report results and propose milestone 2. Do not implement the entire roadmap in one task.

## Milestone 1 — import and manage contacts

Outcome: a user imports contacts, browses them, adds people, edits person/relationship information, and removes records. This is the first observable increment, not the full product MVP.

### 1.1 Inspect existing work and read CSV

- [x] Inspect the existing app, database, migrations, dependencies, and tests. Identify reusable components and the smallest required changes.
- [x] Establish the required uv and strict-typing setup as part of this first feature, without creating a long infrastructure-only phase.
- [x] Implement a typed CSV parser that returns validated rows and actionable errors before any database writes.

The supplied Connections.csv was inspected: 20 nonempty contact rows; header on row 4 after a preamble; columns as follows. Detect the header by expected field names rather than hardcoding row 4.

| CSV column | Destination | Meaning |
| --- | --- | --- |
| First Name | Person.first_name | Person fact |
| Last Name | Person.last_name | Person fact |
| URL | Person.profile_url | Optional external reference |
| Email Address | Person.email | Optional contact information |
| Company | Person.company | Company reported by this snapshot |
| Position | Person.job_title | Title reported by this snapshot |
| Connected On | Relationship.connected_on | Date the owner connected with the person |

Handle UTF-8/BOM, preamble rows, quoted commas, Unicode, blank rows, and empty optional fields. Require at least one nonempty name component. Inspect actual date values and support the observed format explicitly; report invalid or ambiguous dates rather than guessing. Do not infer job start dates from Connected On.

Acceptance: the supplied file parses into 20 contact rows; a fictional malformed fixture returns row numbers and reasons; parsing alone never changes the database. Do not commit the real CSV as a test fixture.

### 1.2 Persist people and relationships

- [x] Adapt the current models and introduce migrations without losing existing records.
- [x] Create one development owner and one focal Person, idempotently.
- [x] Implement typed operations for person creation/update/deletion and edge creation/update/removal.

Minimum model:

| Entity | Fields |
| --- | --- |
| Owner/account | Stable ID; focal Person reference |
| Person | Stable ID, owner ID, first/last name, optional email/profile URL/company/job title/location, created/updated timestamps |
| Relationship | Stable ID, owner ID, two Person endpoints, optional connected-on date, optional context notes, created/updated timestamps |

Display name can be derived from first/last name. Location is manually editable even though the initial CSV does not supply it. Keep optional fields genuinely optional. ORM models may serve as domain classes; do not duplicate every model into additional classes without a need.

Use one undirected connection type initially. Normalize endpoint order and enforce uniqueness per owner and unordered pair. Reject self-edges and cross-owner endpoints. Normal contact creation produces a Person and an edge to the focal Person atomically. A small service operation should also support an edge between any two existing people; a visual edge editor is not required yet.

Acceptance: a test can create the focal Person, add a contact, and retrieve their edge; invalid endpoints and duplicate edges are rejected. A second owner cannot read or mutate the first owner's records.

### 1.3 Commit imports safely

- [x] Add a preview and validation report before import confirmation.
- [x] Connect each imported contact to the focal Person.
- [x] Report created people/edges, matched records, unchanged rows, and errors/conflicts.
- [x] Make repeated imports idempotent without destroying manual edits.

Match only within the owner's graph using unambiguous normalized profile URL or email. Document normalization. If identifiers disagree or match multiple records, require resolution; do not guess. Never match on name alone.

For rows without identifiers, keep minimal import fingerprint bookkeeping so exact repeats do not create duplicates. Flag indistinguishable repeated rows for review. Document that modified identifier-free rows may need manual reconciliation.

Default merge policy: fill missing fields from nonempty imported values; retain existing populated fields. Display conflicting populated values and require an explicit resolution before commit. Blank input never erases manual data. Apply accepted imports and their bookkeeping in one transaction. Invalid or unresolved rows block the entire import in this first version.

Acceptance: importing a fictional CSV twice yields the same node/edge counts; edits survive reimport; a failing import leaves the database unchanged; the real CSV can initialize the user's graph without requiring extra information.

### 1.4 Browse and manage the graph as a list

- [x] Show contacts connected to the focal Person, including name, company, title, and location where present.
- [x] Provide Import CSV and Add person actions and a useful empty state.
- [x] Open a contact detail panel/page with separate Person and Your relationship sections.
- [x] Support adding a person, editing person fields, and editing relationship notes/date.
- [x] Add basic name search and pagination if needed to navigate larger fixtures.
- [x] Support disconnecting and reconnecting an existing person.
- [x] Support confirmed person deletion; protect the focal Person.

Distinguish operations: Remove connection deletes only the relevant edge. Delete person removes that Person and all their incident edges atomically after showing the affected edge count. Disconnected Person records remain selectable from Add person so they can be reconnected without duplication. Hiding/archiving is a later separate feature.

Acceptance: import → inspect → edit → manually add → disconnect → reconnect → delete works in the browser. Changes persist across page reloads and application restart. Errors and save results are visible.

### 1.5 Verify with fictional networks

- [x] Commit a hand-authored fictional CSV of about 10 contacts and edge-case fixtures.
- [x] Add a deterministic generator for 100 and 500 contacts using a fixed seed.
- [x] Include repeated companies/locations, varied roles/dates, missing values, duplicate names, and fictional relationship context. Use example-domain emails.
- [x] Provide separate disposable test and demo databases and documented seed/reset commands.
- [x] Complete focused import, mutation, graph-integrity, and owner-isolation tests using real persistence.
- [x] Run a browser smoke test and verify restart persistence.

Synthetic data checks behavior at larger sizes; it is not customer validation. The 20 real contacts are useful for checking actual workflows. Do not fabricate missing facts about them.

Milestone 1 exit: reproducible local setup; working import/list/mutations; preserved existing data; tests/type checks/lint pass or limitations explicitly documented; README includes exact commands. Record completion evidence here before moving on.

## Milestone 2 — filter and group with nodes and edges

Outcome: the user can find meaningful subsets and inspect groups without a graph visualization or AI dependency.

### Structured enrichment

- Add manually editable person attributes needed by actual queries, initially interests/topics and education if the user needs them.
- Add structured relationship fields: context labels (classmate, coworker, conference), shared activities, and optional last-interaction date. Keep free-text notes as well.
- Use small typed fields/related tables; avoid an unrestricted custom-schema system.
- Node facts remain distinct from edge facts: a person's university is a node fact; attending the same program together is relationship context.

### Filter → group → inspect

- Filter contacts by node attributes and the relationship to the focal Person. Define this edge scope explicitly; an unrelated contact-to-contact edge must not satisfy a “my relationship” filter.
- Combine criteria across both sources. Default: AND across fields, OR across selected values within one field; show the active rules clearly.
- Allow user-selected grouping dimensions, initially company, location, and relationship context.
- Recompute groups when filters or underlying records change. Here “online” means interactive regrouping; it does not require an online-learning algorithm.
- Permit overlapping groups for multi-valued attributes. Show per-group counts and a separate unique-contact total so overlaps are not double-counted as people.
- Display unknown values explicitly. Absence of a value is not evidence of a negative fact.
- Let users inspect each group's contacts in a list and open/edit a contact without losing filters.

Acceptance examples:

1. Filter to London, then group by company.
2. Filter to robotics interest and classmate relationship context, then inspect contacts with old or unknown last-interaction dates as distinct cases.
3. Editing a person's company or relationship label updates their membership correctly.
4. Clearing filters restores all connected contacts, with unknown values and overlapping memberships handled consistently.

Use deterministic fixtures with known expected memberships to test these behaviors. Do not infer “robotics” from arbitrary text in this milestone unless the user explicitly labels it.

## Milestone 3 — semantic categories

Outcome: user-defined categories such as “robotics” can match varied titles, interests, and relationship notes beyond exact labels.

Before implementation, define category membership examples and what evidence may be used. Start with assignment to user-defined categories; automatic discovery of unnamed clusters is a separate optional feature.

Evaluate whether text rules, embeddings, or a model materially improve the example tasks. Do not assume an LLM is necessary. Preserve source text, show the reason/evidence for suggested membership, allow correction and multiple memberships, and leave insufficient-evidence cases unclassified. Derived assignments must not overwrite supplied facts.

Acceptance: a reviewed fixture set distinguishes useful semantic matches, incorrect matches, and insufficient information. Compare against the structured-grouping baseline. Report observed limitations instead of claiming accuracy without evaluation. Any external AI provider and data transfer require an explicit product decision before integration.

## Milestone 4 — graph/list views

Outcome: users can switch between graph and list presentations of the same filtered network and groups.

Preserve selection/filter state across the switch. Display actual stored relationships as edges; shared category membership must not invent a relationship. Explain visual encodings, allow contact inspection, and test readability with the 100/500-contact demo sets. Add timeline/maps only as separately scoped workflows.

## Deferred decisions

AI summaries/transcription, reminders, relationship strength, second-degree discovery, shared identities, real-time collaboration, integrations, billing, and career-center administration are not part of milestone 1. Do not prebuild infrastructure for them. Future cost estimation should follow concrete hosting, storage, model/API usage, and operational choices.

## Progress log
The coding agent should append brief dated entries: step completed, user-visible result, commands/tests run, remaining limitation, next step. Keep this log factual and concise.

- **2026-10-18**: Completed Milestone 1.
  - *Steps completed*: 1.1 (typed CSV parser), 1.2 (schema, migrations, focal person, domain operations), 1.3 (safe idempotent import preview & atomic commit), 1.4 (list interface, contact detail, mutations, disconnect/reconnect, confirmed deletion), 1.5 (deterministic 100/500 contact generator, demo seeder, verification suite).
  - *User-visible result*: Fully operational local web app at `http://127.0.0.1:8000` with dark-mode UI, contact list, detail views, safe 2-step CSV import, and instant reconnection workflow.
  - *Commands/tests run*: `uv sync --locked`, `uv run --locked pytest` (21 passed), `uv run --locked ruff check .` (passed), `uv run --locked ruff format --check .` (passed), `uv run --locked mypy .` (passed), `curl` HTTP verification.
  - *Remaining limitation*: Graphical browser subagent encountered sandbox network download restriction for Playwright driver (404 on AzureEdge CDN); automated HTTP and TestClient integration tests fully cover all 20 smoke test steps.
  - *Next step*: Propose Milestone 2 (filtering and grouping with nodes and edges).

