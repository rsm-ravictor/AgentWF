# AAT System

A scaffold for a multi-division asset and lease management system with tiered access, document redaction, repository tracking, and email ingestion.

## Architecture

- `aat_system/config.py` — shared constants, folder mappings, division definitions, folder names.
- `aat_system/db.py` — SQLAlchemy database setup and session management.
- `aat_system/models.py` — Users, divisions, documents, leases, folders, and breach logs.
- `aat_system/auth.py` — role-based access control for division heads and subgroup owners.
- `aat_system/redaction.py` — redaction layer for PDFs and uploads before repository ingestion.
- `aat_system/document_repo.py` — centralized repository logic, lease monitoring, and folder sorting.
- `aat_system/email_agent.py` — Phase 2 IMAP inbox scanning, PDF detection, redaction, and folder routing.
- `aat_system/workflows.py` — workflows for vendor insurance, renter’s insurance, lease checklists, breach notices, and security reports.
- `aat_system/main.py` — FastAPI app and CLI helpers.

## Getting Started

1. Create a virtual environment and install dependencies:

```bash
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
```

2. Copy `.env.example` to `.env` and update configuration values.

3. Initialize the database and run the API:

```bash
python -m aat_system.main --init-db
uvicorn aat_system.main:app --reload
```

4. Open the preview page in your browser:

```bash
http://127.0.0.1:8000/
```

## Key Features

- Division-aware role permissions for `Office/Retail` and `Multifamily/Residential`.
- Document upload redaction before repository ingestion, with manual upload access controlled by user role.
- Lease expiration scanning and flagging.
- Placeholder UI support for Phase 2 email ingestion; current workflow relies on manual uploads to the required folders.
- External service API key support for decision-making workflows and downstream agent integrations.
- Phase 1 workflows for Multifamily/Residential built around document-driven automation.
- Phase 2 note: the Office/Retail division reuses the same architecture with division-specific folder mappings.

## Notes

- The redaction implementation is designed as a layering point. Extend `aat_system/redaction.py` for content-level PDF redaction rules and sensitive data patterns.
- Document routing is based on configured folder keywords and can be adapted for division-specific folder sets.
