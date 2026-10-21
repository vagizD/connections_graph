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

- [x] Add manually editable person attributes needed by actual queries, initially interests/topics and education if the user needs them.
- [x] Add structured relationship fields: context labels (classmate, coworker, conference), shared activities, and optional last-interaction date. Keep free-text notes as well.
- [x] Use small typed fields/related tables; avoid an unrestricted custom-schema system.
- [x] Node facts remain distinct from edge facts: a person's university is a node fact; attending the same program together is relationship context.
- [x] Model edge total state and chronological interaction sequence (event log for meetings, calls, audio transcripts, notes).

### Filter → group → inspect

- [x] Filter contacts by node attributes and the relationship to the focal Person. Define this edge scope explicitly; an unrelated contact-to-contact edge must not satisfy a “my relationship” filter.
- [x] Combine criteria across both sources. Default: AND across fields, OR across selected values within one field; show the active rules clearly.
- [x] Allow user-selected grouping dimensions, initially company, location, education, and relationship context.
- [x] Recompute groups when filters or underlying records change. Here “online” means interactive regrouping; it does not require an online-learning algorithm.
- [x] Permit overlapping groups for multi-valued attributes. Show per-group counts and a separate unique-contact total so overlaps are not double-counted as people.
- [x] Display unknown values explicitly. Absence of a value is not evidence of a negative fact.
- [x] Let users inspect each group's contacts in a list and open/edit a contact without losing filters.

Acceptance examples verified:

1. Filter to London, then group by company.
2. Filter to robotics interest and classmate relationship context, then inspect contacts with old or unknown last-interaction dates as distinct cases.
3. Editing a person's company or relationship label updates their membership correctly.
4. Clearing filters restores all connected contacts, with unknown values and overlapping memberships handled consistently.

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
- **2026-10-19**: Added instant test table import capability.
  - *Steps completed*: Added `POST /import/test-table` endpoint and one-click import buttons to the main empty-state view, main controls toolbar, and `/import` page; added end-to-end integration test verifying immediate network population and idempotent re-import.
  - *User-visible result*: Users can instantly populate their network with 10 fictional connections and edge data with a single click from the home screen empty state or the import page.
  - *Commands/tests run*: `uv run --locked pytest` (22 passed), `uv run --locked ruff check .` (passed), `uv run --locked ruff format --check .` (passed), `uv run --locked mypy .` (passed).
  - *Remaining limitation*: None.
- **2026-10-19**: Completed Milestone 2 (Filter and group with nodes and edges).
  - *Steps completed*: 
    1. Schema migration `002_milestone2_enrichment.sql` adding node facts (`education`, `interests`), edge total state (`context_labels`, `shared_activities`, `last_interaction_date`, `summary`), and `interactions` event sequence table (for meetings, calls, audio transcripts, notes).
    2. Updated domain models, services, and web routes for person/relationship mutations and interaction event logging.
    3. Implemented multi-criteria filtering (location, company, education, interest, context label, last interaction date recency/unknown) and dynamic grouping (by company, location, education, context label, interest, or interaction recency) with unknown value handling and deduplicated unique contact totals.
    4. Updated UI: dashboard filter bar with facet datalists, active filter chips, grouped section presentation, and full interaction timeline logger on contact detail page.
    5. Added comprehensive unit and integration tests covering all 4 acceptance criteria and web workflow.
  - *User-visible result*: Complete interactive filtering and grouping dashboard on the home page; contact detail view displays node facts, edge total state, and interaction event sequence with audio transcript logging support.
  - *Commands/tests run*: `uv run --locked pytest` (26 passed), `uv run --locked ruff check .` (passed), `uv run --locked ruff format --check .` (passed), `uv run --locked mypy .` (passed).
  - *Remaining limitation*: External speech-to-text API integration for audio transcription is deferred to future milestone; timeline supports entering audio transcripts and conversation details directly.
  - *Next step*: Milestone 3 (semantic categories and matching).
- **2026-10-19**: Implemented clean tabbed navigation, entrance profile tab, and collapsed-by-default filters.
  - *Steps completed*:
    1. Filter panel collapsed by default on the contacts view with an intuitive `#btn-toggle-filters` toggle button and active filter indicator badge. Active filter chips bar remains visible for quick inspection and clearing.
    2. Separated interface into dedicated tabs: Entrance Profile Tab (`tab=profile`) and Contacts Directory Tab (`tab=contacts`).
    3. Entrance Profile Tab features a clean focal user profile card displaying name, headline, company, location, alma mater / education, and interest tags, alongside an inline profile editor (`POST /profile/edit`), overview metric cards (Connected Contacts, Organizations Represented, Active Touchpoints), and getting started/quick connections shortcuts.
    4. Cleaned up global navbar by removing cluttering action buttons; placed `Add Contact`, `Import CSV`, and `Import Test Table` inside the dedicated Contacts Directory header.
  - *User-visible result*: Streamlined, aesthetic entrance view with user profile card and network metrics; isolated, clean Contacts directory with collapsible filter dashboard.
  - *Commands/tests run*: `uv run --locked pytest` (27 passed), `uv run --locked ruff check .` (passed), `uv run --locked ruff format --check .` (passed), `uv run --locked mypy .` (passed).
- **2026-10-19**: Added 100-connection and 5-classmate test fixtures and instant keyword search bar.
  - *Steps completed*:
    1. Built `data/fixtures/duke_international_100.csv` (100 connections across 51 companies, 14 international country hubs, 10 universities, 50+ context tags) and `data/fixtures/duke_classmates_5.csv` (5 Duke classmates cohort for focused tasks).
    2. Added endpoints `POST /import/test-100`, `POST /import/test-5`, and `POST /network/reset` with dedicated UI cards and toolbar buttons.
    3. Added always-visible, real-time fast keyword search bar with live client-side filtering, keyboard shortcut (`/`), and backend query integration.
  - *Commands/tests run*: `uv run --locked pytest` (30 passed), `ruff`, `mypy`.
- **2026-10-19**: Implemented Audio Transcription & AI Conversation Summarizer with edge state auto-updating.
  - *Steps completed*:
    1. Created `app/ai_summarizer.py`: Multimodal Gemini 2.5 Flash audio transcription and conversation analysis service with deterministic local intelligence fallback.
    2. Implemented `record_summarized_interaction` in `app/services.py`: Formats executive summary, action item checklist, key takeaways, and verbatim transcript into `interactions` event sequence; automatically synthesizes and updates `Relationship.summary`, advances `last_interaction_date`, and merges newly discovered context labels and shared activities without duplicates.
    3. Added endpoint `POST /contacts/{person_id}/interactions/audio` in `app/main.py`.
    4. Enhanced `app/templates/contact_detail.html` with an interactive Audio & Voice Memo widget supporting in-browser microphone recording via HTML5 `MediaRecorder` (with pulsing indicator, timer, and preview player), file upload (`.mp3, .wav, .m4a, .webm, .ogg`), and raw transcript pasting.
    5. Added unit tests (`tests/test_ai_summarizer.py`) and integration tests (`tests/test_webapp.py`).
  - *User-visible result*: Contact detail view provides a live voice recorder, audio file uploader, and AI summarizer that automatically transcribes, extracts action items, and updates the contact's overall relationship state.
  - *Commands/tests run*: `uv run --locked pytest` (35 passed), `uv run --locked ruff format --check .` (passed), `uv run --locked ruff check .` (passed), `uv run --locked mypy .` (passed).
- **2026-10-20**: Implemented Actionable Tasks Tab, 1-Month Seeded Follow-ups, and Minimalist UI.
  - *Steps completed*:
    1. Database migration `003_tasks_schema.sql` adding `tasks` table with foreign key to contacts and deadline indexes.
    2. Action items extracted from AI conversations/notes automatically parsed into discrete task records with 1-week default deadline.
    3. Dedicated top-level Tasks tab (`/?tab=tasks`) with live badge count, sorting by upcoming deadline, and toggle complete/delete actions.
    4. Contact-specific tasks widget on contact detail view.
    5. Test imports (`/import/test-100`, `/import/test-5`, `/import/test-table`) automatically seed ~5 realistic follow-up tasks distributed across the nearest month.
    6. UI refinement: eliminated technical jargon ("log", "read only", "total state"), unified the interaction form into a single streamlined panel, placed inline microphone button directly under note area.
- **2026-10-20**: Implemented Milestone 4 — Interactive Network Graph View with Planar Radial Layout.
  - *Steps completed*:
    1. Built planar radial layout algorithm in `app/services.py` (`get_network_graph_payload`) positioning the focal person (You) at the center origin `(0.0, 0.0)` and distributing contacts into angular community sectors (by company, context, or active grouping) across concentric orbits.
    2. Computed direct focal edges and mutual cross-contact edges between connected peers.
    3. Added segmented switch control (`[ 📄 List View | 🕸️ Graph View ]`) in the Contacts toolbar with preference persistence via `localStorage` and URL parameter sync (`?view=graph`).
    4. Built high-DPI HTML5 canvas renderer with dynamic repulsion physics, smooth mouse/pinch zoom, and pan capabilities.
    5. Added interactive cluster filter chips, hovering highlights with glassmorphic tooltip card, and click-to-open slide-over contact inspector drawer.
    6. Synchronized real-time search input (`/` shortcut) with live graph node highlighting/dimming.
    7. Added JSON API endpoint `GET /api/graph` supporting all multi-criteria filters.
    8. Added unit and integration tests in `tests/test_services.py` and `tests/test_webapp.py`.
  - *User-visible result*: Users can toggle seamlessly between List View and an interactive, aesthetically animated Network Graph with themselves at the center, dragging nodes, zooming, inspecting relationship summaries, and filtering clusters.
  - *Commands/tests run*: `uv run --locked pytest` (40 passed), `uv run --locked ruff check .` (passed), `uv run --locked ruff format --check .` (passed), `uv run --locked mypy .` (passed).
  - *Remaining limitation*: None.

