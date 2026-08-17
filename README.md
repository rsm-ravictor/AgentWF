# AAT System

A multi-division asset and lease management system: tiered access, a centralised
document repository, and use cases that read documents, grade them against an
explicit rubric, and hand every outcome to a person.

`CONTEXT 2.0.md` is the source of truth for what this repo is and how it is
structured. This file is the short version of how to run it.

## The shape of the app

The screens, in the order a user meets them:

1. **Login** — company name, then a division: Residential/Multifamily, Office/Retail
   or Construction. The division scopes every folder, use case, record and account
   from that point on, and the accounts offered below the form are that division's.
2. **Division dashboard** — the folders documents live in, and one tile per use
   case. Every tile is the same shape whatever the use case does.
3. **Use case detail** — a persistent top bar to jump between use cases, then a
   fixed layout: the workflow diagram on the left (2/3, with an enlarge overlay),
   the written walkthrough on the right (1/3, editable — the diagram redraws from
   your edits as you type), and a run footer with a live status bar and the outcome.
4. **Reference** — a rollup of every use case in the division, the change log of
   workflow definitions (every version, what changed, and a roll-back action), and
   the shared vocabulary their narratives are written against.
5. **Settings** (user menu) — accounts and access together, per division: create a
   profile with the level it holds, manage the roster, and set what every level is
   allowed to do. Levels are fixed in code; what a level grants is data, keyed by
   division, and every gate reads it.

## Divisions and levels

Three business lines — Residential/Multifamily, Office/Retail, Construction — each
with **its own super admin**. The title belongs to a division, not the company, so
Residential's super admin has no reach into Construction's work.

Three levels, held per division:

| Level | Sees | Can |
| --- | --- | --- |
| **Super admin** | everything in their division | run, approve, edit use cases, manage people and permissions |
| **Admin** | their division, including every general user's activity | run, approve, edit use cases, manage people |
| **General** | their own work only | run use cases, upload documents |

Every level can run use cases. `view_all_divisions` is off for everyone by default,
super admins included, so crossing a division boundary is a deliberate grant.

Adding a business line is one entry in `Division`, one folder list in
`DIVISION_FOLDER_MAPPING` and one key in `DIVISION_KEYS` — its levels, permission
matrix, folders and workflow definitions follow from those.

The use case shell is built once and reused. What differs per use case is only
its definition, so adding one takes no new frontend code.

## One definition, three views

A workflow definition is an ordered list of steps (`workflow_steps`). The
diagram, the narrative, and the run all render from those same rows, and editing
the narrative rewrites them — the diagram follows your edits as you type. That is
what stops the picture, the words and the execution drifting into three different
answers.

Because that definition is also what a run executes, every version of it is kept
in `workflow_revisions`: the Reference page lists what changed, who changed it and
when, and rolls any past version back as the live one. A rollback is recorded as a
new version, not a rewind, so it can be undone too.

Each step carries a `kind`, which colours its node and decides what the runner
does when it reaches it:

| kind | what happens at that step |
| --- | --- |
| `intake` | Check the required documents against the repository |
| `analysis` | Grade an attached document against the rubric, or report what is on file |
| `decision` | Apply the pass rule to everything gathered so far |
| `human` | Queue an approval case when the run could not clear on its own |
| `record` | Write the run to the workflow's record file |
| `note` | Descriptive only — reported, but takes no action |

## Modules

- `aat_system/config.py` — divisions, roles, permissions, folder names, paths.
- `aat_system/models.py` — users, folders, documents, leases, approvals, workflow steps, revisions and records.
- `aat_system/workflow_repo.py` — the use case catalog, definitions, revision history, required documents, records, glossary.
- `aat_system/workflow_runner.py` — executes a use case step by step, yielding one event per state change.
- `aat_system/llm_analyzer.py` — per-workflow rubrics and the graded verdict behind each decision.
- `aat_system/connect.py` — the one LLM entry point: TritonAI's OpenAI-compatible proxy (`ask`, `ask_json`, `list_models`).
- `aat_system/approval_repo.py` — the human-in-the-loop queue.
- `aat_system/user_repo.py`, `auth.py`, `security.py` — roster, role scope, tokens.
- `aat_system/permission_repo.py` — what each role grants, editable at runtime by the Permissions page.
- `aat_system/document_repo.py`, `redaction.py` — ingestion, redaction, lease scanning.
- `aat_system/main.py` — the FastAPI app.
- `static/` — the UI. No build step: `index.html`, `app.js`, `style.css`.

## Look and feel

Corporate and structured, to sit beside
[americanassetstrust.com](https://www.americanassetstrust.com/): white page, navy
`#1B2A4A` brand, bordered panels with a slight lift, a substantial top nav, bold
headers over regular body text, and their `"Open Sans", "Helvetica Neue"` stack.
Dark mode is a deep navy charcoal `#12161F`, never pure black.

Status follows dashboard convention — navy in progress, green complete, amber
needs review. In the **workflow diagrams**, colour marks a step's position in the
sequence (six muted tones, cycled), connectors stay neutral, and two signals layer
on top without changing a node's colour: an amber outline where a person is needed,
and a status ring once a run is live. Label ink is computed per node from the fill's
luminance, so text stays readable in both modes.

Every colour is a design token at the top of `style.css` — nothing is styled
per screen. Light and dark are the same design inverted; dark follows the system
by default (no `data-theme` attribute is written until you use the toggle). The
one place colour carries meaning on its own is live run status: running, done and
needs-review stay legible at a glance in both modes.

## Getting started

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then set ANTHROPIC_API_KEY to grade documents
uvicorn aat_system.main:app --reload
```

Open http://127.0.0.1:8000/. Any password works in this prototype; the
**username sets the access level**. Pick a division, and the login screen lists
that division's accounts to sign in as with one click:

| Division | Super admin | Admin | General |
| --- | --- | --- | --- |
| Residential | `super.residential@aat.com` | `admin.residential@aat.com` | `user.residential@aat.com` |
| Office/Retail | `super.retail@aat.com` | `admin.retail@aat.com` | `user.retail@aat.com` |
| Construction | `super.construction@aat.com` | `admin.construction@aat.com` | `user.construction@aat.com` |

Each also has a test account under its own heading — `test.super.construction@aat.com`
and so on, one per level per division. Any other email is provisioned as a General
user in the division being signed into.

**Creating a profile** is in Settings → Profiles. It needs `manage_users`, which only
Admin and Super admin hold by default — if the form is disabled, the **Give my level
this** button beside it grants the permission to your own level and unlocks the form
on the spot.

Editing a workflow definition needs `edit_workflow`, which by default only Admin and
Super admin hold. Two ways past a disabled **Edit** button: sign in at one of those
levels, or open **Settings → Role permissions**, pick the division, and grant the
capability to the level you are using — that section writes what the app actually
gates on, and your session picks the change up immediately.

Because the roles changed shape, an `aat_system.db` from before this change stores
level names that no longer exist. Delete the file and let startup rebuild it.

Without an API key the app still runs: a run with no attachment reports on what
is already on file, and the UI names the missing variable up front.

## Where decisions get made

Attaching a document to a run grades it against that use case's rubric and returns a
validated verdict — approve / needs human review / reject, with per-requirement
findings and evidence. `LLM_PROVIDER` chooses the route:

- **`tritonai`** (default) — UCSD's OpenAI-compatible proxy through
  `aat_system/connect.py`. Needs `TRITONAI_API_KEY` from
  <https://tritonai-api.ucsd.edu/>; the model is `TRITONAI_MODEL`
  (`claude-opus-4-6-v1`). Switching models is that one variable. Reads text and
  text-based PDFs.
- **`anthropic`** — the Anthropic SDK directly. Needs `ANTHROPIC_API_KEY`. Reads PDFs
  and images natively and has the API enforce the response schema, so use it for scans.

Every model call goes through `connect.py` — one client, no second path. Neither route
falls back to another model: an unknown or unauthorised model raises and the run says so.

## Tests

```bash
python -m pytest tests/ -q
```

No API key needed — no test makes a model call.

## Notes

- Documents are read from and written to the local filesystem
  (`UPLOAD_ROOT`, `REDACTED_ROOT`, `ARCHIVE_ROOT`), not a cloud bucket.
- Redaction runs before repository ingestion. Extend `redaction.py` for
  content-level PDF rules.
- Phase 2 (Office/Retail) reuses this architecture with division-specific folder
  mappings rather than a separate build. Email inbox ingestion is Phase 2;
  Phase 1 relies on manual upload.
