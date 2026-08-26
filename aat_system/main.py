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
from .config import (
    ACTIVE_DIVISIONS,
    ARCHIVE_ROOT,
    DEFAULT_DIVISION,
    DEFAULT_DIVISION_KEY,
    DIVISION_KEYS,
    DIVISION_LABELS,
    Division,
    Permission,
    Role,
    division_key,
    folders_for,
    resolve_division_key,
)
from .security import authenticate_user, create_access_token, get_current_active_user, get_password_hash
from .utils import ensure_storage_directories
from . import (
    llm_analyzer,
    approval_repo,
    permission_repo,
    user_repo,
    workflow_catalog,
    workflow_repo,
    workflow_runner,
)

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


class RequiredDocumentPayload(BaseModel):
    name: str
    # Filename keywords that count as a match. Kept explicit so present/missing
    # stays a deterministic answer rather than a guess.
    match: List[str] = []
    folder: Optional[str] = None


class UseCaseCreateRequest(BaseModel):
    division: str = DEFAULT_DIVISION_KEY
    title: str
    folder: str
    purpose: str = ""
    documents: List[RequiredDocumentPayload] = []
    # What the analysis step grades against. Without these a run has nothing to
    # check, so the UI asks for them at creation time.
    rubric: List[str] = []
    document_kinds: str = ""
    created_by: Optional[str] = None


class UseCaseUpdateRequest(BaseModel):
    division: str = DEFAULT_DIVISION_KEY
    # All optional: a rename should not have to restate the whole use case.
    title: Optional[str] = None
    folder: Optional[str] = None
    purpose: Optional[str] = None
    documents: Optional[List[RequiredDocumentPayload]] = None
    rubric: Optional[List[str]] = None
    document_kinds: Optional[str] = None
    updated_by: Optional[str] = None


class UseCaseArchiveRequest(BaseModel):
    division: str = DEFAULT_DIVISION_KEY
    archived: bool = True
    updated_by: Optional[str] = None


class DefinitionUpdateRequest(BaseModel):
    division: str = DEFAULT_DIVISION_KEY
    steps: List[StepPayload] = []
    updated_by: str = ""


class ProfileCreateRequest(BaseModel):
    name: str = ""
    email: str
    division: str = DEFAULT_DIVISION_KEY
    role: Role = Role.GENERAL
    password: str = ""
    acting_user_id: Optional[int] = None


class RolePermissionsRequest(BaseModel):
    division: str = DEFAULT_DIVISION_KEY
    permissions: List[str] = []
    updated_by: str = ""


class SessionResolveRequest(BaseModel):
    email: str
    division: str = DEFAULT_DIVISION_KEY
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
# The mapping itself lives in config, so adding a business line is one edit.
def resolve_division(key: str) -> Division:
    return resolve_division_key(key)


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
            # Each division gets its own folder set — Construction's includes
            # permits, change orders and lien waivers that the others have no use
            # for.
            for folder_name in folders_for(division):
                get_or_create_folder(db, folder_name, division)
        permission_repo.ensure_seeded(db)
        user_repo.seed_roster(db)
        # The shipped use cases, for the divisions that ship with them. Only
        # Office/Retail is active, so only it is seeded; the paused divisions
        # keep whatever rows they already had.
        workflow_catalog.seed(
            db, workflow_repo.OFFICE_CATALOG, llm_analyzer.WORKFLOW_RUBRICS
        )
        # Illustrative cases so the queue is not empty on a fresh install. They
        # are tagged 'sample' in the UI and clearable from the dashboard. Scoped
        # to the use cases the division actually has, so a sample can always be
        # opened from the queue it appears in.
        for division in ACTIVE_DIVISIONS:
            approval_repo.seed_samples(
                db, division, known_ids=[uc["id"] for uc in workflow_catalog.catalog(db, division)]
            )


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
def repository_documents(division: str = DEFAULT_DIVISION_KEY, folder: str = "", q: str = "", db: Session = Depends(get_db)):
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
def dashboard_summary(division: str = DEFAULT_DIVISION_KEY, db: Session = Depends(get_db)):
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
        for name in folders_for(div)
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
    for wf in workflow_catalog.catalog(db, div):
        wf_id = wf["id"]
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
        "division_key": division_key(div),
        "division_label": DIVISION_LABELS.get(div, div.value),
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
        # Which route decisions are made through, so the UI names it rather than
        # implying every model call goes to the same place.
        "llm_route": llm_analyzer.active_route(),
    }


# ---------------- Use cases ----------------

@app.get("/workflows")
def list_workflows(division: str = DEFAULT_DIVISION_KEY, include_archived: bool = False, db: Session = Depends(get_db)):
    """The division's use cases — the catalog the dashboard and top bar render."""
    div = resolve_division(division)
    return {
        "division": div.value,
        "division_key": workflow_repo.division_key(div),
        "use_cases": workflow_catalog.catalog(db, div, include_archived=include_archived),
        "folders": folders_for(div),
        "step_kinds": workflow_repo.STEP_KINDS,
    }


@app.post("/workflows", status_code=201)
def create_workflow(payload: UseCaseCreateRequest, db: Session = Depends(get_db)):
    """Add a use case to a division.

    The steps are not written here: the first read of the definition seeds the
    starter spine, the same path the shipped use cases take. So a use case is
    runnable the moment it is created, and its author edits wording rather than
    building a workflow from an empty page.
    """
    div = resolve_division(payload.division)
    try:
        entry = workflow_catalog.create(
            db,
            div,
            title=payload.title,
            folder=payload.folder,
            purpose=payload.purpose,
            documents=[d.model_dump() for d in payload.documents],
            rubric=payload.rubric,
            document_kinds=payload.document_kinds,
            created_by=payload.created_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    # Writes version 1 from a clean slate, so a slug carrying leftover rows from
    # the shared-catalog era cannot hand this use case another division's steps.
    definition = workflow_repo.seed_definition(
        db, entry["id"], div, updated_by=payload.created_by
    )
    return {"use_case": entry, "definition": definition}


@app.patch("/workflows/{workflow_id}")
def update_workflow(workflow_id: str, payload: UseCaseUpdateRequest, db: Session = Depends(get_db)):
    """Rename a use case, repoint its folder, or change what it requires.

    The slug is left alone, so records, approvals and revision history stay
    attached to the use case across a rename.
    """
    div = resolve_division(payload.division)
    documents = (
        None if payload.documents is None else [d.model_dump() for d in payload.documents]
    )
    try:
        return {
            "use_case": workflow_catalog.update(
                db,
                workflow_id,
                div,
                title=payload.title,
                folder=payload.folder,
                purpose=payload.purpose,
                documents=documents,
                rubric=payload.rubric,
                document_kinds=payload.document_kinds,
                updated_by=payload.updated_by,
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/workflows/{workflow_id}/archive")
def archive_workflow(workflow_id: str, payload: UseCaseArchiveRequest, db: Session = Depends(get_db)):
    """Retire a use case, or bring it back.

    Retired rather than deleted: the runs it recorded and the approvals it raised
    are history that should outlive it being taken out of service.
    """
    div = resolve_division(payload.division)
    try:
        return {
            "use_case": workflow_catalog.set_archived(
                db, workflow_id, div, payload.archived, updated_by=payload.updated_by
            )
        }
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/workflows/{workflow_id}")
def workflow_detail(workflow_id: str, division: str = DEFAULT_DIVISION_KEY, db: Session = Depends(get_db)):
    """Everything the use case detail page renders: definition, state, rubric."""
    div = resolve_division(division)
    if not workflow_catalog.exists(db, workflow_id, div, include_archived=True):
        raise HTTPException(
            status_code=404, detail=f"Unknown workflow '{workflow_id}' in {div.value}"
        )
    entry = workflow_catalog.entry(db, workflow_id, div, include_archived=True)
    return {
        "definition": workflow_repo.get_definition(db, workflow_id, div),
        "version": workflow_repo.current_version(db, workflow_id, div),
        "approvals": approval_repo.list_pending(db, div, workflow_id=workflow_id),
        "records": workflow_repo.record_summary(db, workflow_id, div),
        "record_rows": workflow_repo.list_records(db, workflow_id, div, limit=25),
        "record_files": workflow_repo.record_files(db, workflow_id, div),
        "required_documents": workflow_repo.required_documents(db, workflow_id, div),
        # From the use case itself, so one created in the UI shows and grades
        # against its own requirements rather than falling back on a constant.
        "rubric": entry["rubric"],
        "document_kinds": entry["document_kinds"],
        "archived": entry["archived"],
        "analysis_configured": llm_analyzer.has_api_key(),
        "model": llm_analyzer.MODEL,
        # Which route decisions are made through, so the UI names it rather than
        # implying every model call goes to the same place.
        "llm_route": llm_analyzer.active_route(),
    }


@app.put("/workflows/{workflow_id}/definition")
def put_workflow_definition(workflow_id: str, payload: DefinitionUpdateRequest, db: Session = Depends(get_db)):
    """Rewrite the steps from an edited narrative.

    This is the write end of the single source of truth: the diagram, the
    narrative and the run all read what this stores.
    """
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
    division: str = Form(DEFAULT_DIVISION_KEY),
    property_id: str = Form(""),
    unit: str = Form(""),
    actor: str = Form(""),
    inputs: str = Form(""),
    upload_file: Optional[UploadFile] = File(None),
):
    """Run the use case, streaming one JSON event per step as it happens.

    The response is newline-delimited JSON rather than a single object so the
    status bar can move while the run is still going — the model call is the
    slow part and a run that only reports at the end is not a status bar.

    Unauthenticated for the same reason as /dashboard/summary. It accepts
    uploads and spends API tokens, so gate it before exposing beyond localhost.
    """
    div = resolve_division(division)
    # A session of its own: this route takes no Depends(get_db), because
    # dependency teardown would close the session before the body streams.
    with SessionLocal() as check_db:
        if not workflow_catalog.exists(check_db, workflow_id, div):
            raise HTTPException(
                status_code=404, detail=f"Unknown workflow '{workflow_id}' in {div.value}"
            )

    # The answers to whatever questions this use case declares, as JSON: the set
    # differs per use case, so it cannot be a fixed list of form fields. Bad JSON
    # is refused rather than silently dropped, because a run that quietly lost
    # the incident summary would search the lease against nothing.
    answers = {}
    if inputs.strip():
        try:
            answers = json.loads(inputs)
        except ValueError:
            raise HTTPException(status_code=400, detail="Run inputs were not valid JSON.")
        if not isinstance(answers, dict):
            raise HTTPException(status_code=400, detail="Run inputs must be an object.")
        answers = {str(k): ("" if v is None else str(v)) for k, v in answers.items()}

    attachment = None
    if upload_file is not None and upload_file.filename:
        contents = await upload_file.read()
        if not contents:
            raise HTTPException(status_code=400, detail="The attached file is empty.")
        if len(contents) > MAX_RUN_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="Attached file is larger than the 20 MB limit.")
        if not llm_analyzer.has_api_key():
            route = llm_analyzer.active_route()
            raise HTTPException(
                status_code=503,
                detail=f"{route['key_env']} is not set, so an attached document cannot be "
                f"graded through the {route['provider']} route. Add it to .env and restart, "
                "or run without an attachment.",
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
                    inputs=answers,
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
def get_workflow_records_csv(workflow_id: str, division: str = DEFAULT_DIVISION_KEY, db: Session = Depends(get_db)):
    """The workflow's record file, openable straight from the use case page."""
    div = resolve_division(division)
    if not workflow_catalog.exists(db, workflow_id, div, include_archived=True):
        raise HTTPException(
            status_code=404, detail=f"Unknown workflow '{workflow_id}' in {div.value}"
        )
    body = workflow_repo.records_csv(db, workflow_id, div)
    return Response(
        content="﻿" + body,  # BOM so Excel reads UTF-8
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{workflow_id}-records.csv"'},
    )


# ---------------- Reference ----------------

@app.get("/reference")
def reference(division: str = DEFAULT_DIVISION_KEY, db: Session = Depends(get_db)):
    """The division's reference page: a rollup of every use case, plus the
    shared vocabulary those use case narratives are written against."""
    div = resolve_division(division)
    counts = approval_repo.pending_counts(db, div)
    return {
        "division": div.value,
        "use_cases": [
            {
                "id": wf["id"],
                "title": wf["title"],
                "folder": wf["folder"],
                "purpose": wf["purpose"],
                "steps": [
                    {"title": s["title"], "kind": s["kind"], "summary": s["summary"]}
                    for s in workflow_repo.get_definition(db, wf["id"], div)["steps"]
                ],
                "rubric": wf["rubric"],
                "records": workflow_repo.record_summary(db, wf["id"], div),
                "approvals": counts.get(wf["id"], 0),
                "record_file": f"/workflows/{wf['id']}/records.csv?division={workflow_repo.division_key(div)}",
            }
            for wf in workflow_catalog.catalog(db, div)
        ],
        "glossary": workflow_repo.GLOSSARY,
        "step_kinds": workflow_repo.STEP_KINDS,
        "folders": folders_for(div),
        # Division-wide definition history: what changed, when, by whom, and the
        # version to roll back to if a change broke something.
        "change_log": workflow_repo.change_log(db, div),
        "revision_sources": workflow_repo.REVISION_SOURCES,
    }


@app.get("/workflows/{workflow_id}/history")
def workflow_history(workflow_id: str, division: str = DEFAULT_DIVISION_KEY, db: Session = Depends(get_db)):
    """Every version this workflow's definition has had, newest first."""
    div = resolve_division(division)
    if not workflow_catalog.exists(db, workflow_id, div, include_archived=True):
        raise HTTPException(
            status_code=404, detail=f"Unknown workflow '{workflow_id}' in {div.value}"
        )
    workflow_repo.get_definition(db, workflow_id, div)  # seeds version 1 on first look
    return {
        "workflow_id": workflow_id,
        "title": workflow_catalog.entry(db, workflow_id, div, include_archived=True)["title"],
        "current_version": workflow_repo.current_version(db, workflow_id, div),
        "revisions": workflow_repo.list_revisions(db, workflow_id, div),
    }


@app.post("/workflows/{workflow_id}/revisions/{version}/restore")
def restore_workflow_revision(
    workflow_id: str,
    version: int,
    payload: DefinitionUpdateRequest,
    db: Session = Depends(get_db),
):
    """Put a past version of a definition back as the live one.

    The rollback is recorded as a new version rather than a rewind, so the
    history stays complete and a rollback can itself be rolled back.
    """
    try:
        return workflow_repo.restore_revision(
            db,
            workflow_id,
            resolve_division(payload.division),
            version,
            updated_by=payload.updated_by,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


# ---------------- Approvals ----------------

@app.get("/approvals")
def list_approvals(division: str = DEFAULT_DIVISION_KEY, workflow: str = "", db: Session = Depends(get_db)):
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
def delete_sample_approvals(division: str = DEFAULT_DIVISION_KEY, db: Session = Depends(get_db)):
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
    signing_into = resolve_division(payload.division)
    user = user_repo.resolve_session_user(db, payload.email, signing_into, name=payload.name)
    # An account homed to a paused division still signs in; it just does not get
    # to land there, because nothing in the UI routes to a paused division.
    user = user_repo.session_division(db, user, signing_into)
    return {"profile": user_repo.profile(user, db)}


@app.get("/session/accounts")
def session_accounts(db: Session = Depends(get_db)):
    """The seeded accounts the login screen offers as a quick pick.

    Prototype affordance: because the username is what sets the access level,
    the roles have to be discoverable rather than memorised. Remove this with the
    simulated login when real authentication lands.
    """
    return {"accounts": user_repo.roster_accounts(db)}


@app.get("/roles")
def list_roles(division: str = DEFAULT_DIVISION_KEY, db: Session = Depends(get_db)):
    """Every level, its rank, and what it grants in the given division."""
    div = resolve_division(division)
    return {
        "roles": user_repo.role_catalog(db, div),
        "permissions": user_repo.permission_catalog(),
        "divisions": permission_repo.division_catalog(),
    }


@app.get("/admin/users")
def admin_list_users(division: str = "", db: Session = Depends(get_db)):
    """The roster. Scoped to one division unless asked for all of them.

    Settings passes the division being administered, because a super admin runs
    one business line: seeing every account in the company is a different
    capability (`view_all_divisions`) that no level holds by default.
    """
    div = DIVISION_KEYS.get(division) if division else None
    return {
        "users": user_repo.list_users(db, division=div),
        "roles": user_repo.role_catalog(db, div or DEFAULT_DIVISION),
        "permissions": user_repo.permission_catalog(),
        "divisions": permission_repo.division_catalog(),
    }


# ---------------- Permissions: what each role may do ----------------

def _permission_payload(db: Session, div: Division) -> dict:
    return {
        "division": div.value,
        "division_key": division_key(div),
        "division_label": DIVISION_LABELS.get(div, div.value),
        "divisions": permission_repo.division_catalog(),
        "roles": permission_repo.matrix(db, div),
        "permissions": permission_repo.catalog(),
    }


@app.get("/permissions")
def get_permissions(division: str = DEFAULT_DIVISION_KEY, db: Session = Depends(get_db)):
    """One division's level × permission matrix as currently configured.

    Per division because the levels are per division: Construction's admin is a
    different person from Residential's, and may be allowed different things.
    `default` alongside `granted` is what "Restore defaults" goes back to, so the
    page can show where the live configuration has been changed.
    """
    return _permission_payload(db, resolve_division(division))


@app.put("/permissions/{role}")
def put_role_permissions(
    role: Role, payload: RolePermissionsRequest, db: Session = Depends(get_db)
):
    """Replace what one level may do in one division.

    This changes what the system allows, not merely what the UI offers: every
    gate resolves through the same configuration.
    """
    div = resolve_division(payload.division)
    permission_repo.set_for(db, div, role, payload.permissions, updated_by=payload.updated_by)
    return _permission_payload(db, div)


@app.post("/permissions/reset")
def reset_permissions(payload: RolePermissionsRequest, db: Session = Depends(get_db)):
    """Put one division's levels back to the permissions the system ships with."""
    div = resolve_division(payload.division)
    permission_repo.reset(db, div, updated_by=payload.updated_by)
    return _permission_payload(db, div)


@app.post("/admin/users")
def admin_create_user(payload: ProfileCreateRequest, db: Session = Depends(get_db)):
    """Create a profile from the Settings page.

    Gated on the acting account holding MANAGE_USERS, same as changing one. What
    that role grants is configurable on the same page, so this is a gate someone
    can open deliberately rather than a wall.
    """
    actor = db.get(User, payload.acting_user_id) if payload.acting_user_id else None
    if actor is None:
        raise HTTPException(status_code=403, detail="An acting account is required.")
    if not permission_repo.role_has(db, actor.division, actor.role, Permission.MANAGE_USERS):
        raise HTTPException(
            status_code=403,
            detail=(
                f"Your level ({actor.role.value}) does not grant 'manage_users'. "
                "Grant it under Role permissions to create accounts."
            ),
        )

    target_division = resolve_division(payload.division)
    # An admin runs one division. Creating accounts in another needs the
    # cross-division permission, which no level holds by default.
    if target_division != actor.division and not permission_repo.role_has(
        db, actor.division, actor.role, Permission.VIEW_ALL_DIVISIONS
    ):
        raise HTTPException(
            status_code=403,
            detail=(
                f"You administer {actor.division.value}. Creating an account in "
                f"{target_division.value} needs 'view_all_divisions'."
            ),
        )
    if payload.role == Role.SUPER_ADMIN and actor.role != Role.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Only a super admin can create a super admin.")

    try:
        user = user_repo.create_account(
            db,
            email=payload.email,
            name=payload.name,
            division=resolve_division(payload.division),
            role=payload.role,
            password=payload.password,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"user": user_repo.profile(user, db)}


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
    if not permission_repo.role_has(db, actor.division, actor.role, needed):
        raise HTTPException(
            status_code=403,
            detail=f"Your level ({actor.role.value}) does not grant '{needed.value}'.",
        )

    # An account in another division is someone else's to administer.
    if target.division != actor.division and not permission_repo.role_has(
        db, actor.division, actor.role, Permission.VIEW_ALL_DIVISIONS
    ):
        raise HTTPException(
            status_code=403,
            detail=f"That account belongs to {target.division.value}, which you do not administer.",
        )

    # Only a super admin can create or unmake another super admin.
    touches_super = Role.SUPER_ADMIN in (payload.role, target.role)
    if touches_super and actor.role != Role.SUPER_ADMIN:
        raise HTTPException(
            status_code=403, detail="Only a super admin can change super-admin access."
        )

    if actor.id == target.id and payload.role is not None and payload.role != actor.role:
        raise HTTPException(status_code=400, detail="You cannot change your own level.")

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
    return {"user": user_repo.profile(target, db)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("aat_system.main:app", host="127.0.0.1", port=8000, reload=True)
