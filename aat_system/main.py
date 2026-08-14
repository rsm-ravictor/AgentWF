import json
import os
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Depends, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
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
from . import llm_analyzer, approval_repo, user_repo, workflow_repo, workflow_runner

MAX_RUN_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB — well under the API's 32 MB request cap


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


class UserCreateRequest(BaseModel):
    email: str
    name: str
    password: str
    division: Division
    role: Role


class StepPayload(BaseModel):
    title: str
    kind: str = "note"
    summary: str = ""
    bullets: List[str] = []
    key: Optional[str] = None


class DefinitionUpdateRequest(BaseModel):
    division: str = "mf"
    steps: List[StepPayload] = []
    updated_by: str = ""


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
        raise HTTPException(status_code=404, detail="UI is not available")

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


# ---------------- Auth and users ----------------

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
    user = User(
        email=payload.email,
        name=payload.name,
        division=payload.division,
        role=payload.role,
        hashed_password=hashed_password,
    )
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


# ---------------- Repository ----------------

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


@app.get("/repository/documents")
def repository_documents(division: str = "mf", folder: str = "", q: str = "", db: Session = Depends(get_db)):
    """Documents stored in the repository, for the dashboard folder view.

    Unauthenticated preview endpoint — see the note on /dashboard/summary.
    """
    div = resolve_division(division)
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


# ---------------- Division dashboard ----------------

def _folder_counts(db: Session, division: Division) -> dict:
    return dict(
        db.query(Folder.name, func.count(Document.id))
        .outerjoin(Document, Document.folder_id == Folder.id)
        .filter(Folder.division == division)
        .group_by(Folder.name)
        .all()
    )


@app.get("/dashboard/summary")
def dashboard_summary(division: str = "mf", db: Session = Depends(get_db)):
    """Everything the division dashboard shows, in one round trip.

    Nothing here is seeded or estimated: folder counts come from the documents
    table, lease counts from lease_end dates, and the queue from real approval
    rows. An empty repository reports zeroes.

    Unauthenticated because the preview UI holds no token. Gate it with
    get_current_active_user before exposing beyond localhost.
    """
    div = resolve_division(division)

    per_folder = _folder_counts(db, div)
    last_upload = dict(
        db.query(Folder.name, func.max(Document.uploaded_at))
        .join(Document, Document.folder_id == Folder.id)
        .filter(Folder.division == div)
        .group_by(Folder.name)
        .all()
    )
    folders = [
        {
            "name": name,
            "count": per_folder.get(name, 0),
            "last_upload": last_upload[name].isoformat() if last_upload.get(name) else None,
        }
        for name in CORE_FOLDERS
    ]

    now = datetime.utcnow()
    cutoff = now + timedelta(days=30)
    leases_expiring = (
        db.query(func.count(Lease.id)).filter(Lease.lease_end >= now, Lease.lease_end <= cutoff).scalar() or 0
    )
    leases_expired = db.query(func.count(Lease.id)).filter(Lease.lease_end < now).scalar() or 0

    counts = approval_repo.pending_counts(db, div)

    # One tile payload per use case. Every tile follows the same shape whatever
    # the use case does — only the execution logic behind it differs.
    use_cases = []
    for wf_id, wf in workflow_repo.WORKFLOW_CATALOG.items():
        definition = workflow_repo.get_definition(db, wf_id, div)
        records = workflow_repo.record_summary(db, wf_id, div)
        documents = workflow_repo.required_documents(db, wf_id, div)
        use_cases.append(
            {
                "id": wf_id,
                "title": wf["title"],
                "folder": wf["folder"],
                "purpose": wf["purpose"],
                "steps": len(definition["steps"]),
                "approvals": counts.get(wf_id, 0),
                "records": records["rows_logged"],
                "last_run": records["last_updated"],
                "documents_present": documents["present"],
                "documents_total": documents["total"],
            }
        )

    return {
        "division": div.value,
        "division_key": workflow_repo.division_key(div),
        "folders": folders,
        "documents_total": sum(f["count"] for f in folders),
        "leases_expiring_soon": leases_expiring,
        "leases_expired": leases_expired,
        "leases_total": db.query(func.count(Lease.id)).scalar() or 0,
        "approvals": approval_repo.list_pending(db, div),
        "approval_counts": counts,
        "use_cases": use_cases,
        "analysis_configured": llm_analyzer.has_api_key(),
        "model": llm_analyzer.MODEL,
    }


# ---------------- Use cases ----------------

@app.get("/workflows/{workflow_id}")
def workflow_detail(workflow_id: str, division: str = "mf", db: Session = Depends(get_db)):
    """Everything the use case detail page renders: definition, state, rubric."""
    if workflow_id not in workflow_repo.WORKFLOW_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown workflow '{workflow_id}'")
    div = resolve_division(division)
    rubric = llm_analyzer.WORKFLOW_RUBRICS.get(workflow_id, {})
    return {
        "definition": workflow_repo.get_definition(db, workflow_id, div),
        "approvals": approval_repo.list_pending(db, div, workflow_id=workflow_id),
        "records": workflow_repo.record_summary(db, workflow_id, div),
        "record_rows": workflow_repo.list_records(db, workflow_id, div, limit=25),
        "record_files": workflow_repo.record_files(db, workflow_id, div),
        "required_documents": workflow_repo.required_documents(db, workflow_id, div),
        "rubric": rubric.get("requirements", []),
        "analysis_configured": llm_analyzer.has_api_key(),
        "model": llm_analyzer.MODEL,
    }


@app.put("/workflows/{workflow_id}/definition")
def put_workflow_definition(workflow_id: str, payload: DefinitionUpdateRequest, db: Session = Depends(get_db)):
    """Rewrite the steps from an edited narrative.

    This is the write end of the single source of truth: the diagram, the
    narrative and the run all read what this stores.
    """
    if workflow_id not in workflow_repo.WORKFLOW_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown workflow '{workflow_id}'")
    try:
        return workflow_repo.update_definition(
            db,
            workflow_id,
            resolve_division(payload.division),
            [s.model_dump() for s in payload.steps],
            updated_by=payload.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/workflows/{workflow_id}/definition/reset")
def reset_workflow_definition(workflow_id: str, payload: DefinitionUpdateRequest, db: Session = Depends(get_db)):
    try:
        return workflow_repo.reset_definition(
            db, workflow_id, resolve_division(payload.division), updated_by=payload.updated_by
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/workflows/{workflow_id}/run")
async def run_workflow(
    workflow_id: str,
    division: str = Form("mf"),
    property_id: str = Form(""),
    unit: str = Form(""),
    actor: str = Form(""),
    upload_file: Optional[UploadFile] = File(None),
):
    """Run the use case, streaming one JSON event per step as it happens.

    The response is newline-delimited JSON rather than a single object so the
    status bar can move while the run is still going — the model call is the
    slow part and a run that only reports at the end is not a status bar.

    Unauthenticated for the same reason as /dashboard/summary. It accepts
    uploads and spends API tokens, so gate it before exposing beyond localhost.
    """
    if workflow_id not in workflow_repo.WORKFLOW_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown workflow '{workflow_id}'")

    div = resolve_division(division)

    attachment = None
    if upload_file is not None and upload_file.filename:
        contents = await upload_file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="The attached file is empty.")
        if len(contents) > MAX_RUN_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Attached file is larger than the 20 MB limit.")
        if not llm_analyzer.has_api_key():
            raise HTTPException(
                status_code=503,
                detail="ANTHROPIC_API_KEY is not set, so an attached document cannot be graded. "
                "Add it to .env and restart, or run without an attachment.",
            )
        attachment = workflow_runner.Attachment(
            contents, upload_file.filename, upload_file.content_type or "application/octet-stream"
        )

    def stream():
        # A session of our own rather than Depends(get_db): dependency teardown
        # runs before the response body is streamed, which would close the
        # session out from under the run.
        with SessionLocal() as db:
            try:
                for event in workflow_runner.run(
                    db,
                    workflow_id=workflow_id,
                    division=div,
                    property_id=property_id.strip(),
                    unit=unit.strip(),
                    actor=actor.strip(),
                    attachment=attachment,
                ):
                    yield json.dumps(event) + "\n"
            except Exception as exc:  # a failed run must still say so
                db.rollback()
                yield json.dumps({"type": "error", "message": str(exc) or exc.__class__.__name__}) + "\n"

    return StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


@app.get("/workflows/{workflow_id}/records.csv")
def get_workflow_records_csv(workflow_id: str, division: str = "mf", db: Session = Depends(get_db)):
    """The workflow's record file, openable straight from the use case page."""
    if workflow_id not in workflow_repo.WORKFLOW_CATALOG:
        raise HTTPException(status_code=404, detail=f"Unknown workflow '{workflow_id}'")
    body = workflow_repo.records_csv(db, workflow_id, resolve_division(division))
    return Response(
        content="﻿" + body,  # BOM so Excel reads UTF-8
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{workflow_id}-records.csv"'},
    )


# ---------------- Reference ----------------

@app.get("/reference")
def reference(division: str = "mf", db: Session = Depends(get_db)):
    """The division's reference page: a rollup of every use case, plus the
    shared vocabulary those use case narratives are written against."""
    div = resolve_division(division)
    counts = approval_repo.pending_counts(db, div)
    return {
        "division": div.value,
        "use_cases": [
            {
                "id": wf_id,
                "title": wf["title"],
                "folder": wf["folder"],
                "purpose": wf["purpose"],
                "steps": [
                    {"title": s["title"], "kind": s["kind"], "summary": s["summary"]}
                    for s in workflow_repo.get_definition(db, wf_id, div)["steps"]
                ],
                "rubric": llm_analyzer.WORKFLOW_RUBRICS.get(wf_id, {}).get("requirements", []),
                "records": workflow_repo.record_summary(db, wf_id, div),
                "approvals": counts.get(wf_id, 0),
                "record_file": f"/workflows/{wf_id}/records.csv?division={workflow_repo.division_key(div)}",
            }
            for wf_id, wf in workflow_repo.WORKFLOW_CATALOG.items()
        ],
        "glossary": workflow_repo.GLOSSARY,
        "step_kinds": workflow_repo.STEP_KINDS,
        "folders": CORE_FOLDERS,
    }


# ---------------- Approvals ----------------

@app.get("/approvals")
def list_approvals(division: str = "mf", workflow: str = "", db: Session = Depends(get_db)):
    """Pending cases plus a per-workflow count, so the UI can group them."""
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


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("aat_system.main:app", host="127.0.0.1", port=8000, reload=True)
