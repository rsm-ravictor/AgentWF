# AAT System

A multi-division asset and lease management system: tiered access, a centralised
document repository, and use cases that read documents, grade them against an
explicit rubric, and hand every outcome to a person.

`CONTEXT 2.0.md` is the source of truth for what this repo is and how it is
structured. This file is the short version of how to run it.

## The shape of the app

The screens, in the order a user meets them:

1. **Login** — company name, then a division: Residential/Multifamily or Office/Retail.
   The division scopes every folder, use case and record from that point on.
2. **Division dashboard** — the folders documents live in, and one tile per use
   case. Every tile is the same shape whatever the use case does.
3. **Use case detail** — a persistent top bar to jump between use cases, then a
   fixed layout: the workflow diagram on the left (2/3, with an enlarge overlay),
   the written walkthrough on the right (1/3, editable — the diagram redraws from
   your edits as you type), and a run footer with a live status bar and the outcome.
4. **Reference** — a rollup of every use case in the division, the change log of
   workflow definitions (every version, what changed, and a roll-back action), and
   the shared vocabulary their narratives are written against.
5. **Settings** (user menu) — accounts and access together: create a profile with
   the role it holds, manage the roster, and set what every role is allowed to do.
   Roles are fixed in code; what a role grants is data, and every gate reads it.

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
- `aat_system/llm_analyzer.py` — per-workflow rubrics and the structured-output call to Claude.
- `aat_system/approval_repo.py` — the human-in-the-loop queue.
- `aat_system/user_repo.py`, `auth.py`, `security.py` — roster, role scope, tokens.
- `aat_system/permission_repo.py` — what each role grants, editable at runtime by the Permissions page.
- `aat_system/document_repo.py`, `redaction.py` — ingestion, redaction, lease scanning.
- `aat_system/main.py` — the FastAPI app.
- `static/` — the UI. No build step: `index.html`, `app.js`, `style.css`.

## Getting started

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # then set ANTHROPIC_API_KEY to grade documents
uvicorn aat_system.main:app --reload
```

Open http://127.0.0.1:8000/. Any password works in this prototype; the
**username sets the access level**, and the login screen lists the seeded
accounts to sign in as one click each — `admin@aat.com` (super user),
`sysadmin@aat.com` (administrator), `head.mf@aat.com` (division head),
`owner@aat.com`, `reviewer@aat.com`, `agent@aat.com` — plus a **test account per
role** under their own heading (`test.super@`, `test.admin@`, `test.head@`,
`test.owner@`, `test.reviewer@`, `test.agent@`, `test.retail@`). Anything else is
provisioned as an Agent.

Editing a workflow definition needs `edit_workflow`, which by default only
Division head, Administrator and Super user hold. Two ways past a disabled
**Edit** button: sign in as one of those, or open **Settings → Role permissions**
and grant the capability to the role you are using — that section writes what the
app actually gates on, and your session picks the change up immediately.

Without an API key the app still runs: a run with no attachment reports on what
is already on file, and the UI says up front that grading is unavailable.

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
