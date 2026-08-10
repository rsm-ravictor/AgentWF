"""Persistence for Daily Activity Report incidents.

Storing incidents is what turns "first violation date" into a real answer. Within
a single upload it only means "earliest row in this report"; across stored reports
it means the first time that unit was ever written up — which is what a manager
deciding whether to escalate actually needs.

Aggregation reuses dar_analyzer.aggregate_by_unit so the per-upload table and the
standing register apply identical triage rules.
"""

from datetime import datetime
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .dar_analyzer import Incident, aggregate_by_unit
from .models import DarIncident, DarReport


def save_report(
    db: Session,
    extraction: dict,
    incidents: List[Incident],
    filename: str,
    property_id: Optional[str] = None,
) -> DarReport:
    """Store one analyzed report and its incidents."""
    report = DarReport(
        filename=filename,
        property_id=property_id or None,
        property_name=extraction.get("property_name") or None,
        report_date=extraction.get("report_date") or None,
        shift_or_range=extraction.get("shift_or_range") or None,
        reporting_officer=extraction.get("reporting_officer") or None,
        highlights_detected=bool(extraction.get("highlights_detected", True)),
        notes=extraction.get("notes") or None,
    )
    db.add(report)
    db.flush()  # assign report.id before attaching incidents

    for inc in incidents:
        db.add(
            DarIncident(
                report_id=report.id,
                property_id=property_id or None,
                unit=inc.unit or "Unknown",
                incident_date=inc.date or None,
                incident_time=inc.time or None,
                highlight=inc.highlight,
                category=inc.category,
                keywords="\n".join(inc.keywords or []),
                snippet=inc.snippet,
                lease_relevant=bool(inc.lease_relevant),
            )
        )

    db.commit()
    db.refresh(report)
    return report


def _row_to_incident(row: DarIncident) -> Incident:
    """Rehydrate a stored row into the shape aggregate_by_unit expects."""
    return Incident(
        unit=row.unit,
        date=row.incident_date or "",
        time=row.incident_time or "",
        highlight=row.highlight or "none",
        category=row.category or "",
        keywords=[k for k in (row.keywords or "").split("\n") if k],
        snippet=row.snippet or "",
        lease_relevant=bool(row.lease_relevant),
    )


def unit_register(db: Session, property_id: Optional[str] = None) -> dict:
    """Every unit ever written up, with its full violation history.

    This is the standing register: one row per unit across all stored reports,
    not just the most recent upload.
    """
    query = db.query(DarIncident)
    if property_id:
        query = query.filter(DarIncident.property_id == property_id)
    rows = query.order_by(DarIncident.incident_date.asc(), DarIncident.id.asc()).all()

    incidents = [_row_to_incident(r) for r in rows]
    units = aggregate_by_unit(incidents)

    # Attach which report each incident came from, so the log is traceable.
    report_by_id = {r.id: r for r in db.query(DarReport).all()}
    source_by_unit: dict = {}
    for row in rows:
        rep = report_by_id.get(row.report_id)
        source_by_unit.setdefault(row.unit or "Unknown", []).append(
            {
                "report_id": row.report_id,
                "filename": rep.filename if rep else None,
                "report_date": rep.report_date if rep else None,
                "property_id": row.property_id,
                "property_name": rep.property_name if rep else None,
                "incident_date": row.incident_date,
                "category": row.category,
                "highlight": row.highlight,
            }
        )

    unit_dicts = []
    for u in units:
        d = u.model_dump()
        d["sources"] = source_by_unit.get(u.unit, [])
        unit_dicts.append(d)

    return {
        "units": unit_dicts,
        "totals": {
            "units_affected": len(units),
            "incidents": len(incidents),
            "escalate": sum(1 for u in units if u.triage == "escalate"),
            "watch": sum(1 for u in units if u.triage == "watch"),
            "repeat_units": sum(1 for u in units if u.occurrences > 1),
            "reports": db.query(func.count(DarReport.id)).scalar() or 0,
        },
    }


def list_reports(db: Session, limit: int = 50) -> List[dict]:
    """Reports uploaded so far, newest first — the 'log of reports made'."""
    reports = (
        db.query(DarReport)
        .order_by(DarReport.uploaded_at.desc(), DarReport.id.desc())
        .limit(limit)
        .all()
    )
    out = []
    for r in reports:
        counts = {"red": 0, "yellow": 0, "none": 0}
        for inc in r.incidents:
            counts[inc.highlight if inc.highlight in counts else "none"] += 1
        out.append(
            {
                "id": r.id,
                "filename": r.filename,
                "property_id": r.property_id,
                "property_name": r.property_name,
                "report_date": r.report_date,
                "shift_or_range": r.shift_or_range,
                "reporting_officer": r.reporting_officer,
                "highlights_detected": r.highlights_detected,
                "uploaded_at": r.uploaded_at.isoformat() if r.uploaded_at else None,
                "incident_count": len(r.incidents),
                "units": sorted({i.unit for i in r.incidents}),
                "severity_counts": counts,
            }
        )
    return out


def delete_report(db: Session, report_id: int) -> bool:
    """Remove a report and its incidents — for re-running a bad extraction."""
    report = db.get(DarReport, report_id)
    if report is None:
        return False
    db.delete(report)  # incidents cascade
    db.commit()
    return True
