"""Workflow catalog, standing instructions, and record-keeping.

Three things live here that the Workflows page needs and that were previously
either hard-coded in the frontend or absent entirely:

1. **The catalog** — which folder a workflow reads, and which documents it
   requires. The frontend used to own this list, which meant the "required
   documents" checklist could not be checked against anything real. Here each
   required document carries the filename keywords that count as a match, so
   present/missing is a deterministic answer rather than a guess.

2. **Standing instructions (SOP)** — what the agent does every time the workflow
   runs: inputs expected, steps taken, pass/fail logic, escalation rules. Seeded
   from the defaults below on first read, then editable and persisted, so
   changing them does not mean changing code.

3. **Records** — one row per signed-off run, which is what "rows logged / last
   updated" on the mini-dashboard reports, and what the workflow's record file
   exports.
"""

import csv
import io
from datetime import datetime
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import Division
from .models import Document, Folder, WorkflowRecord, WorkflowSop

# Extensions that count as a "record file" a user would want to open from the
# workflow view rather than treat as evidence to be graded.
RECORD_EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".csv", ".ods")

AAT_REQUIREMENTS_FOLDER = "AAT Company Requirements/Documents"

WORKFLOW_CATALOG = {
    "vendor-insurance": {
        "title": "Vendor Insurance",
        "folder": "Vendor Insurances",
        "steps": ["Fetch docs", "Redact", "Compare", "Verdict", "Store"],
        "documents": [
            {"name": "Vendor insurance certificate", "match": ["coi", "certificate", "insurance"]},
            {
                "name": "AAT requirements document",
                "match": ["requirement", "aat"],
                "folder": AAT_REQUIREMENTS_FOLDER,
            },
        ],
    },
    "renters-insurance": {
        "title": "Renter's Insurance",
        "folder": "Renters Insurance",
        "steps": ["Fetch lease", "Build checklist", "Compare submission", "Approve / email", "Store"],
        "documents": [
            {"name": "Lease agreement", "match": ["lease"], "folder": "Lease Agreements"},
            {"name": "Tenant checklist", "match": ["checklist"], "folder": "Checklists"},
            {"name": "Submitted insurance policy", "match": ["policy", "insurance", "renter"]},
        ],
    },
    "lease-checklist": {
        "title": "Lease & File Checklist",
        "folder": "Lease Agreements",
        "steps": ["Prepare docs", "Build checklist", "Verify received", "Sign-off queue", "Archive"],
        "documents": [
            {"name": "Lease agreement", "match": ["lease"]},
            {"name": "Addenda / riders", "match": ["addend", "rider", "amendment"]},
            {"name": "File checklist", "match": ["checklist"], "folder": "Checklists"},
        ],
    },
    "breach-notice": {
        "title": "Breach Notice",
        "folder": "Breach Agreement Notices",
        "steps": ["Retrieve lease", "Draft notice", "Check history", "Mgmt review", "Log breach"],
        "documents": [
            {"name": "Tenant lease", "match": ["lease"], "folder": "Lease Agreements"},
            {"name": "Violation report", "match": ["violation", "incident", "activity"]},
            {"name": "Prior breach history", "match": ["breach", "notice", "history"]},
        ],
    },
    "security-report": {
        "title": "Daily Activity Report",
        "folder": "Daily Activity Reports",
        "steps": ["Read report", "Find highlights", "Extract incidents", "Group by unit", "Triage"],
        "documents": [
            {"name": "Daily activity report (PDF or image, highlighting intact)", "match": ["dar", "activity", "report"]},
        ],
    },
}

# Seeded on first read of a workflow's SOP, then owned by whoever edits it.
DEFAULT_SOPS = {
    "vendor-insurance": {
        "inputs_expected": (
            "A vendor certificate of insurance (COI) as a PDF or image, and the current AAT "
            "requirements document for the division. Property ID identifies which site the "
            "vendor is working at."
        ),
        "steps_taken": (
            "1. Pull the vendor's COI and the AAT requirements document from the repository.\n"
            "2. Redact identifiers before the document is read.\n"
            "3. Grade the COI against every requirement — coverage types, limits, additional "
            "insured wording, and the policy period.\n"
            "4. Record the policy number, carrier, limits and expiry as extracted fields.\n"
            "5. Queue the verdict for a human, then file it."
        ),
        "pass_fail_logic": (
            "PASS only when every requirement is met with a supporting quote from the document. "
            "Limits below the AAT minimum FAIL. AAT named as certificate holder rather than "
            "additional insured FAILS. An expired or not-yet-effective policy period FAILS. "
            "Anything ambiguous or within 10% of a threshold routes to human review rather than "
            "being cleared."
        ),
        "escalation_rules": (
            "A failed COI for a vendor already on site is escalated to the division head the "
            "same day. Expired coverage is escalated immediately. Otherwise send the vendor a "
            "corrective request and re-check on resubmission."
        ),
    },
    "renters-insurance": {
        "inputs_expected": (
            "The tenant's executed lease, the checklist generated from it, and the policy the "
            "tenant submitted. Property ID and unit number identify the tenancy."
        ),
        "steps_taken": (
            "1. Read the lease and generate the tenant checklist of required coverage.\n"
            "2. Send the checklist to the tenant.\n"
            "3. Compare the submitted policy line by line against the checklist.\n"
            "4. Approve if compliant, or draft a corrective email naming each gap.\n"
            "5. File the outcome against the unit."
        ),
        "pass_fail_logic": (
            "PASS when liability limits meet or exceed the lease minimum, AAT is listed as "
            "additional insured or interested party as the lease requires, and the policy period "
            "covers the lease term. A missing additional-insured endorsement FAILS. A policy "
            "expiring before the lease ends FAILS."
        ),
        "escalation_rules": (
            "Two rejected submissions from the same tenant escalate to the property manager. "
            "A tenant occupying a unit with no policy on file at all escalates immediately — "
            "that is a lease breach, not a paperwork gap."
        ),
    },
    "lease-checklist": {
        "inputs_expected": (
            "The lease agreement, every addendum and rider attached to it, and the file "
            "checklist for the division."
        ),
        "steps_taken": (
            "1. Assemble the lease and all addenda.\n"
            "2. Build the file checklist for this lease type.\n"
            "3. Verify each required document is present, signed, and dated.\n"
            "4. Match signature and date fields against the lease term.\n"
            "5. Queue for human sign-off, then archive the complete file."
        ),
        "pass_fail_logic": (
            "PASS only when every checklist line is received, executed by all parties, and "
            "dated. An unsigned page, a missing initial, or an addendum referenced in the lease "
            "but absent from the file FAILS. Nothing archives on the agent's own authority."
        ),
        "escalation_rules": (
            "A file still incomplete 5 business days after move-in escalates to the division "
            "head. A missing signature on the lease itself escalates immediately."
        ),
    },
    "breach-notice": {
        "inputs_expected": (
            "The tenant's lease, the violation report or DAR incident that triggered this, and "
            "the unit's prior breach history."
        ),
        "steps_taken": (
            "1. Retrieve the lease for the unit.\n"
            "2. Draft the notice citing the specific lease sections breached.\n"
            "3. Check prior breaches for the unit and include the history.\n"
            "4. Queue the drafted notice for management review — it is never sent automatically.\n"
            "5. Log the breach so it counts toward the next occurrence."
        ),
        "pass_fail_logic": (
            "A notice is ready to send only when it cites a specific lease section by number, "
            "describes the conduct with a date, and states the cure period the lease allows. "
            "A draft that cites no section, or asserts a breach the lease does not cover, is "
            "held for a human to rewrite."
        ),
        "escalation_rules": (
            "Third documented violation escalates to the division head before sending. Anything "
            "involving safety, weapons, or threats escalates immediately regardless of count and "
            "does not wait for the notice cycle."
        ),
    },
    "security-report": {
        "inputs_expected": (
            "A Daily Activity Report as the original PDF or image with highlighting intact. "
            "Plain-text exports lose the colour and degrade the triage."
        ),
        "steps_taken": (
            "1. Read the report natively so highlight colours survive — never through text "
            "extraction.\n"
            "2. Pull every highlighted row as an incident, with unit, date, time and category.\n"
            "3. Group incidents by unit and count occurrences across all stored reports.\n"
            "4. Apply triage by colour and recurrence.\n"
            "5. Store to the incident log so the first-violation date holds across weeks."
        ),
        "pass_fail_logic": (
            "Red highlight = severe, escalate. Yellow = watch, logged, and escalates "
            "automatically on recurrence. No highlight = routine patrol activity, skipped "
            "unless the text plainly describes a violation. If no highlighting is detected at "
            "all, every row describing a violation is extracted and the report is flagged as "
            "un-triaged rather than returning nothing."
        ),
        "escalation_rules": (
            "Red incidents go to management the same day. A yellow incident on a unit that "
            "already has one becomes an escalation. Lease-relevant incidents feed the Breach "
            "Notice workflow."
        ),
    },
}

SOP_FIELDS = ("inputs_expected", "steps_taken", "pass_fail_logic", "escalation_rules")


def catalog() -> List[dict]:
    """Workflow definitions, for the frontend to render against."""
    return [
        {
            "id": wf_id,
            "title": wf["title"],
            "folder": wf["folder"],
            "steps": wf["steps"],
            "documents": [
                {"name": d["name"], "folder": d.get("folder", wf["folder"])} for d in wf["documents"]
            ],
        }
        for wf_id, wf in WORKFLOW_CATALOG.items()
    ]


# ---------------- Standing instructions ----------------

def get_sop(db: Session, workflow_id: str, division: Division) -> dict:
    """The workflow's standing instructions, seeding defaults on first read."""
    if workflow_id not in WORKFLOW_CATALOG:
        raise ValueError(f"Unknown workflow '{workflow_id}'")

    sop = (
        db.query(WorkflowSop)
        .filter(WorkflowSop.workflow_id == workflow_id, WorkflowSop.division == division)
        .first()
    )
    if sop is None:
        defaults = DEFAULT_SOPS.get(workflow_id, {})
        sop = WorkflowSop(
            workflow_id=workflow_id,
            division=division,
            updated_by="AAT default",
            **{f: defaults.get(f, "") for f in SOP_FIELDS},
        )
        db.add(sop)
        db.commit()
        db.refresh(sop)
    return _sop_dict(sop)


def update_sop(
    db: Session,
    workflow_id: str,
    division: Division,
    updates: dict,
    updated_by: Optional[str] = None,
) -> dict:
    """Overwrite the fields supplied; leave the rest as they were."""
    if workflow_id not in WORKFLOW_CATALOG:
        raise ValueError(f"Unknown workflow '{workflow_id}'")

    get_sop(db, workflow_id, division)  # ensure the row exists
    sop = (
        db.query(WorkflowSop)
        .filter(WorkflowSop.workflow_id == workflow_id, WorkflowSop.division == division)
        .first()
    )
    for field in SOP_FIELDS:
        if field in updates and updates[field] is not None:
            setattr(sop, field, updates[field])
    sop.updated_by = updated_by or sop.updated_by
    sop.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(sop)
    return _sop_dict(sop)


def reset_sop(db: Session, workflow_id: str, division: Division, updated_by: Optional[str] = None) -> dict:
    """Restore the shipped defaults — an escape hatch after a bad edit."""
    defaults = DEFAULT_SOPS.get(workflow_id, {})
    return update_sop(
        db,
        workflow_id,
        division,
        {f: defaults.get(f, "") for f in SOP_FIELDS},
        updated_by=updated_by,
    )


def _sop_dict(sop: WorkflowSop) -> dict:
    defaults = DEFAULT_SOPS.get(sop.workflow_id, {})
    return {
        "workflow_id": sop.workflow_id,
        "division": sop.division.value,
        "inputs_expected": sop.inputs_expected or "",
        "steps_taken": sop.steps_taken or "",
        "pass_fail_logic": sop.pass_fail_logic or "",
        "escalation_rules": sop.escalation_rules or "",
        "updated_at": sop.updated_at.isoformat() if sop.updated_at else None,
        "updated_by": sop.updated_by,
        "is_default": all((getattr(sop, f) or "") == defaults.get(f, "") for f in SOP_FIELDS),
    }


# ---------------- Records ----------------

def log_record(
    db: Session,
    workflow_id: str,
    division: Division,
    outcome: str,
    property_id: str = "",
    unit: str = "",
    subject: str = "",
    decision_note: str = "",
    document_name: str = "",
    recorded_by: str = "",
) -> WorkflowRecord:
    """Write one record-keeping row for a completed run."""
    record = WorkflowRecord(
        workflow_id=workflow_id,
        division=division,
        property_id=property_id or None,
        unit=unit or None,
        subject=subject or None,
        outcome=outcome,
        decision_note=decision_note or None,
        document_name=document_name or None,
        recorded_by=recorded_by or None,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def list_records(db: Session, workflow_id: str, division: Division, limit: int = 200) -> List[dict]:
    rows = (
        db.query(WorkflowRecord)
        .filter(WorkflowRecord.workflow_id == workflow_id, WorkflowRecord.division == division)
        .order_by(WorkflowRecord.recorded_at.desc(), WorkflowRecord.id.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": r.id,
            "property_id": r.property_id or "",
            "unit": r.unit or "",
            "subject": r.subject or "",
            "outcome": r.outcome,
            "decision_note": r.decision_note or "",
            "document_name": r.document_name or "",
            "recorded_by": r.recorded_by or "",
            "recorded_at": r.recorded_at.isoformat() if r.recorded_at else None,
        }
        for r in rows
    ]


def record_summary(db: Session, workflow_id: str, division: Division) -> dict:
    """Rows logged and when they last moved — the mini-dashboard's numbers."""
    base = db.query(WorkflowRecord).filter(
        WorkflowRecord.workflow_id == workflow_id, WorkflowRecord.division == division
    )
    total = base.count()
    last = base.order_by(WorkflowRecord.recorded_at.desc(), WorkflowRecord.id.desc()).first()
    by_outcome = dict(
        db.query(WorkflowRecord.outcome, func.count(WorkflowRecord.id))
        .filter(WorkflowRecord.workflow_id == workflow_id, WorkflowRecord.division == division)
        .group_by(WorkflowRecord.outcome)
        .all()
    )
    return {
        "rows_logged": total,
        "last_updated": last.recorded_at.isoformat() if last and last.recorded_at else None,
        "last_updated_by": last.recorded_by if last else None,
        "last_subject": (last.subject or last.property_id or "") if last else "",
        "by_outcome": by_outcome,
    }


def records_csv(db: Session, workflow_id: str, division: Division) -> str:
    """The workflow's record file, as CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(
        ["Recorded at", "Property", "Unit", "Subject", "Outcome", "Document", "Recorded by", "Note"]
    )
    for r in list_records(db, workflow_id, division, limit=5000):
        writer.writerow(
            [
                r["recorded_at"] or "",
                r["property_id"],
                r["unit"],
                r["subject"],
                r["outcome"],
                r["document_name"],
                r["recorded_by"],
                r["decision_note"],
            ]
        )
    return buffer.getvalue()


# ---------------- Documents ----------------

def _folder_documents(db: Session, division: Division, folder_name: str) -> List[Document]:
    return (
        db.query(Document)
        .join(Folder)
        .filter(Folder.division == division, Folder.name == folder_name)
        .order_by(Document.uploaded_at.desc())
        .all()
    )


def required_documents(db: Session, workflow_id: str, division: Division) -> dict:
    """Per-required-document present/missing, with the file that satisfied it.

    Matching is on filename keywords declared in the catalog, so the answer is
    reproducible and the UI can show *which* file counted.
    """
    wf = WORKFLOW_CATALOG.get(workflow_id)
    if not wf:
        raise ValueError(f"Unknown workflow '{workflow_id}'")

    cache: dict = {}
    items = []
    for spec in wf["documents"]:
        folder_name = spec.get("folder", wf["folder"])
        if folder_name not in cache:
            cache[folder_name] = _folder_documents(db, division, folder_name)

        match = None
        for doc in cache[folder_name]:
            lowered = (doc.filename or "").lower()
            if any(keyword in lowered for keyword in spec["match"]):
                match = doc
                break

        items.append(
            {
                "name": spec["name"],
                "folder": folder_name,
                "present": match is not None,
                "matched_document": (
                    {
                        "id": match.id,
                        "filename": match.filename,
                        "uploaded_at": match.uploaded_at.isoformat() if match.uploaded_at else None,
                        "redacted": match.redacted_at is not None,
                    }
                    if match
                    else None
                ),
            }
        )

    present = sum(1 for i in items if i["present"])
    return {
        "items": items,
        "present": present,
        "total": len(items),
        "missing": [i["name"] for i in items if not i["present"]],
    }


def record_files(db: Session, workflow_id: str, division: Division) -> List[dict]:
    """Spreadsheets and record files tied to this workflow, openable in place.

    The generated record file is always listed first — it is derived from
    workflow_records and therefore always current. Real uploads follow.
    """
    wf = WORKFLOW_CATALOG.get(workflow_id)
    if not wf:
        raise ValueError(f"Unknown workflow '{workflow_id}'")

    summary = record_summary(db, workflow_id, division)
    files = [
        {
            "kind": "generated",
            "name": f"{workflow_id}-records.csv",
            "label": "Workflow record log",
            "rows": summary["rows_logged"],
            "updated_at": summary["last_updated"],
            "url": f"/workflows/{workflow_id}/records.csv?division={_division_key(division)}",
        }
    ]

    if workflow_id == "security-report":
        files.append(
            {
                "kind": "generated",
                "name": "incident-register.csv",
                "label": "Incident register (all reports)",
                "rows": None,
                "updated_at": None,
                "url": "/dar/register.csv",
            }
        )

    folders = {wf["folder"]} | {d.get("folder", wf["folder"]) for d in wf["documents"]}
    for folder_name in sorted(folders):
        for doc in _folder_documents(db, division, folder_name):
            if not (doc.filename or "").lower().endswith(RECORD_EXTENSIONS):
                continue
            files.append(
                {
                    "kind": "uploaded",
                    "name": doc.filename,
                    "label": folder_name,
                    "rows": None,
                    "updated_at": doc.uploaded_at.isoformat() if doc.uploaded_at else None,
                    "url": f"/repository/documents/{doc.id}/download",
                }
            )
    return files


def _division_key(division: Division) -> str:
    return "retail" if division == Division.OFFICE else "mf"
