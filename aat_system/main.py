import os
from pathlib import Path
from datetime import datetime, timedelta
import anthropic
from fastapi import FastAPI, UploadFile, File, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session
from .db import Base, engine, SessionLocal, get_db
from .models import User, Folder, Document, Lease
from .auth import assert_division_access, assert_folder_access, get_allowed_folders
from .document_repo import ingest_document, scan_expired_leases, get_or_create_folder
from .config import Division, Role, CORE_FOLDERS
from .security import authenticate_user, create_access_token, get_current_active_user, get_password_hash
from .utils import ensure_storage_directories
from . import llm_analyzer, dar_analyzer, dar_repo

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

app = FastAPI(title="AAT System")

# The UI identifies divisions by short key; the DB stores the full enum value.
DIVISION_KEYS = {"mf": Division.MULTIFAMILY, "retail": Division.OFFICE}

static_dir = Path(__file__).resolve().parent.parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

@app.get("/", response_class=HTMLResponse)
def read_root():
    html_path = static_dir / "index.html"
    if not html_path.exists():
        raise HTTPException(status_code=404, detail="UI preview not available")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))

@app.on_event("startup")
def startup_event():
    Base.metadata.create_all(bind=engine)
    ensure_storage_directories()
    with SessionLocal() as db:
        for division in Division:
            for folder_name in CORE_FOLDERS:
                get_or_create_folder(db, folder_name, division)

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
    upload_file: UploadFile = File(...),
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

    return {
        "workflow": workflow,
        "filename": upload_file.filename,
        "verdict": verdict.model_dump(),
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

    return {
        "division": div.value,
        "folders": folders,
        "documents_total": sum(f["count"] for f in folders),
        "leases_expiring_soon": leases_expiring,
        "leases_expired": leases_expired,
        "leases_total": db.query(func.count(Lease.id)).scalar() or 0,
        "dar": register["totals"],
        "escalations": escalations,
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
