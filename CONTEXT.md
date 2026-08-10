# AAT System Context

## Purpose
This project is a scaffold for an asset and lease management system with tiered access permissions, centralized document repository monitoring, redaction processing, and workflow automation.

## Business Divisions
- **Multifamily/Residential**
- **Office/Retail**

Each division has a division head with full access to all workflows and folders within their division. Subgroup owners have limited, role-specific access to assigned folders and workflows.

## Core Requirements
- Centralized repository that tracks all leases and flags expired leases.
- Redaction layer applied before any documents enter the repository or are used by agents.
- Manual folder upload support in Phase 1, with email ingestion deferred to Phase 2.
- A Phase 2 UI placeholder should exist for future email ingestion and automated sorting, but it is not required to work in the prototype.
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

## Office/Retail Phase 2
- Mirror Phase 1 with the same core structure and automation patterns.
- Adapt folder mappings, document sources, and division-specific requirements for Office/Retail.

## Implementation Notes
- The current scaffold includes FastAPI, SQLAlchemy, document redaction, folder-based routing, and workflow stubs.
- The redaction layer is designed as a pre-processing step for PDFs and uploaded files.
- Phase 2 reuses the same architecture with division-specific adjustments rather than a separate rebuild.
- Email inbox ingestion is a Phase 2 capability; Phase 1 relies on manual upload and folder permission controls.

## Current State (as of 2026-08-10)

### LLM document analysis — real, working
`aat_system/llm_analyzer.py` is where review decisions are actually made. An uploaded
document (PDF, image, or text) is sent to Claude and graded against the rubric for its
workflow. The response shape is enforced by the API through structured outputs against a
Pydantic schema, so it always parses — there is no "please return JSON" prompting, no
regex extraction, and no retry-on-parse-failure loop.

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

### API surface
| Endpoint | Purpose |
| --- | --- |
| `GET /` | Serves the single-page UI |
| `GET /analyze/workflows` | Rubrics per workflow, plus whether a credential is configured |
| `POST /analyze` | Grades an uploaded document against a workflow rubric |
| `POST /token`, `POST /users`, `GET /users/me` | Auth and user management |
| `POST /documents/upload` | Redact and ingest into the repository |
| `GET /leases/expired` | Lease expiration scan |
| `GET /folders/{division}/{folder}/documents` | Folder contents |
| `GET /phase2/email-ingestion` | Phase 2 placeholder |

### Frontend
Single-page app in `static/` (`index.html`, `app.js`, `style.css`) — no build step.
Flow is landing/login → dashboard → per-workflow view. The dashboard carries stat tiles,
a human-in-the-loop approvals queue (each case expands to show why it needs a human, and
deep-links into its workflow), an outstanding/blocked list, and an activity feed. The
workflow view has the rubric, a real file dropzone, a labeled step track, an activity log,
and the Claude analysis panel. Dark mode follows system preference and persists.

### Configuration
`ANTHROPIC_API_KEY` is required for analysis; `ANTHROPIC_MODEL` defaults to `claude-opus-5`.
See `.env.example`. Without a credential the UI warns up front and `POST /analyze` returns
503 with an actionable message rather than failing opaquely.

### Testing
`sample_docs/vendor_coi_brightline.txt` is a certificate of insurance whose general
liability limit is $1M against the rubric's $2M requirement, and which names AAT as
certificate holder rather than additional insured. Both should come back short, so the
rejection path is exercisable without supplying a real document.

## Known Gaps
- `POST /analyze` is unauthenticated so the preview UI can reach it. Gate it behind
  `get_current_active_user` before exposing beyond localhost — it accepts uploads and
  spends API tokens.
- UI login is simulated: any credentials are accepted and no token is issued, so the
  authenticated endpoints are not exercised by the frontend.
- Dashboard case data, approvals queue, and activity feed are seeded in the frontend, not
  read from the database. Only document analysis hits a real backend.
- Redaction runs on the repository ingestion path (`POST /documents/upload`), not on the
  analysis path — documents sent to the LLM are not redacted first. Decide whether
  analysis should sit behind redaction before handling live tenant data.
- `redact_pdf` adds a text annotation rather than removing the underlying content; it is a
  layering point, not a real redaction implementation.
- No automated test suite.
