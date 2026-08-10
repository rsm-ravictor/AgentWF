from datetime import datetime
from typing import List, Optional
from sqlalchemy.orm import Session
from .document_repo import find_documents_by_folder, find_lease_by_tenant, record_lease
from .models import BreachLog, Lease, User


def vendor_insurance_workflow(db: Session, division, vendor_name: str, insurance_doc_id: int, requirements: str) -> dict:
    documents = find_documents_by_folder(db, division, "Vendor Insurances")
    return {
        "vendor_name": vendor_name,
        "insurance_doc_id": insurance_doc_id,
        "requirements_reference": requirements,
        "available_documents": [doc.filename for doc in documents],
    }


def renters_insurance_workflow(db: Session, lease_id: int, tenant_email: str, required_endorsements: List[str], submitted_document_id: Optional[int] = None) -> dict:
    checklist = {"lease_id": lease_id, "required_endorsements": required_endorsements, "tenant_email": tenant_email}
    if submitted_document_id:
        return {
            "checklist": checklist,
            "submitted_document_id": submitted_document_id,
            "status": "pending validation",
        }
    return {"checklist": checklist, "status": "checklist created"}


def lease_and_checklist_workflow(db: Session, lease_data: dict, document_ids: List[int], reviewer_email: str) -> dict:
    lease = record_lease(
        db,
        tenant_name=lease_data["tenant_name"],
        property_name=lease_data["property_name"],
        lease_start=lease_data["lease_start"],
        lease_end=lease_data["lease_end"],
        notes=lease_data.get("notes"),
    )
    return {
        "lease_id": lease.id,
        "document_ids": document_ids,
        "reviewer_email": reviewer_email,
        "status": "ready for final confirmation",
    }


def breach_notice_workflow(db: Session, lease_id: int, reporter: User, violation_details: str, lease_sections: List[str]) -> dict:
    lease = db.query(Lease).filter(Lease.id == lease_id).first()
    if not lease:
        raise ValueError("Lease not found")
    prior_breaches = db.query(BreachLog).filter(BreachLog.lease_id == lease_id).all()
    description = f"Violation details: {violation_details}. Lease sections: {', '.join(lease_sections)}. Prior breaches: {len(prior_breaches)}"
    breach = BreachLog(
        lease_id=lease.id,
        breach_type="lease_breach",
        description=description,
        created_by=reporter.id,
    )
    db.add(breach)
    db.commit()
    db.refresh(breach)
    return {
        "lease_id": lease.id,
        "breach_id": breach.id,
        "prior_breach_count": len(prior_breaches),
        "status": "queued for management review",
    }


def security_report_workflow(db: Session, report_id: int, flagged_items: List[dict], reviewer_email: str) -> dict:
    action_summary = []
    for item in flagged_items:
        color = item.get("severity")
        if color == "red":
            action_summary.append({"item": item, "action": "management review"})
        elif color == "yellow":
            if item.get("recurring"):
                action_summary.append({"item": item, "action": "prepare breach notice"})
            else:
                action_summary.append({"item": item, "action": "add note"})
        else:
            action_summary.append({"item": item, "action": "monitor"})
    return {
        "report_id": report_id,
        "reviewer_email": reviewer_email,
        "actions": action_summary,
        "status": "evaluated",
    }
