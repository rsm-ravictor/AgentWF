"""Workflow definitions, required documents, and record-keeping.

A workflow definition is an ordered list of steps. That list is the only source
of truth behind the use case page: the colour-coded diagram on the left, the
narrative walkthrough on the right, and the track the runner walks through are
all rendered from the same rows. Editing the narrative rewrites the rows, so the
three views cannot drift into three different answers.

Also here:

* **The catalog** — which folder a workflow reads and which documents it needs.
  Each required document declares the filename keywords that count as a match,
  so present/missing is a deterministic answer rather than a guess.
* **Records** — one row per completed run, exported as the workflow's record
  file and rolled up on the reference page.
"""

import csv
import io
import re
from datetime import datetime
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import Division
from .models import Document, Folder, WorkflowRecord, WorkflowStep

# Extensions that count as a "record file" a user would want to open rather than
# treat as evidence to be graded.
RECORD_EXTENSIONS = (".xlsx", ".xlsm", ".xls", ".csv", ".ods")

AAT_REQUIREMENTS_FOLDER = "AAT Company Requirements/Documents"

# Step kinds. The kind colours the node in the diagram and decides what the
# runner actually does when it reaches that step (see workflow_runner).
STEP_KINDS = {
    "intake": "Gathers the documents the workflow needs",
    "analysis": "Reads and grades what was gathered",
    "decision": "Applies the pass/fail rule",
    "human": "Hands the outcome to a person",
    "record": "Writes the result to the record file",
    "note": "Descriptive step with no automated action",
}

# ---------------------------------------------------------------------------
# Catalog
#
# The use cases below are the current Phase 1 Multifamily set. They are expected
# to change; the page shell that renders them is not. Adding a use case here is
# all it takes for it to appear on the dashboard, in the top bar, and on the
# reference page.
# ---------------------------------------------------------------------------

WORKFLOW_CATALOG = {
    "vendor-insurance": {
        "title": "Vendor Insurance",
        "folder": "Vendor Insurances",
        "purpose": "Check a vendor's certificate of insurance against AAT's requirements before they work on site.",
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
        "purpose": "Build a tenant's coverage checklist from the lease and grade what the tenant submits against it.",
        "documents": [
            {"name": "Lease agreement", "match": ["lease"], "folder": "Lease Agreements"},
            {"name": "Tenant checklist", "match": ["checklist"], "folder": "Checklists"},
            {"name": "Submitted insurance policy", "match": ["policy", "insurance", "renter"]},
        ],
    },
    "lease-checklist": {
        "title": "Lease & File Checklist",
        "folder": "Lease Agreements",
        "purpose": "Confirm a tenant file is complete, executed and dated before it is archived.",
        "documents": [
            {"name": "Lease agreement", "match": ["lease"]},
            {"name": "Addenda / riders", "match": ["addend", "rider", "amendment"]},
            {"name": "File checklist", "match": ["checklist"], "folder": "Checklists"},
        ],
    },
    "breach-notice": {
        "title": "Breach Notice",
        "folder": "Breach Agreement Notices",
        "purpose": "Draft a notice of breach citing the lease sections violated, with the unit's prior history attached.",
        "documents": [
            {"name": "Tenant lease", "match": ["lease"], "folder": "Lease Agreements"},
            {"name": "Violation report", "match": ["violation", "incident", "activity"]},
            {"name": "Prior breach history", "match": ["breach", "notice", "history"]},
        ],
    },
    "security-report": {
        "title": "Daily Activity Report",
        "folder": "Daily Activity Reports",
        "purpose": "Review a security report, log what was flagged, and raise anything that looks like a lease breach.",
        "documents": [
            {
                "name": "Daily activity report",
                "match": ["dar", "activity", "report"],
            },
        ],
    },
}

# ---------------------------------------------------------------------------
# Default definitions
#
# Seeded into workflow_steps on first read, then owned by whoever edits them.
# Each step carries the narrative text shown beside its node: a one-line summary
# and the bullets describing what happens at that step.
# ---------------------------------------------------------------------------

DEFAULT_STEPS = {
    "vendor-insurance": [
        {
            "title": "Gather the certificate",
            "kind": "intake",
            "summary": "Pull the vendor's COI and the AAT requirements standard in force.",
            "bullets": [
                "Reads the Vendor Insurances folder for the certificate.",
                "Reads AAT Company Requirements/Documents for the standard to grade against.",
                "Records which file satisfied each requirement, so the check is auditable.",
            ],
        },
        {
            "title": "Redact and read",
            "kind": "analysis",
            "summary": "Strip identifiers, then grade the certificate against the rubric.",
            "bullets": [
                "Identifiers are redacted before the document is read.",
                "Every rubric requirement comes back met, not met, or unclear, with a supporting quote.",
                "Policy number, carrier, limits and policy period are extracted as fields.",
            ],
        },
        {
            "title": "Compare to requirements",
            "kind": "decision",
            "summary": "Pass only on a clean sheet; anything near a threshold goes to a human.",
            "bullets": [
                "A general liability limit below the AAT minimum fails.",
                "AAT named as certificate holder rather than additional insured fails.",
                "An expired or not-yet-effective policy period fails.",
                "Anything within 10% of a threshold routes to review rather than clearing.",
            ],
        },
        {
            "title": "Human sign-off",
            "kind": "human",
            "summary": "A person owns the outcome; the agent never clears a vendor itself.",
            "bullets": [
                "Anything not clean is queued as an approval case naming the gap.",
                "A failed COI for a vendor already on site escalates to the division head the same day.",
                "Expired coverage escalates immediately rather than waiting for the next cycle.",
            ],
        },
        {
            "title": "File the outcome",
            "kind": "record",
            "summary": "Write the decision to the workflow's record file.",
            "bullets": [
                "One row per run: property, subject, outcome, and who signed off.",
                "The record file exports as CSV from the reference page.",
                "Cleared certificates stay filed under Vendor Insurances for the next renewal check.",
            ],
        },
    ],
    "renters-insurance": [
        {
            "title": "Read the lease",
            "kind": "intake",
            "summary": "Pull the executed lease and the checklist generated from it.",
            "bullets": [
                "Finds the lease for the unit under Lease Agreements.",
                "Finds the tenant checklist under Checklists.",
                "Property ID and unit number identify which tenancy this run is about.",
            ],
        },
        {
            "title": "Build the checklist",
            "kind": "analysis",
            "summary": "Turn the lease's insurance clause into a checkable list of coverages.",
            "bullets": [
                "Required limits, endorsements and the coverage period come from the lease itself.",
                "The checklist is what gets sent to the tenant, so it is written in plain terms.",
                "The submitted policy is graded line by line against that list.",
            ],
        },
        {
            "title": "Compare the submission",
            "kind": "decision",
            "summary": "Pass when limits, endorsements and dates all clear the lease minimum.",
            "bullets": [
                "Personal liability below the lease minimum fails.",
                "A missing additional-insured endorsement fails.",
                "A policy expiring before the lease term ends fails.",
            ],
        },
        {
            "title": "Approve or correct",
            "kind": "human",
            "summary": "Approve a compliant policy, or queue a corrective email naming each gap.",
            "bullets": [
                "The corrective email is drafted for a person to send, never sent automatically.",
                "Two rejected submissions from the same tenant escalate to the property manager.",
                "An occupied unit with no policy on file at all escalates immediately — that is a lease breach.",
            ],
        },
        {
            "title": "File against the unit",
            "kind": "record",
            "summary": "Log the outcome so the unit's coverage history is continuous.",
            "bullets": [
                "The record row carries the property, unit and decision.",
                "Renewal checks read the same rows rather than starting from scratch.",
            ],
        },
    ],
    "lease-checklist": [
        {
            "title": "Assemble the file",
            "kind": "intake",
            "summary": "Collect the lease, every addendum, and the division's file checklist.",
            "bullets": [
                "Reads Lease Agreements for the lease and its riders.",
                "Reads Checklists for the file checklist that applies to this lease type.",
                "Anything referenced by the lease but absent from the folder is reported as missing.",
            ],
        },
        {
            "title": "Verify each line",
            "kind": "analysis",
            "summary": "Check every checklist line is present, executed and dated.",
            "bullets": [
                "Signature and date fields are matched against the lease term.",
                "Rent, deposit and term dates are extracted for the record.",
                "An unsigned page or a missing initial is reported with the page it is on.",
            ],
        },
        {
            "title": "Complete or incomplete",
            "kind": "decision",
            "summary": "Complete only when every line is received and executed by all parties.",
            "bullets": [
                "A missing addendum referenced in the lease fails the file.",
                "An undated signature fails; a signature block with no counter-signature fails.",
                "Nothing archives on the agent's own authority.",
            ],
        },
        {
            "title": "Queue for sign-off",
            "kind": "human",
            "summary": "A person confirms the file before it is closed.",
            "bullets": [
                "Incomplete files are queued with the outstanding lines listed.",
                "A file still incomplete 5 business days after move-in escalates to the division head.",
                "A missing signature on the lease itself escalates immediately.",
            ],
        },
        {
            "title": "Archive the file",
            "kind": "record",
            "summary": "Record the sign-off and archive the completed file.",
            "bullets": [
                "The record row is the audit trail for the tenancy.",
                "Archived files stay searchable from the dashboard folder view.",
            ],
        },
    ],
    "breach-notice": [
        {
            "title": "Retrieve the lease",
            "kind": "intake",
            "summary": "Pull the tenant's lease and whatever triggered this notice.",
            "bullets": [
                "Finds the lease for the unit under Lease Agreements.",
                "Finds the violation or activity report that reported the conduct.",
                "Pulls the unit's prior breach notices so history is attached, not re-derived.",
            ],
        },
        {
            "title": "Draft the notice",
            "kind": "analysis",
            "summary": "Write the notice against the lease sections actually breached.",
            "bullets": [
                "Cites the specific lease section by number, not a general clause.",
                "Describes the conduct with dates, taken from the violation report.",
                "States the cure period the lease allows.",
                "Includes the count of prior documented breaches for this unit.",
            ],
        },
        {
            "title": "Check it stands up",
            "kind": "decision",
            "summary": "Ready to send only when the draft is specific and supported.",
            "bullets": [
                "A draft citing no section is held for a person to rewrite.",
                "A draft asserting a breach the lease does not cover is held.",
                "A conflict between the report's dates and the lease term is held.",
            ],
        },
        {
            "title": "Management review",
            "kind": "human",
            "summary": "Notices are never sent by the agent — management sends them.",
            "bullets": [
                "The drafted notice is queued for review with the history attached.",
                "A third documented violation escalates to the division head before sending.",
                "Anything involving safety, weapons or threats escalates immediately, regardless of count.",
            ],
        },
        {
            "title": "Log the breach",
            "kind": "record",
            "summary": "Record the breach so it counts toward the next occurrence.",
            "bullets": [
                "The record row is what the next run reads as prior history.",
                "Property, unit and the sections cited are all captured.",
            ],
        },
    ],
    "security-report": [
        {
            "title": "Take in the report",
            "kind": "intake",
            "summary": "Pull the day's activity report for the property.",
            "bullets": [
                "Reads the Daily Activity Reports folder for the report under review.",
                "Property ID scopes the run to one site.",
                "The original PDF or image is preferred — exports lose formatting that carries meaning.",
            ],
        },
        {
            "title": "Read what was flagged",
            "kind": "analysis",
            "summary": "Work through the report and pull out each flagged item.",
            "bullets": [
                "Each flagged item is read for unit, date, time and what happened.",
                "Items are grouped by unit so a pattern on one unit is visible.",
                "Anything that reads as a lease violation is marked as such.",
            ],
        },
        {
            "title": "Triage",
            "kind": "decision",
            "summary": "Sort each item into note-only, flag, or breach.",
            "bullets": [
                "A minor, non-recurring issue is noted and closed.",
                "A severe first-time issue is flagged for management.",
                "A lease violation is routed to the Breach Notice workflow instead of being closed here.",
            ],
        },
        {
            "title": "Raise what needs a person",
            "kind": "human",
            "summary": "Anything above note-only goes to a person the same day.",
            "bullets": [
                "Severe items are queued for management review immediately.",
                "A repeat item on a unit already noted becomes an escalation.",
            ],
        },
        {
            "title": "Log the incidents",
            "kind": "record",
            "summary": "Write the day's outcome to the record file.",
            "bullets": [
                "The record row keeps the incident history continuous across reports.",
                "Later runs read those rows when deciding whether an issue is recurring.",
            ],
        },
    ],
}

# Shared vocabulary for the reference page. Kept in one place so a term means the
# same thing in every use case narrative rather than being redefined slightly
# differently inside each one.
GLOSSARY = [
    {
        "term": "Additional insured",
        "definition": (
            "AAT named on a vendor's or tenant's policy so the policy responds to claims against "
            "AAT. Stronger than certificate holder, and the wording the rubrics require."
        ),
    },
    {
        "term": "Certificate holder",
        "definition": (
            "A party listed on a certificate as having been sent a copy. It confers no coverage. "
            "A certificate naming AAT only as holder does not satisfy an additional-insured "
            "requirement."
        ),
    },
    {
        "term": "COI",
        "definition": "Certificate of insurance — the one-page summary of a policy's coverages, limits and dates.",
    },
    {
        "term": "Cure period",
        "definition": "The window a lease gives a tenant to fix a breach before further action. A breach notice must state it.",
    },
    {
        "term": "DAR",
        "definition": "Daily activity report — the security log for a property, one per day per site.",
    },
    {
        "term": "Rubric",
        "definition": (
            "The fixed list of requirements a document is graded against for a given use case. "
            "Every requirement comes back met, not met, or unclear, with a quote from the document."
        ),
    },
    {
        "term": "Met / not met / unclear",
        "definition": (
            "The three grades a requirement can receive. 'Unclear' means the document does not say — "
            "it is never treated as satisfied."
        ),
    },
    {
        "term": "Approval case",
        "definition": (
            "A queued item waiting on a person. Raised whenever a run cannot clear on its own, and "
            "closed as approved or sent back."
        ),
    },
    {
        "term": "Human in the loop",
        "definition": (
            "The rule that no outcome is final until a person signs it off. The agent reads, grades "
            "and drafts; it does not clear, send or archive on its own authority."
        ),
    },
    {
        "term": "Record file",
        "definition": "The per-workflow log of completed runs. One row per run, exportable as CSV.",
    },
    {
        "term": "Redaction",
        "definition": "Removing identifiers from an uploaded file before it is read or stored in the repository.",
    },
    {
        "term": "Escalation",
        "definition": "Routing a case above the normal reviewer — to the division head — because of severity or repetition.",
    },
    {
        "term": "Division",
        "definition": (
            "The business line a user signs in under: Multifamily/Residential or Office/Retail. It "
            "scopes every folder, use case and record in the system."
        ),
    },
]


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "step"


def catalog() -> List[dict]:
    """Workflow definitions without a database — id, title, folder, purpose."""
    return [
        {
            "id": wf_id,
            "title": wf["title"],
            "folder": wf["folder"],
            "purpose": wf["purpose"],
            "documents": [
                {"name": d["name"], "folder": d.get("folder", wf["folder"])} for d in wf["documents"]
            ],
        }
        for wf_id, wf in WORKFLOW_CATALOG.items()
    ]


# ---------------- Definition: the steps behind diagram, narrative and run ----------------

def _step_dict(step: WorkflowStep) -> dict:
    return {
        "key": step.key,
        "position": step.position,
        "title": step.title,
        "kind": step.kind,
        "kind_label": STEP_KINDS.get(step.kind, "Step"),
        "summary": step.summary or "",
        "bullets": [line for line in (step.bullets or "").split("\n") if line.strip()],
    }


def _ordered_steps(db: Session, workflow_id: str, division: Division) -> List[WorkflowStep]:
    return (
        db.query(WorkflowStep)
        .filter(WorkflowStep.workflow_id == workflow_id, WorkflowStep.division == division)
        .order_by(WorkflowStep.position.asc(), WorkflowStep.id.asc())
        .all()
    )


def _write_steps(
    db: Session,
    workflow_id: str,
    division: Division,
    steps: List[dict],
    updated_by: Optional[str] = None,
) -> None:
    """Replace the definition wholesale.

    The step list is short and nothing references a step row by id, so replacing
    it is simpler — and safer against half-applied edits — than reconciling adds,
    moves and deletes one at a time.
    """
    db.query(WorkflowStep).filter(
        WorkflowStep.workflow_id == workflow_id, WorkflowStep.division == division
    ).delete(synchronize_session=False)

    used = set()
    now = datetime.utcnow()
    for position, spec in enumerate(steps):
        title = (spec.get("title") or "").strip() or f"Step {position + 1}"
        key = _slug(spec.get("key") or title)
        while key in used:  # two steps may legitimately share a title
            key = f"{key}-{position}"
        used.add(key)

        kind = (spec.get("kind") or "note").strip().lower()
        if kind not in STEP_KINDS:
            kind = "note"

        bullets = spec.get("bullets") or []
        if isinstance(bullets, str):
            bullets = bullets.split("\n")
        bullets = [b.strip() for b in bullets if b and b.strip()]

        db.add(
            WorkflowStep(
                workflow_id=workflow_id,
                division=division,
                position=position,
                key=key,
                title=title,
                kind=kind,
                summary=(spec.get("summary") or "").strip() or None,
                bullets="\n".join(bullets) or None,
                updated_at=now,
                updated_by=updated_by or None,
            )
        )
    db.commit()


def _is_default(steps: List[dict], workflow_id: str) -> bool:
    defaults = DEFAULT_STEPS.get(workflow_id, [])
    if len(steps) != len(defaults):
        return False
    for current, default in zip(steps, defaults):
        if current["title"] != default["title"] or current["kind"] != default["kind"]:
            return False
        if current["summary"] != default["summary"] or current["bullets"] != default["bullets"]:
            return False
    return True


def get_definition(db: Session, workflow_id: str, division: Division) -> dict:
    """The workflow's steps, seeding the shipped defaults on first read."""
    wf = WORKFLOW_CATALOG.get(workflow_id)
    if wf is None:
        raise ValueError(f"Unknown workflow '{workflow_id}'")

    rows = _ordered_steps(db, workflow_id, division)
    if not rows:
        _write_steps(db, workflow_id, division, DEFAULT_STEPS.get(workflow_id, []), "AAT default")
        rows = _ordered_steps(db, workflow_id, division)

    steps = [_step_dict(r) for r in rows]
    latest = max((r.updated_at for r in rows if r.updated_at), default=None)
    return {
        "workflow_id": workflow_id,
        "title": wf["title"],
        "folder": wf["folder"],
        "purpose": wf["purpose"],
        "division": division.value,
        "steps": steps,
        "updated_at": latest.isoformat() if latest else None,
        "updated_by": rows[0].updated_by if rows else None,
        "is_default": _is_default(steps, workflow_id),
    }


def update_definition(
    db: Session,
    workflow_id: str,
    division: Division,
    steps: List[dict],
    updated_by: Optional[str] = None,
) -> dict:
    """Rewrite the definition from an edited narrative.

    Steps may be added, reordered, retitled or removed. The run walks whatever
    comes back out of here, which is what keeps the narrative honest: a step
    written into the narrative is a step the run actually reports on.
    """
    if workflow_id not in WORKFLOW_CATALOG:
        raise ValueError(f"Unknown workflow '{workflow_id}'")
    if not steps:
        raise ValueError("A workflow needs at least one step.")

    _write_steps(db, workflow_id, division, steps, updated_by)
    return get_definition(db, workflow_id, division)


def reset_definition(
    db: Session, workflow_id: str, division: Division, updated_by: Optional[str] = None
) -> dict:
    """Restore the shipped steps — the escape hatch after a bad edit."""
    if workflow_id not in WORKFLOW_CATALOG:
        raise ValueError(f"Unknown workflow '{workflow_id}'")
    _write_steps(db, workflow_id, division, DEFAULT_STEPS.get(workflow_id, []), updated_by or "AAT default")
    return get_definition(db, workflow_id, division)


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
    """Rows logged and when they last moved."""
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
            "url": f"/workflows/{workflow_id}/records.csv?division={division_key(division)}",
        }
    ]

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


def division_key(division: Division) -> str:
    return "retail" if division == Division.OFFICE else "mf"
