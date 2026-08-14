# AAT System Context

## Purpose
This project is a scaffold for an asset and lease management system with tiered access permissions, centralized document repository monitoring, and workflow automation.

## Runtime
The system runs locally and references documents stored on the local device (local filesystem paths, not a remote store or cloud bucket). This applies to both the folder-based document repository and any files fed into a workflow for analysis.

## Business Divisions
- **Multifamily/Residential**
- **Office/Retail**

Each division has a division head with full access to all workflows and folders within their division. Subgroup owners have limited, role-specific access to assigned folders and workflows.

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
    Includes an **Edit** control: editing here can add/update steps, and edits should
    propagate to (a) the visual diagram on the left and (b) the underlying workflow
    definition itself, so the diagram, narrative, and actual execution logic stay in
    sync rather than drifting into three separate sources of truth.
- **Footer — Execution control:** a "Start Process" action that kicks off a live run.
  While running, a status bar tracks progress through the workflow's steps in real
  time. On completion, the page shows the final outcome/result of that run.

### 4. Reference Page
A dedicated page (reachable from the top bar or dashboard) that is not tied to any one
use case. It holds:
- A summary rollup of the use cases in the division — what each one does, at a glance.
- Important language/terminology references: shared vocabulary, rubric terms, and any
  standard phrasing used across use case narratives, so terms stay consistent instead of
  being redefined slightly differently inside each use case's narrative panel.

### Design implication
Because every use case shares this same page shell (header, 2/3 diagram + 1/3 narrative,
enlarge/close overlay, edit-in-place, start/status/outcome footer), the use case-specific
piece is just: the workflow definition (nodes + step logic), the narrative content, and
the execution/outcome payload. The shell itself should be built once and reused across
Vendor Insurance, Renter's Insurance, Lease/Addenda Checklist, Notices of Breach, DAR/
Security Report, and any future use cases (including Office/Retail's Phase 2 set).

## Office/Retail Phase 2
- Mirror Phase 1 with the same core structure and automation patterns.
- Adapt folder mappings, document sources, and division-specific requirements for Office/Retail.

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

Each step carries a `kind`, which both colours its node and decides what the
runner does there: `intake` (check required documents against the repository),
`analysis` (grade an attached document, or report what is on file), `decision`
(apply the pass rule), `human` (queue an approval case), `record` (write to the
record file), `note` (reported, no action). A step someone adds in the narrative
is a step the run reports on — there is no second list of steps in code.

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

### API surface
| Endpoint | Purpose |
| --- | --- |
| `GET /` | Serves the single-page UI |
| `GET /dashboard/summary` | Everything the division dashboard shows, in one round trip |
| `GET /workflows/{id}` | The use case detail bundle: definition, approvals, records, documents, rubric |
| `PUT /workflows/{id}/definition` | Rewrite the steps from an edited narrative |
| `POST /workflows/{id}/definition/reset` | Restore the shipped definition |
| `POST /workflows/{id}/run` | Run the use case, streaming one JSON event per step |
| `GET /workflows/{id}/records.csv` | The workflow's record file |
| `GET /reference` | Use case rollup, step kinds, and shared vocabulary |
| `GET /approvals`, `POST /approvals/{id}/resolve` | The human-in-the-loop queue |
| `GET /repository/documents`, `GET /repository/documents/{id}/download` | Folder contents and archived files |
| `POST /token`, `POST /users`, `GET /users/me` | Auth and user management |
| `POST /session/resolve`, `GET /roles`, `GET /admin/users`, `PATCH /admin/users/{id}` | Session role resolution and administration |
| `POST /documents/upload` | Ingest into the repository |
| `GET /leases/expired` | Lease expiration scan |
| `GET /folders/{division}/{folder}/documents` | Folder contents (authenticated) |

> Document grading no longer has its own endpoint. Attaching a file to
> `POST /workflows/{id}/run` grades it as that workflow's `analysis` step, so there is one
> path to a verdict rather than two that can diverge. `/phase2/email-ingestion` is gone —
> Phase 2 ingestion is not built, and a placeholder endpoint said otherwise.

### Frontend
Single-page app in `static/` (`index.html`, `app.js`, `style.css`) — no build step. It
implements the four screens described under "Frontend Redesign" above: login/division,
division dashboard, use case detail, reference. The use case shell is written once and
reused for every use case; a new use case needs no new frontend code. Dark mode follows
system preference and persists.

### Configuration
`ANTHROPIC_API_KEY` is required to grade an attached document; `ANTHROPIC_MODEL` defaults
to `claude-opus-5`. See `.env.example`. Without a credential the dashboard and the run
footer both say so up front, attaching a file returns 503 with an actionable message, and
a run with no attachment still works — it reports on what is already on file.

### Testing
`python -m pytest tests/ -q` — 55 tests, no API key required and no model call made.
Coverage is on the definition seed/edit/reset cycle, the required-document check, records
and the approvals queue (`test_workflow_repo.py`), the run engine including that an edited
definition changes what the run does (`test_workflow_runner.py`), and roles/permissions
(`test_access_control.py`).

Sample documents in `sample_docs/`:

- `vendor_coi_brightline.txt` — a certificate of insurance whose general liability limit is
  $1M against the rubric's $2M requirement, and which names AAT as certificate holder rather
  than additional insured. Both come back short, so the rejection path is exercisable.

## Known Gaps
- `/dashboard/summary`, `/workflows/*`, `/reference` and `/approvals` are unauthenticated so
  the preview UI can reach them. Gate them behind `get_current_active_user` before exposing
  beyond localhost — `POST /workflows/{id}/run` accepts uploads and spends API tokens.
- UI login is simulated: any password is accepted and no token is issued. The username is
  resolved server-side to a real account, so the *role* is real even though the session is
  not; the authenticated endpoints are not exercised by the frontend.
- The API endpoints and the frontend have no automated coverage; the backend logic does.
- An existing `aat_system.db` keeps the now-unused `dar_reports`, `dar_incidents` and
  `workflow_sops` tables — `create_all` does not drop tables. Delete the file for a clean
  schema; nothing reads them.
- Uploading into the repository still requires a token, so in practice documents are seeded
  outside the UI. The dashboard folder view reads them correctly once they are there.
