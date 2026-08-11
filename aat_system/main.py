import csv
import io
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
import anthropic
from fastapi import FastAPI, UploadFile, File, Depends, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from .db import Base, engine, SessionLocal, get_db
from .models import User, Folder, Document, Lease
from .auth import assert_division_access, assert_folder_access, get_allowed_folders
from .document_repo import ingest_document, scan_expired_leases, get_or_create_folder
from .config import ARCHIVE_ROOT, Division, Permission, Role, CORE_FOLDERS, has_permission
from .security import authenticate_user, create_access_token, get_current_active_user, get_password_hash
from .utils import ensure_storage_directories
from . import llm_analyzer, dar_analyzer, dar_repo, approval_repo, user_repo, workflow_repo

class TokenResponse(BaseModel):
    access_token: str
    token_type: str

class UserCreateRequest(BaseModel):
    email: str
    name: str
    password: str
    division: Division
    role: Role

class DocumentUploadRequest(BaseModel):
    folder_name: str
    division: Division

class SopUpdateRequest(BaseModel):
    division: str = "mf"
    inputs_expected: Optional[str] = None
    steps_taken: Optional[str] = None
    pass_fail_logic: Optional[str] = None
    escalation_rules: Optional[str] = None
    updated_by: str = ""


class RecordCreateRequest(BaseModel):
    division: str = "mf"
    outcome: str = "signed_off"
    property_id: str = ""
    unit: str = ""
    subject: str = ""
    decision_note: str = ""
    document_name: str = ""
    recorded_by: str = ""


class SessionResolveRequest(BaseModel):
    email: str
    division: str = "mf"
    name: str = ""


class UserUpdateRequest(BaseModel):
    role: Optional[Role] = None
    division: Optional[Division] = None
    is_active: Optional[bool] = None
    name: Optional[str] = None
    acting_user_id: Optional[int] = None


class ApprovalResolveRequest(BaseModel):
    outcome: str = "approved"
    resolved_by: str = ""


app = FastAPI(title="AAT System")

# The UI identifies divisions by short key; the DB stores the full enum value.
DIVISION_KEYS = {"mf": Division.MULTIFAMILY, "retail": Division.OFFICE}


def resolve_division(key: str) -> Division:
    return DIVISION_KEYS.get(key, Division.MULTIFAMILY)

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", response_class=HTMLResponse)
def read_root():
    html_path = static_dir / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="UI preview not available")

    html = html_path.read_text(encoding="utf-8")
    # Stamp each asset URL with that file's modification time. Nothing sets
    # cache headers on /static, so a browser is free to pair a freshly served
    # index.html with an app.js it cached before the last edit -- markup and
    # script from different versions, which presents as a dead UI rather than
    # as a stale file. A changing query string makes that pairing impossible.
    for asset in ("app.js", "style.css"):
        asset_path = static_dir / asset
        if asset_path.exists():
            stamp = int(asset_path.stat().st_mtime)
            html = html.replace(f"/static/{asset}", f"/static/{asset}?v={stamp}")

    return HTMLResponse(html, headers={"Cache-Control": "no-store"})

@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)
    ensure_storage_directories()
    with SessionLocal() as db:
        for division in Division:
            for folder_name in CORE_FOLDERS:
                get_or_create_folder(db, folder_name, division)
        user_repo.seed_roster(db)
        # Illustrative cases so the queue is not empty on a fresh install. They
        # are tagged 'sample' in the UI and clearable from the dashboard.
        for division in Division:
            approval_repo.seed_samples(db, division)

@app.post("/token", response_model=TokenResponse)
def login_for_access_token(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    access_token_expires = timedelta(minutes=int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30")))
    access_token = create_access_token(data={"sub": user.email}, expires_delta=access_token_expires)
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/users")
def create_user(payload: UserCreateRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="User already exists")
    hashed_password = get_password_hash(payload.password)
    user = User(email=payload.email, name=payload.name, division=payload.division, role=payload.role, hashed_password=hashed_password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

@app.get("/users/me")
def read_current_user(current_user: User = Depends(get_current_active_user)):
    return current_user

@app.get("/users/{user_id}/folders")
def read_allowed_folders(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"allowed_folders": get_allowed_folders(user)}

@app.post("/documents/upload")
def upload_document(
    owner_id: int = Form(...),
    division: Division = Form(...),
    folder_name: str = Form(...),
    upload_file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    owner = db.get(User, owner_id)
    if not owner:
        raise HTTPException(status_code=404, detail="Owner not found")
    assert_division_access(owner, division)
    assert_folder_access(owner, folder_name)
    upload_root = Path(os.getenv("UPLOAD_ROOT", "uploaded_files"))
    source_path = upload_root / upload_file.filename
    source_path.parent.mkdir(exist_ok=True, parents=True)
    with source_path.open("wb") as buffer:
        buffer.write(upload_file.file.read())
    document = ingest_document(db, owner, source_path, folder_name, division, metadata=upload_file.content_type)
    return document

@app.get("/leases/expired")
def leases_expired(db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    expired = scan_expired_leases(db)
    return {"expired_count": len(expired), "leases": [lease.id for lease in expired]}

@app.get("/folders/{division}/{folder_name}/documents")
def get_documents(division: Division, folder_name: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_active_user)):
    documents = db.query(Document).join(Folder).filter(Folder.division == division, Folder.name == folder_name).all()
    return documents

MAX_ANALYZE_BYTES = 20 * 1024 * 1024  # 20 MB — well under the API's 32 MB request cap

@app.get("/analyze/workflows")
def list_analyzable_workflows():
    """Workflow IDs the analyzer can grade, plus whether a key is configured."""
    return {
        "configured": llm_analyzer.has_api_key(),
        "model": llm_analyzer.MODEL,
        "workflows": [
            {"id": wf_id, "title": rubric["title"], "requirements": rubric["requirements"]}
            for wf_id, rubric in llm_analyzer.WORKFLOW_RUBRICS.items()
        ],
    }

@app.post("/analyze")
async def analyze_document_endpoint(
    workflow: str = Form(...),
    property_id: str = Form(""),
    unit_id: str = Form(""),
    division: str = Form("mf"),
    upload_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Grade an uploaded document against a workflow's rubric using Claude.

    Prototype endpoint: unauthenticated so the preview UI can exercise it. Put it
    behind get_current_active_user before this is exposed beyond localhost.
    """
    if not llm_analyzer.has_api_key():
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is not set. Add it to .env and restart the server.",
        )

    contents = await upload_file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(contents) > MAX_ANALYZE_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than the 20 MB limit.")

    media_type = upload_file.content_type or "application/octet-stream"
    try:
        verdict = llm_analyzer.analyze_document(
            workflow_id=workflow,
            file_bytes=contents,
            filename=upload_file.filename or "document",
            media_type=media_type,
            property_id=property_id,
            unit_id=unit_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except TypeError as exc:
        # The SDK raises TypeError at request time when no credential resolved.
        if "authentication" in str(exc).lower():
            raise HTTPException(
                status_code=503,
                detail="No Anthropic credential resolved. Set ANTHROPIC_API_KEY in .env and restart.",
            )
        raise
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=502, detail="Anthropic rejected the API key.")
    except anthropic.RateLimitError:
        raise HTTPException(status_code=429, detail="Rate limited by Anthropic. Try again shortly.")
    except anthropic.APIStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {exc.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=502, detail="Could not reach the Anthropic API.")

    # Anything the model would not clear outright becomes a real queued case, so
    # the approvals list reflects work that actually happened.
    approval = None
    if verdict.decision in ("needs_human_review", "reject") and workflow in workflow_repo.WORKFLOW_CATALOG:
        unmet = [f.requirement for f in verdict.findings if f.status != "met"]
        created = approval_repo.create(
            db,
            workflow_id=workflow,
            division=resolve_division(division),
            subject=f"{upload_file.filename or 'Document'} — {verdict.document_type}",
            reason=verdict.summary,
            property_id=property_id,
            unit=unit_id,
            found=[f.requirement for f in verdict.findings if f.status == "met"],
            missing=unmet + list(verdict.missing_information or []),
            source="analysis",
        )
        approval = approval_repo.to_dict(created) if created else None

    return {
        "workflow": workflow,
        "filename": upload_file.filename,
        "verdict": verdict.model_dump(),
        "approval": approval,
    }

@app.get("/dar/register")
def dar_register(property_id: str = "", db: Session = Depends(get_db)):
    """Standing per-unit register across every stored report."""
    return dar_repo.unit_register(db, property_id=property_id or None)

@app.get("/dar/reports")
def dar_reports(db: Session = Depends(get_db)):
    """Log of reports uploaded so far, newest first."""
    return {"reports": dar_repo.list_reports(db)}

@app.delete("/dar/reports/{report_id}")
def dar_delete_report(report_id: int, db: Session = Depends(get_db)):
    """Drop a report and its incidents, e.g. to re-run a bad extraction."""
    if not dar_repo.delete_report(db, report_id):
        raise HTTPException(status_code=404, detail="Report not found")
    return {"deleted": report_id}

@app.post("/analyze/dar")
async def analyze_dar_endpoint(
    property_id: str = Form(""),
    save: bool = Form(True),
    division: str = Form("mf"),
    upload_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Extract highlighted incidents from a Daily Activity Report, grouped by unit.

    Prototype endpoint: unauthenticated so the preview UI can exercise it. Put it
    behind get_current_active_user before this is exposed beyond localhost.
    """
    if not llm_analyzer.has_api_key():
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is not set. Add it to .env and restart the server.",
        )

    contents = await upload_file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(contents) > MAX_ANALYZE_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than the 20 MB limit.")

    try:
        result = dar_analyzer.analyze_dar(
            file_bytes=contents,
            filename=upload_file.filename or "report",
            media_type=upload_file.content_type or "application/octet-stream",
            property_id=property_id,
        )
    except TypeError as exc:
        if "authentication" in str(exc).lower():
            raise HTTPException(
                status_code=503,
                detail="No Anthropic credential resolved. Set ANTHROPIC_API_KEY in .env and restart.",
            )
        raise
    except anthropic.AuthenticationError:
        raise HTTPException(status_code=502, detail="Anthropic rejected the API key.")
    except anthropic.RateLimitError:
        raise HTTPException(status_code=429, detail="Rate limited by Anthropic. Try again shortly.")
    except anthropic.APIStatusError as exc:
        raise HTTPException(status_code=502, detail=f"Anthropic API error: {exc.message}")
    except anthropic.APIConnectionError:
        raise HTTPException(status_code=502, detail="Could not reach the Anthropic API.")

    raw_incidents = result.pop("_incidents", [])
    result["filename"] = upload_file.filename
    result["saved"] = False

    if save:
        try:
            report = dar_repo.save_report(
                db,
                extraction=result["report"],
                incidents=raw_incidents,
                filename=upload_file.filename or "report",
                property_id=property_id or None,
            )
            result["saved"] = True
            result["report_id"] = report.id
        except Exception as exc:  # analysis already succeeded — don't lose it
            db.rollback()
            result["save_error"] = f"Analysis succeeded but saving failed: {exc}"

    # A unit that triages to escalate is exactly the case the Breach Notice
    # workflow exists for, so raise it rather than leaving it in the log only.
    div = resolve_division(division)
    raised = 0
    for unit in result.get("units", []):
        if unit.get("triage") != "escalate":
            continue
        created = approval_repo.create(
            db,
            workflow_id="breach-notice",
            division=div,
            subject=f"Unit {unit['unit']} — {', '.join(unit.get('categories') or ['incident'])}",
            reason=(
                f"{unit['occurrences']} logged incident(s), worst highlight "
                f"{unit.get('worst_highlight')}. First seen {unit.get('first_violation_date') or 'unknown'}."
            ),
            property_id=property_id,
            unit=unit["unit"],
            found=["Violation report"],
            missing=["Tenant lease", "Prior breach history"],
            source="dar",
            dedupe=True,  # one open case per unit, not one per upload
        )
        if created:
            raised += 1
    result["approvals_raised"] = raised

    return result

@app.get("/dashboard/summary")
def dashboard_summary(division: str = "mf", db: Session = Depends(get_db)):
    """Every number the dashboard shows, read from the database.

    Nothing here is seeded or estimated: folder counts come from the documents
    table, lease counts from lease_end dates, and the review queue from DAR
    incidents that triaged to escalate. An empty repository reports zeroes.

    Unauthenticated for the same reason as /analyze — the preview UI holds no
    token. Gate it with get_current_active_user before exposing beyond localhost.
    """
    div = DIVISION_KEYS.get(division, Division.MULTIFAMILY)

    per_folder = dict(
        db.query(Folder.name, func.count(Document.id))
        .outerjoin(Document, Document.folder_id == Folder.id)
        .filter(Folder.division == div)
        .group_by(Folder.name)
        .all()
    )
    folders = [{"name": name, "count": per_folder.get(name, 0)} for name in CORE_FOLDERS]

    now = datetime.utcnow()
    cutoff = now + timedelta(days=30)
    leases_expiring = (
        db.query(func.count(Lease.id))
        .filter(Lease.lease_end >= now, Lease.lease_end <= cutoff)
        .scalar()
        or 0
    )
    leases_expired = db.query(func.count(Lease.id)).filter(Lease.lease_end < now).scalar() or 0

    # Units whose incident history triaged to escalate are the real "needs a human"
    # queue: red-highlighted, or yellow that recurred. See dar_analyzer triage rules.
    register = dar_repo.unit_register(db)
    escalations = []
    for unit in register["units"]:
        if unit["triage"] != "escalate":
            continue
        sources = unit.get("sources") or []
        escalations.append(
            {
                "unit": unit["unit"],
                "property_id": next((s.get("property_id") for s in sources if s.get("property_id")), ""),
                "property_name": next((s.get("property_name") for s in sources if s.get("property_name")), ""),
                "occurrences": unit["occurrences"],
                "first_violation_date": unit["first_violation_date"],
                "latest_violation_date": unit["latest_violation_date"],
                "worst_highlight": unit["worst_highlight"],
                "categories": unit["categories"],
                "keywords": unit["keywords"],
                "snippets": unit["snippets"],
                "lease_relevant": any(inc.get("lease_relevant") for inc in unit.get("incidents", [])),
                "reports": sorted({s.get("filename") for s in sources if s.get("filename")}),
            }
        )

    approvals = approval_repo.list_pending(db, div)

    return {
        "division": div.value,
        "folders": folders,
        "documents_total": sum(f["count"] for f in folders),
        "leases_expiring_soon": leases_expiring,
        "leases_expired": leases_expired,
        "leases_total": db.query(func.count(Lease.id)).scalar() or 0,
        "dar": register["totals"],
        "escalations": escalations,
        "approvals": approvals,
        "approval_counts": approval_repo.pending_counts(db, div),
        "workflows": workflow_repo.catalog(),
    }

@app.get("/repository/documents")
def repository_documents(division: str = "mf", folder: str = "", q: str = "", db: Session = Depends(get_db)):
    """Documents actually stored in the repository, for the workflow doc search.

    Unauthenticated preview endpoint — same caveat as /dashboard/summary.
    """
    div = DIVISION_KEYS.get(division, Division.MULTIFAMILY)
    query = db.query(Document).join(Folder).filter(Folder.division == div)
    if folder:
        query = query.filter(Folder.name == folder)
    if q:
        query = query.filter(Document.filename.ilike(f"%{q}%"))
    documents = query.order_by(Document.uploaded_at.desc()).limit(100).all()
    return {
        "documents": [
            {
                "id": d.id,
                "filename": d.filename,
                "folder": d.folder.name if d.folder else None,
                "uploaded_at": d.uploaded_at.isoformat() if d.uploaded_at else None,
                "redacted": d.redacted_at is not None,
            }
            for d in documents
        ]
    }

@app.get("/repository/documents/{document_id}/download")
def download_document(document_id: int, db: Session = Depends(get_db)):
    """Serve an archived file so record files open without leaving the page."""
    document = db.get(Document, document_id)
    if not document or not document.folder:
        raise HTTPException(status_code=404, detail="Document not found")

    path = ARCHIVE_ROOT / document.folder.division.value / document.folder.name / document.filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="File is recorded but missing from the archive.")
    return FileResponse(path, filename=document.filename)


@app.get("/repository/folders")
def repository_folders(division: str = "mf", db: Session = Depends(get_db)):
    """Folder categories with live document counts, for the Repository page."""
    div = resolve_division(division)
    per_folder = dict(
        db.query(Folder.name, func.count(Document.id))
        .outerjoin(Document, Document.folder_id == Folder.id)
        .filter(Folder.division == div)
        .group_by(Folder.name)
        .all()
    )
    last_upload = dict(
        db.query(Folder.name, func.max(Document.uploaded_at))
        .join(Document, Document.folder_id == Folder.id)
        .filter(Folder.division == div)
        .group_by(Folder.name)
        .all()
    )
    folder_workflows: dict = {}
    for wf_id, wf in workflow_repo.WORKFLOW_CATALOG.items():
        folder_workflows.setdefault(wf["folder"], []).append({"id": wf_id, "title": wf["title"]})

    return {
        "division": div.value,
        "folders": [
            {
                "name": name,
                "count": per_folder.get(name, 0),
                "last_upload": last_upload[name].isoformat() if last_upload.get(name) else None,
                "workflows": folder_workflows.get(name, []),
            }
            for name in CORE_FOLDERS
        ],
        "total": sum(per_folder.values()),
    }


# ---------------- Workflows: catalog, overview, standing instructions ----------------

@app.get("/workflows/catalog")
def workflows_catalog():
    """Workflow definitions — folder, steps, and the documents each requires."""
    return {"workflows": workflow_repo.catalog()}


@app.get("/workflows/overview")
def workflows_overview(division: str = "mf", db: Session = Depends(get_db)):
    """One mini-dashboard payload per use case, in a single round trip."""
    div = resolve_division(division)
    return {
        "division": div.value,
        "overviews": [
            {
                "workflow": wf_id,
                "title": wf["title"],
                "folder": wf["folder"],
                "approvals": approval_repo.list_pending(db, div, workflow_id=wf_id),
                "records": workflow_repo.record_summary(db, wf_id, div),
                "record_files": workflow_repo.record_files(db, wf_id, div),
                "required_documents": workflow_repo.required_documents(db, wf_id, div),
            }
            for wf_id, wf in workflow_repo.WORKFLOW_CATALOG.items()
        ],
    }


@app.get("/workflows/{workflow_id}/overview")
def workflow_overview(workflow_id: str, division: str = "mf", db: Session = Depends(get_db)):
    """The Workflows mini-dashboard: approvals, records, files, doc checklist."""
    if workflow_id not in workflow_repo.WORKFLOW_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown workflow '{workflow_id}'")
    div = resolve_division(division)
    return {
        "workflow": workflow_id,
        "division": div.value,
        "approvals": approval_repo.list_pending(db, div, workflow_id=workflow_id),
        "records": workflow_repo.record_summary(db, workflow_id, div),
        "record_files": workflow_repo.record_files(db, workflow_id, div),
        "required_documents": workflow_repo.required_documents(db, workflow_id, div),
    }


@app.get("/workflows/{workflow_id}/sop")
def get_workflow_sop(workflow_id: str, division: str = "mf", db: Session = Depends(get_db)):
    """Standing instructions for the workflow, seeded from defaults on first read."""
    try:
        return workflow_repo.get_sop(db, workflow_id, resolve_division(division))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.put("/workflows/{workflow_id}/sop")
def put_workflow_sop(workflow_id: str, payload: SopUpdateRequest, db: Session = Depends(get_db)):
    """Edit the standing instructions. Kept out of code so no deploy is needed."""
    try:
        return workflow_repo.update_sop(
            db,
            workflow_id,
            resolve_division(payload.division),
            payload.model_dump(exclude={"division", "updated_by"}),
            updated_by=payload.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/workflows/{workflow_id}/sop/reset")
def reset_workflow_sop(workflow_id: str, payload: SopUpdateRequest, db: Session = Depends(get_db)):
    try:
        return workflow_repo.reset_sop(
            db, workflow_id, resolve_division(payload.division), updated_by=payload.updated_by
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/workflows/{workflow_id}/records")
def get_workflow_records(workflow_id: str, division: str = "mf", db: Session = Depends(get_db)):
    div = resolve_division(division)
    return {
        "summary": workflow_repo.record_summary(db, workflow_id, div),
        "records": workflow_repo.list_records(db, workflow_id, div),
    }


@app.post("/workflows/{workflow_id}/records")
def post_workflow_record(workflow_id: str, payload: RecordCreateRequest, db: Session = Depends(get_db)):
    """Log one row of record-keeping for a completed run."""
    if workflow_id not in workflow_repo.WORKFLOW_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown workflow '{workflow_id}'")
    div = resolve_division(payload.division)
    record = workflow_repo.log_record(
        db,
        workflow_id=workflow_id,
        division=div,
        outcome=payload.outcome,
        property_id=payload.property_id,
        unit=payload.unit,
        subject=payload.subject,
        decision_note=payload.decision_note,
        document_name=payload.document_name,
        recorded_by=payload.recorded_by,
    )
    return {"id": record.id, "summary": workflow_repo.record_summary(db, workflow_id, div)}


@app.get("/workflows/{workflow_id}/records.csv")
def get_workflow_records_csv(workflow_id: str, division: str = "mf", db: Session = Depends(get_db)):
    """The workflow's record file, openable straight from the Workflows page."""
    if workflow_id not in workflow_repo.WORKFLOW_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown workflow '{workflow_id}'")
    body = workflow_repo.records_csv(db, workflow_id, resolve_division(division))
    return Response(
        content="﻿" + body,  # BOM so Excel reads UTF-8
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{workflow_id}-records.csv"'},
    )


@app.get("/dar/register.csv")
def dar_register_csv(property_id: str = "", db: Session = Depends(get_db)):
    """The standing incident register as a spreadsheet-ready file."""
    register = dar_repo.unit_register(db, property_id=property_id or None)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(
        ["Unit", "First violation", "Latest violation", "Occurrences", "Triage",
         "Highlight", "Categories", "Keywords", "Snippets"]
    )
    for unit in register["units"]:
        writer.writerow(
            [
                unit["unit"], unit["first_violation_date"], unit["latest_violation_date"],
                unit["occurrences"], unit["triage"], unit["worst_highlight"],
                "; ".join(unit["categories"]), "; ".join(unit["keywords"]),
                " | ".join(unit["snippets"]),
            ]
        )
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="incident-register.csv"'},
    )


# ---------------- Approvals ----------------

@app.get("/approvals")
def list_approvals(division: str = "mf", workflow: str = "", db: Session = Depends(get_db)):
    """Pending cases plus a per-workflow count, so the dashboard can group them."""
    div = resolve_division(division)
    return {
        "approvals": approval_repo.list_pending(db, div, workflow_id=workflow or None),
        "counts": approval_repo.pending_counts(db, div),
    }


@app.post("/approvals/{approval_id}/resolve")
def resolve_approval(approval_id: int, payload: ApprovalResolveRequest, db: Session = Depends(get_db)):
    """Approve or send back a case, and log a record row for the decision."""
    if payload.outcome not in ("approved", "returned"):
        raise HTTPException(status_code=400, detail="Outcome must be 'approved' or 'returned'.")

    approval = approval_repo.resolve(db, approval_id, payload.outcome, resolved_by=payload.resolved_by)
    if approval is None:
        raise HTTPException(status_code=404, detail="No pending approval with that id.")

    workflow_repo.log_record(
        db,
        workflow_id=approval.workflow_id,
        division=approval.division,
        outcome="signed_off" if payload.outcome == "approved" else "sent_back",
        property_id=approval.property_id or "",
        unit=approval.unit or "",
        subject=approval.subject,
        decision_note=approval.reason or "",
        recorded_by=payload.resolved_by,
    )
    return {"resolved": approval_repo.to_dict(approval)}


@app.delete("/approvals/samples")
def delete_sample_approvals(division: str = "mf", db: Session = Depends(get_db)):
    """Drop the illustrative cases once real ones exist."""
    removed = approval_repo.clear_samples(db, resolve_division(division))
    return {"removed": removed}


# ---------------- Session, profile, and administration ----------------

@app.post("/session/resolve")
def resolve_session(payload: SessionResolveRequest, db: Session = Depends(get_db)):
    """Resolve a signing-in email to a real account, so the UI knows the role.

    The preview UI issues no token; this is what gives a session an actual role
    rather than letting the client assert one. Unknown emails are provisioned at
    the least-privileged role.
    """
    if not payload.email.strip():
        raise HTTPException(status_code=400, detail="An email or username is required.")
    user = user_repo.resolve_session_user(
        db, payload.email, resolve_division(payload.division), name=payload.name
    )
    return {"profile": user_repo.profile(user)}


@app.get("/roles")
def list_roles():
    """Every role, its access level, and the permissions it grants."""
    return {"roles": user_repo.role_catalog(), "permissions": user_repo.permission_catalog()}


@app.get("/admin/users")
def admin_list_users(division: str = "", db: Session = Depends(get_db)):
    div = DIVISION_KEYS.get(division) if division else None
    return {
        "users": user_repo.list_users(db, division=div),
        "roles": user_repo.role_catalog(),
        "permissions": user_repo.permission_catalog(),
    }


@app.patch("/admin/users/{user_id}")
def admin_update_user(user_id: int, payload: UserUpdateRequest, db: Session = Depends(get_db)):
    """Change a user's role, division, or active state.

    Gated on the acting user actually holding MANAGE_ROLES / MANAGE_USERS, so the
    Admin page is not merely hidden in the UI — the server refuses too.
    """
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")

    actor = db.get(User, payload.acting_user_id) if payload.acting_user_id else None
    if actor is None:
        raise HTTPException(status_code=403, detail="An acting administrator is required.")

    needed = Permission.MANAGE_ROLES if payload.role is not None else Permission.MANAGE_USERS
    if not has_permission(actor.role, needed):
        raise HTTPException(
            status_code=403,
            detail=f"Your role ({actor.role.value}) does not grant '{needed.value}'.",
        )

    # Only a super user can create or unmake another super user.
    touches_super = Role.SUPER_USER in (payload.role, target.role)
    if touches_super and actor.role != Role.SUPER_USER:
        raise HTTPException(status_code=403, detail="Only a super user can change super-user access.")

    if actor.id == target.id and payload.role is not None and payload.role != actor.role:
        raise HTTPException(status_code=400, detail="You cannot change your own role.")

    if payload.role is not None:
        target.role = payload.role
    if payload.division is not None:
        target.division = payload.division
    if payload.is_active is not None:
        target.is_active = payload.is_active
    if payload.name is not None and payload.name.strip():
        target.name = payload.name.strip()

    db.commit()
    db.refresh(target)
    return {"user": user_repo.profile(target)}


@app.get("/phase2/email-ingestion")
def email_ingestion_placeholder(current_user: User = Depends(get_current_active_user)):
    return {
        "phase": "2",
        "feature": "Email ingestion placeholder",
        "status": "planned",
        "description": "This endpoint represents a UI spot for future inbox scanning and automated folder sorting.",
        "required_folders": CORE_FOLDERS,
        "note": "Manual folder uploads remain the current workflow. Email ingestion will be implemented in Phase 2.",
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("aat_system.main:app", host="127.0.0.1", port=8000, reload=True)
