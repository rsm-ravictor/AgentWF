# AAT System Context

## Purpose
This project is a scaffold for an asset and lease management system with tiered access permissions, centralized document repository monitoring, and workflow automation.

## Runtime
The system runs locally and references documents stored on the local device (local filesystem paths, not a remote store or cloud bucket). This applies to both the folder-based document repository and any files fed into a workflow for analysis.

## Business Divisions
- **Multifamily/Residential**
- **Office/Retail**
- **Construction**

Each division is a self-contained business line with **its own super admin** — the title
belongs to a division, not to the company, so Residential's super admin has no reach into
Construction. Folder categories are per division too: Construction carries the shared
categories plus Contractor Insurances, Permits and Approvals, Change Orders, Lien Waivers
and Safety Reports.

## Access levels
Three levels, held **per division**, so an account is always a division *and* a level:

| Level | Sees | Can |
| --- | --- | --- |
| **Super admin** | everything in their division — every use case, approval and record | run, approve, edit use cases, manage people, assign permissions |
| **Admin** | their division, including every general user's activity | run, approve, edit use cases, manage people |
| **General** | their own work only | run use cases and upload documents |

Every level can run use cases — execution is the point of the system, not a privilege.
What separates the levels is whose work they can see (`view_team_activity`), whether they
can sign off, and whether they can change the system's definitions and access.

No level reaches beyond its own division by default: `view_all_divisions` is off for
everyone including super admins, and has to be granted deliberately.

## Core Requirements
- Centralized repository that tracks all leases and flags expired leases.
- Manual folder upload support in Phase 1, with email ingestion deferred to Phase 2.
- External decision workflows should reference an environment-defined API key for downstream service calls.
- Document review decisions are made by an LLM against an explicit, per-workflow rubric, with a human signing off on the outcome.
- Required folder categories:
  - Vendor Insurances
  - Renters Insurance
  - Lease Agreements
  - Checklists
  - Breach Agreement Notices
  - Daily Activity Reports
  - AAT Company Requirements/Documents

## Multifamily/Residential Phase 1 Workflows
> **Note:** The specific use cases listed below are placeholders for the current build and are expected to change. Do not treat this list as final scope — the UI/UX structure in the "Frontend Redesign" section is the stable part; the use cases that populate it are not.

1. **Vendor Insurance**
   - Compare vendor insurance documents against AAT requirements.
2. **Renter’s Insurance**
   - Generate a tenant checklist from the lease.
   - Send checklist to tenant.
   - Compare submitted insurance to checklist.
   - Approve if compliant or draft a corrective email if not.
3. **Lease, Addenda, and File Checklist**
   - Prepare lease documents.
   - Create a file checklist.
   - Verify required documents are received and matched.
   - Sign off and queue for human review before archiving.
4. **Notices of Breach of Lease**
   - Retrieve the tenant’s lease on violation.
   - Draft breach notice citing specific lease sections.
   - Check prior breaches and include history.
   - Queue notice for management review and sending.
   - Log breach for future reference.
5. **Security Report Workflow**
   - Triggered by daily activity reports.
   - Review flagged items and log incidents.
   - Minor nonrecurring issues receive notes only.
   - Severe first-time issues flagged for management.
   - Lease breaches generate notices similar to breach workflow.

## Frontend Redesign (supersedes prior single-page flow)

The frontend is being restructured. This section describes the target structure; the
"Current State" and "Frontend" sections below describe what exists today and will be
migrated toward this.

### 1. Landing / Login
- Shows the company name and a login screen.
- Login selects a division: **Residential/Multifamily** or **Office/Retail**.
- Division selection determines which dashboard, folders, and use cases the user sees next.
- While the login is simulated, the username is what sets the access level, so the seeded
  accounts are listed as one-click sign-ins (`GET /session/accounts`) with the role each
  holds and whether it can edit a workflow definition. Picking a role is a choice on the
  screen rather than an email to remember. This affordance goes when real auth lands.

### 2. Division Dashboard
- Landing page per division after login.
- Shows overall information at a glance: the document types/folders that data lives in
  (Vendor Insurances, Renters Insurance, Lease Agreements, Checklists, Breach Agreement
  Notices, Daily Activity Reports, AAT Company Requirements/Documents, etc.).
- Displays a tile per use case for that division. Every use case tile follows the same
  visual pattern regardless of what the use case actually does — only the underlying
  execution logic differs per use case; the presentation layer is shared.

### 3. Use Case Detail Page
Once inside any use case, a persistent top bar lists every use case name for that
division, so the user can jump directly from one use case to another without going
back through the dashboard. The active use case is indicated in the bar.

Clicking a use case tile (or a name in the top bar) opens a detail page with this fixed layout:

- **Header:** use case name at the top.
- **Body split 2/3 (left) / 1/3 (right):**
  - **Left (2/3) — Workflow visual:** a color-coded, clearly labeled diagram of the
    use case's workflow (nodes/steps). Includes an "enlarge" button that opens the
    diagram full-screen in an overlay on top of the page, with a "close" button at the
    top of the overlay to dismiss it back to the normal layout.
  - **Right (1/3) — Narrative:** a written walkthrough of the use case. Section headers
    in the narrative correspond 1:1 with the nodes/steps shown in the left-side diagram,
    with small bullets under each header describing what's happening at that step.
    Includes an **Edit** control: editing here can add/update/reorder/remove steps, and
    edits propagate to (a) the visual diagram on the left — live, as they are typed,
    before anything is saved — and (b) the underlying workflow definition itself on save,
    so the diagram, narrative, and actual execution logic stay in sync rather than
    drifting into three separate sources of truth.
- **Footer — Execution control:** a "Start Process" action that kicks off a live run.
  While running, a status bar tracks progress through the workflow's steps in real
  time. On completion, the page shows the final outcome/result of that run.

### 4. Reference Page
A dedicated page (reachable from the top bar or dashboard) that is not tied to any one
use case. It holds:
- A summary rollup of the use cases in the division — what each one does, at a glance.
- A **change log of workflow definitions**: every version each workflow has had, newest
  first, with what changed, who changed it, when, the steps that version contained, and a
  **roll back to this version** action. Because a definition decides what a run executes,
  an edit is a change to the system's behaviour; this is the way back from one that broke
  something.
- Important language/terminology references: shared vocabulary, rubric terms, and any
  standard phrasing used across use case narratives, so terms stay consistent instead of
  being redefined slightly differently inside each use case's narrative panel.

### 5. Settings, and the personal profile page
**Settings** (user menu) is where accounts and access live, because they are one subject:
a profile's role is a name for a set of permissions, so creating a profile and defining
what its role means belong on the same page. Two sections:

A switcher at the top chooses which **division** is being administered, because both
sections below it are per division.

- **Profiles** — create a profile (name, email, division, level, optional password) with a
  live preview of exactly what the chosen level grants in that division, above the roster
  of existing profiles with their level, division and active state editable in place.
- **Role permissions** — the level × permission matrix for the chosen division, editable in
  place, with a restore-defaults action.

Settings is reachable at every level. Creating and editing profiles needs `manage_users`,
which a general user does not hold — and since any email off the roster is provisioned as
a general user, that would otherwise be a dead end on the very screen meant to fix it. The
disabled form therefore carries **Give my level this**: one click grants `manage_users` to
the signed-in level in its own division, re-resolves the session, and unlocks the form in
place. The gate stays real — the server still refuses without the permission — it just
stops being a wall in a build whose permissions are open anyway.

Administering another division's people needs `view_all_divisions`, which the server
enforces on both creating and editing accounts.

**Profile & access** (user menu) stays separate as the read-only personal page: what this
account may do and where those limits come from.

### Design implication
Because every use case shares this same page shell (header, 2/3 diagram + 1/3 narrative,
enlarge/close overlay, edit-in-place, start/status/outcome footer), the use case-specific
piece is just: the workflow definition (nodes + step logic), the narrative content, and
the execution/outcome payload. The shell itself should be built once and reused across
Vendor Insurance, Renter's Insurance, Lease/Addenda Checklist, Notices of Breach, DAR/
Security Report, and any future use cases (including Office/Retail's Phase 2 set).

## Office/Retail and Construction
- Mirror Phase 1 with the same core structure and automation patterns.
- Adapt folder mappings, document sources, and division-specific requirements per division.
- Adding a business line is one entry in `Division`, one folder list in
  `DIVISION_FOLDER_MAPPING`, and one short key in `DIVISION_KEYS`. Its levels, permission
  matrix, folders, workflow definitions and revision history all come into being from
  those; nothing else is per-division by hand.
- The five Phase 1 use cases exist in every division, since definitions are seeded per
  division from the same shipped steps and then owned locally. Construction's use cases are
  therefore Residential's until someone edits them — the paperwork it needs
  (permits, change orders, lien waivers) has folders but no use cases of its own yet.

## Implementation Notes
- **FastAPI** — the backend web framework; serves the API endpoints (e.g.
  `POST /workflows/{id}/run`, `GET /dashboard/summary`) and runs the local server.
- **SQLAlchemy** — the ORM (object-relational mapper) the backend uses to read/write the
  local database (leases, users, etc.) from Python without writing raw SQL.
- **Folder-based routing** — how a division (Residential/Multifamily vs Office/Retail) and
  its folder categories (Vendor Insurances, Lease Agreements, etc.) map to what a logged-in
  user can see and upload. This is the backend counterpart to the login → division →
  dashboard flow described above.
- **Workflow definitions** — each use case is a stored list of steps rather than code, so
  the diagram, the narrative and the run stay one thing. See "Current State" below.
- Phase 2 reuses the same architecture with division-specific adjustments rather than a
  separate rebuild.
- Email inbox ingestion is a Phase 2 capability; Phase 1 relies on manual upload and folder
  permission controls.

## Current State (as of 2026-08-14)

### One definition behind diagram, narrative, and execution
A use case's workflow is an ordered list of steps stored in `workflow_steps`
(`aat_system/workflow_repo.py`). The colour-coded diagram, the narrative
walkthrough, and the track the run walks are three renderings of those same
rows — editing the narrative rewrites them, so the three cannot drift apart.
Steps are seeded from `DEFAULT_STEPS` on first read, then owned by whoever edits
them; changing a workflow needs no deploy. Definitions are per division.

Editing is live: while the walkthrough is open for editing, the diagram redraws
from the draft on every keystroke — renaming a step renames its node, changing a
step's type recolours it, and adding, reordering or removing a step does the same
to the diagram, all before anything is saved. The draft diagram is drawn dashed
and badged **Unsaved edits**, because it is not yet what a run would execute; a
run is refused while edits are open rather than reporting progress against steps
that do not exist yet. Save commits the draft through
`PUT /workflows/{id}/definition`, at which point the diagram, the walkthrough and
the run are the same thing again. Cancel drops it and the saved diagram returns.
The **Edit** control is visible to every role but disabled for those without
`edit_workflow`, so the capability reads as withheld rather than absent.

Each step carries a `kind`, which both colours its node and decides what the
runner does there: `intake` (check required documents against the repository),
`analysis` (grade an attached document, or report what is on file), `decision`
(apply the pass rule), `human` (queue an approval case), `record` (write to the
record file), `note` (reported, no action). A step someone adds in the narrative
is a step the run reports on — there is no second list of steps in code.

### Definition history and rollback — `workflow_revisions`
Every write to a definition — the first seed, an edit, a reset to defaults, a
rollback — lands a row in `workflow_revisions` holding the whole definition as it
stood after that write, stored as JSON so a historical version keeps exactly what
was saved even if the step table's shape or the shipped defaults later change.
Versions are 1-based per workflow and division, so "v3" means something to a
person reading the log.

Each row carries a plain-language note of what changed against the previous
version — added, removed, retyped, reworded, reordered, naming the steps —
written at save time while both sides are in hand, so the log reads as what
happened rather than as two lists to compare. Editing a workflow nobody had
opened yet seeds the shipped baseline first, so version 1 is always a version
there is something to roll back to. A save that changed nothing records nothing.

A rollback (`POST /workflows/{id}/revisions/{version}/restore`) replays that
version's steps as a **new** version rather than rewinding, so nothing leaves the
history and a bad rollback can itself be rolled back. The Reference page's change
log is the division-wide view of this; the use case page's version chip links into
it filtered to that workflow.

### Running a use case — `aat_system/workflow_runner.py`
`POST /workflows/{id}/run` streams newline-delimited JSON, one event per state
change, so the status bar moves while the run is still going rather than
reporting only at the end. Each step does real work against the database: the
intake step's present/missing answer comes from filename matching declared in
the catalog, the human step writes a real `approvals` row (deduped to one open
case per property/unit), and the record step writes a real `workflow_records`
row. A run that cannot clear still finishes, still queues, and still records.

### LLM document analysis — real, working
`aat_system/llm_analyzer.py` is where review decisions are actually made. A document
attached to a run (PDF, image, or text) is sent to Claude and graded against the rubric
for its workflow. The response shape is enforced by the API through structured outputs
against a Pydantic schema, so it always parses — there is no "please return JSON"
prompting, no regex extraction, and no retry-on-parse-failure loop.

Each of the five Phase 1 workflows carries a concrete rubric (`WORKFLOW_RUBRICS`). For
every requirement the model returns `met` / `not_met` / `unclear` plus a supporting quote
from the document, and the verdict carries:

- `decision` — `approve` / `needs_human_review` / `reject`, with a confidence level
- `findings` — one graded entry per rubric requirement, with evidence
- `extracted_fields` — policy numbers, dates, limits, names as they appear
- `missing_information` — what a reviewer still needs

`approve` is reserved for documents where every requirement is met and nothing is
ambiguous; judgment calls and near-threshold values route to `needs_human_review`. This
keeps the human-in-the-loop requirement intact — the model does the reading and makes the
call auditable, it does not clear things through.

### Daily Activity Report extraction — removed, to be rebuilt
The previous DAR extraction implementation (`dar_analyzer.py`, highlight-based triage,
`dar_reports`/`dar_incidents` persistence, the Incident Log tab and its endpoints) has been
removed rather than left in place as stale spec. The **Daily Activity Report use case still
exists** and runs on the shared shell like every other one; only the highlight-extraction
implementation behind it is gone. When the new approach is defined it becomes a step
definition plus a rubric, not a new page.

### Levels and permissions — `config.py` for the defaults, `permission_repo.py` for the live grants
Three levels (`Role`) and eleven discrete capabilities (`Permission`), so the Profile and
Settings screens can report *what* a level actually lets someone do rather than showing an
opaque label. Unlike the six job titles this replaced, the levels are a true ladder:
General ⊂ Admin ⊂ Super admin, so a promotion never quietly takes access away.

**Levels are code; what a level grants is data, per division.** `config.ROLE_PERMISSIONS`
is the shipped default — the answer on a fresh database and what "Restore defaults" returns
to. `role_permission_sets` is keyed by **(division, level)** and holds the live
configuration Settings writes, and every gate resolves through
`permission_repo.granted_for()`, so changing a grant changes what the system permits rather
than only what a screen offers. Keying by division is the point: Construction can let its
general users edit use cases without that touching Residential's.

The whole grant list is one row per pair, not a row per grant, so "this level has been
configured and holds nothing" is representable — with a row per grant it would be
indistinguishable from "never configured", and the defaults would silently come back.

The matrix lives in **Settings → Role permissions**, under a switcher for which division is
being administered: checkboxes per level, the signed-in account's own row highlighted when
it is their own division, a marker on any level that differs from its shipped default, and
a restore-defaults action scoped to that division. Saving re-resolves the current session,
so a permission granted to your own level takes effect on the screens you are already
looking at — the Edit control on a use case un-greys, and the create-profile form unlocks,
without a re-login.

### Redaction before ingestion — `aat_system/redaction.py`
A document is redacted on its way into the repository, not after. `document_repo`
routes every upload through `redact_uploaded_file` before archiving it, stamps
`Document.redacted_at`, and the folder view labels the result. Patterns live in
`REDACTION_PATTERNS` (SSN, card and tax numbers, phone, email). PDF handling is
page-level and coarse — extend `redaction.py` for content-level rules.

### Seeded on first run
Startup creates the core folders for both divisions, writes each role's shipped
permissions, seeds the roster, and fills an empty approvals queue with illustrative cases
tagged `source='sample'`, so a fresh install is not an empty screen. Samples are labelled
as samples in the UI and clearable from the dashboard, so a demo queue is never mistaken
for real work.

`DEFAULT_ROSTER` is 18 accounts, password `prototype`: for each of the three divisions, a
named account at each level — `super.residential@` / `admin.residential@` /
`user.residential@`, and the same for `retail` and `construction` — plus a **test account**
per level per division (`test.super.construction@` and so on). Any level of any division can
be signed into without editing the roster or borrowing a real person's account. Test
accounts are marked `is_test`, named "Test …", and folded into their own group on the login
screen so they are never mistaken for real staff. The login screen lists only the accounts
belonging to the division picked above it.

### API surface
| Endpoint | Purpose |
| --- | --- |
| `GET /` | Serves the single-page UI |
| `GET /dashboard/summary` | Everything the division dashboard shows, in one round trip |
| `GET /workflows/{id}` | The use case detail bundle: definition, approvals, records, documents, rubric |
| `PUT /workflows/{id}/definition` | Rewrite the steps from an edited narrative |
| `POST /workflows/{id}/definition/reset` | Restore the shipped definition |
| `GET /workflows/{id}/history` | Every version of that definition, newest first |
| `POST /workflows/{id}/revisions/{version}/restore` | Roll back to a past version |
| `POST /workflows/{id}/run` | Run the use case, streaming one JSON event per step |
| `GET /workflows/{id}/records.csv` | The workflow's record file |
| `GET /reference` | Use case rollup, definition change log, step kinds, and shared vocabulary |
| `GET /approvals`, `POST /approvals/{id}/resolve` | The human-in-the-loop queue |
| `DELETE /approvals/samples` | Clear the seeded sample cases for a division |
| `GET /repository/documents`, `GET /repository/documents/{id}/download` | Folder contents and archived files |
| `POST /token`, `POST /users`, `GET /users/me` | Auth and user management |
| `POST /session/resolve`, `GET /roles`, `GET /admin/users`, `PATCH /admin/users/{id}` | Session role resolution and administration |
| `POST /admin/users` | Create a profile (needs `manage_users`) |
| `GET /permissions?division=` | One division's level × permission matrix, live grants beside shipped defaults |
| `PUT /permissions/{role}` | Replace what one level may do in one division |
| `POST /permissions/reset` | Put one division's levels back to their shipped permissions |
| `GET /session/accounts` | The seeded accounts the login screen offers as a quick pick |
| `POST /documents/upload` | Ingest into the repository |
| `GET /leases/expired` | Lease expiration scan |
| `GET /folders/{division}/{folder}/documents` | Folder contents (authenticated) |
| `GET /users/{user_id}/folders` | The folders a given account may reach (authenticated) |

> Document grading no longer has its own endpoint. Attaching a file to
> `POST /workflows/{id}/run` grades it as that workflow's `analysis` step, so there is one
> path to a verdict rather than two that can diverge. `/phase2/email-ingestion` is gone —
> Phase 2 ingestion is not built, and a placeholder endpoint said otherwise.

### Visual design — quiet monochrome
The whole app is one restrained aesthetic: a warm neutral palette, a single muted
sand/stone accent, hairline rules and whitespace instead of cards. Light is a warm
off-white (`#F7F5F2`) with near-black text (`#111111`); dark is a warm near-black
(`#121110`, never pure black) with warm off-white text — the same design inverted,
not a second design language.

Every colour is a token at the top of `style.css`, defined once in three blocks: the
light `:root`, a `prefers-color-scheme: dark` block guarded by
`:root:not([data-theme='light'])`, and a `[data-theme='dark']` block so the manual
toggle wins in both directions. No component defines a colour of its own, so the
theme switch is consistent across login, dashboard, use case detail, reference,
settings and profile without any screen being styled individually. `color-scheme` is
set alongside, so native selects, checkboxes and scrollbars follow the theme too.

Dark mode follows the system by default: with no stored choice the app writes no
`data-theme` attribute at all and the media query governs, so it keeps tracking the
system as it changes. The toggle stores a preference only once someone actually
uses it.

Held to throughout: one sans family, light weight body, uppercase wide-tracked
labels and section headers, a three-size type scale, flat surfaces (no shadows or
gradients), square corners, buttons as a word inside a hairline rather than a filled
pill, and thin-line monochrome icons — stroke width is overridden in CSS so every
symbol matches and icons only invert between modes.

The deliberate exception is **live run status**. Running / done / needs review carry
the accent (or primary text for done) clearly enough to read at a glance, because a
run's state is the one thing minimalism must not hide. Step kinds, formerly six
hues, are now a tonal ramp read together with their uppercase labels.

### Frontend
Single-page app in `static/` (`index.html`, `app.js`, `style.css`) — no build step. It
implements the screens described under "Frontend Redesign" above: login/division,
division dashboard, use case detail and reference in the main nav, plus settings (profiles
and role permissions) and the personal profile page reached from the header's user menu. The use case shell is written
once and reused for every use case; a new use case needs no new frontend code.

The dashboard carries four at-a-glance tiles (use cases, documents on file, leases
expiring in 30 days, pending human review), the use case tile grid, a folder grid that
expands into a searchable document list, and the approvals queue grouped by use case.
`index.html` is served with `Cache-Control: no-store` and its asset URLs are stamped with
each file's mtime, so a browser cannot mix an old `app.js` with a new `index.html`.

### Configuration
`ANTHROPIC_API_KEY` is required to grade an attached document; `ANTHROPIC_MODEL` defaults
to `claude-opus-5`. See `.env.example`. Without a credential the dashboard and the run
footer both say so up front, attaching a file returns 503 with an actionable message, and
a run with no attachment still works — it reports on what is already on file.

### Testing
`python -m pytest tests/ -q` — 81 tests, no API key required and no model call made.
Coverage is on the definition seed/edit/reset cycle, revision history and rollback
(including that a rollback is a new version and that the bad edit stays on the record),
the required-document check, records and the approvals queue (`test_workflow_repo.py`),
the run engine including that an edited definition changes what the run does
(`test_workflow_runner.py`), and the access model (`test_access_control.py`): that the three
levels are a true ladder, that every level can run use cases, that no level crosses
divisions by default, that a super admin is refused another division's work and folders,
that granting Construction's general users a permission leaves Residential's alone, that a
level stripped to nothing stays stripped, that resetting one division does not reset
another, and that every division has its own account at every level.

Sample documents in `sample_docs/`:

- `vendor_coi_brightline.txt` — a certificate of insurance whose general liability limit is
  $1M against the rubric's $2M requirement, and which names AAT as certificate holder rather
  than additional insured. Both come back short, so the rejection path is exercisable.

## Known Gaps
- `/dashboard/summary`, `/workflows/*`, `/reference` and `/approvals` are unauthenticated so
  the preview UI can reach them. Gate them behind `get_current_active_user` before exposing
  beyond localhost — `POST /workflows/{id}/run` accepts uploads and spends API tokens, and
  the definition write, reset and rollback endpoints change what every later run executes.
  The UI disables editing and rolling back for roles without `edit_workflow`; the server
  does not yet refuse them.
- UI login is simulated: any password is accepted and no token is issued. The username is
  resolved server-side to a real account, so the *role* is real even though the session is
  not; the authenticated endpoints are not exercised by the frontend.
- Settings → Role permissions is deliberately unguarded in this build: any signed-in
  account can change what any role grants, so nobody can lock themselves out of a prototype
  they are still shaping. The section says so on itself. Before this leaves localhost, gate
  `PUT /permissions/{role}` and `POST /permissions/reset` on `MANAGE_ROLES` — an account
  that can widen its own permissions has, in effect, all of them. Creating and editing
  profiles is already gated on `manage_users`, which is only meaningful once the
  permissions endpoints are too.
- The API endpoints and the frontend have no automated coverage; the backend logic does.
- An existing `aat_system.db` keeps the now-unused `dar_reports`, `dar_incidents` and
  `workflow_sops` tables — `create_all` does not drop tables. Delete the file for a clean
  schema; nothing reads them.
- **A database written before the three-level model cannot be read by it.** Roles are
  stored as an enum, and the old names (`super_user`, `division_head`, `subgroup_owner`,
  `reviewer`, `agent`) are no longer defined, so those rows raise on load. There is no
  migration: delete `aat_system.db` and let startup reseed. The same applies to
  `role_permission_sets`, which gained a division column.
- Uploading into the repository still requires a token, so in practice documents are seeded
  outside the UI. The dashboard folder view reads them correctly once they are there.
