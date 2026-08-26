"""The human-in-the-loop approvals queue.

Every workflow ends with a person, not the agent. This is where the cases that
are waiting on that person live, so the dashboard can group them by use case and
the Workflows page can show how many are outstanding for the workflow you have
open — off the same rows, rather than two lists that drift.
"""

from datetime import datetime
from typing import Iterable, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import Division
from .models import Approval

# Illustrative cases for a fresh queue. Each names a workflow_id, and
# `seed_samples` drops any whose use case the division does not actually have —
# an approval pointing at a use case that is not in the catalog is unopenable.
SAMPLE_APPROVALS = [
    {
        "workflow_id": "insurance-certificate-audit",
        "property_id": "RTL-220",
        "unit": "Common area",
        "subject": "Brightline Landscaping — COI renewal",
        "reason": "General liability limit is $1M; AAT requirements specify $2M minimum.",
        "found": ["Certificate of insurance"],
        "missing": ["AAT requirements document"],
    },
    {
        "workflow_id": "coverage-matching",
        "property_id": "OFF-101",
        "unit": "Suite 300",
        "subject": "Tenant policy missing additional insured",
        "reason": "Submitted policy does not list AAT as additional insured, as the agreement requires.",
        "found": ["Governing agreement", "Submitted policy"],
        "missing": ["Coverage matrix"],
    },
    {
        "workflow_id": "clause-search",
        "property_id": "RTL-118",
        "unit": "Suite 210",
        "subject": "After-hours noise — notice drafted, awaiting send",
        "reason": "Draft ready to review: “Suite 210 — use of premises outside permitted hours”. The report is matched to the lease and the section is quoted; the notice has not been sent.",
        "found": ["Incident report", "Tenant lease"],
        "missing": [],
    },
]


def _split(value: Optional[str]) -> List[str]:
    return [line for line in (value or "").split("\n") if line]


def _relative(when: Optional[datetime]) -> str:
    if not when:
        return ""
    delta = datetime.utcnow() - when
    seconds = int(delta.total_seconds())
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} min ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hr ago"
    days = seconds // 86400
    return "yesterday" if days == 1 else f"{days} days ago"


def to_dict(approval: Approval) -> dict:
    return {
        "id": approval.id,
        "reference": f"AP-{approval.id:04d}",
        "workflow": approval.workflow_id,
        "division": approval.division.value,
        "property": approval.property_id or "",
        "unit": approval.unit or "",
        "subject": approval.subject,
        "reason": approval.reason or "",
        "found": _split(approval.found_documents),
        "missing": _split(approval.missing_documents),
        "status": approval.status,
        "source": approval.source,
        "raised": _relative(approval.raised_at),
        "raised_at": approval.raised_at.isoformat() if approval.raised_at else None,
    }


def create(
    db: Session,
    workflow_id: str,
    division: Division,
    subject: str,
    reason: str = "",
    property_id: str = "",
    unit: str = "",
    found: Optional[List[str]] = None,
    missing: Optional[List[str]] = None,
    source: str = "analysis",
    dedupe: bool = False,
) -> Optional[Approval]:
    """Queue a case. With dedupe, an identical pending case is not re-raised."""
    if dedupe:
        existing = (
            db.query(Approval)
            .filter(
                Approval.workflow_id == workflow_id,
                Approval.division == division,
                Approval.status == "pending",
                Approval.property_id == (property_id or None),
                Approval.unit == (unit or None),
            )
            .first()
        )
        if existing:
            return None

    approval = Approval(
        workflow_id=workflow_id,
        division=division,
        property_id=property_id or None,
        unit=unit or None,
        subject=subject,
        reason=reason or None,
        found_documents="\n".join(found or []) or None,
        missing_documents="\n".join(missing or []) or None,
        status="pending",
        source=source,
    )
    db.add(approval)
    db.commit()
    db.refresh(approval)
    return approval


def list_pending(db: Session, division: Division, workflow_id: Optional[str] = None) -> List[dict]:
    query = db.query(Approval).filter(Approval.division == division, Approval.status == "pending")
    if workflow_id:
        query = query.filter(Approval.workflow_id == workflow_id)
    rows = query.order_by(Approval.raised_at.desc(), Approval.id.desc()).all()
    return [to_dict(r) for r in rows]


def pending_counts(db: Session, division: Division) -> dict:
    """Pending count per workflow — what the grouped dashboard header needs."""
    return dict(
        db.query(Approval.workflow_id, func.count(Approval.id))
        .filter(Approval.division == division, Approval.status == "pending")
        .group_by(Approval.workflow_id)
        .all()
    )


def resolve(db: Session, approval_id: int, outcome: str, resolved_by: str = "") -> Optional[Approval]:
    """Close a case. `outcome` is 'approved' or 'returned'."""
    approval = db.get(Approval, approval_id)
    if approval is None or approval.status != "pending":
        return None
    approval.status = outcome
    approval.resolved_at = datetime.utcnow()
    approval.resolved_by = resolved_by or None
    db.commit()
    db.refresh(approval)
    return approval


def seed_samples(db: Session, division: Division, known_ids: Optional[Iterable[str]] = None) -> int:
    """Populate the queue with illustrative cases when it is empty.

    Marked `source='sample'` and shown as such in the UI, so a demo queue is
    never mistaken for real work. Clearable from the dashboard.

    `known_ids` is the division's catalog. A sample naming a use case that
    division does not have is skipped rather than seeded: it would show in the
    queue but open onto nothing, because every approval is read back through its
    workflow. The ids are passed in rather than looked up here so this module
    stays independent of the catalog.
    """
    if db.query(func.count(Approval.id)).filter(Approval.division == division).scalar():
        return 0
    allowed = None if known_ids is None else set(known_ids)
    seeded = 0
    for spec in SAMPLE_APPROVALS:
        if allowed is not None and spec["workflow_id"] not in allowed:
            continue
        create(db, division=division, source="sample", **spec)
        seeded += 1
    return seeded


def clear_samples(db: Session, division: Division) -> int:
    rows = db.query(Approval).filter(Approval.division == division, Approval.source == "sample").all()
    for row in rows:
        db.delete(row)
    db.commit()
    return len(rows)
