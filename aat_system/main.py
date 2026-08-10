import os
from pathlib import Path
from datetime import datetime, timedelta
from fastapi import FastAPI, UploadFile, File, Depends, Form, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session
from .db import Base, engine, SessionLocal, get_db
from .models import User, Folder, Document, Lease
from .auth import assert_division_access, assert_folder_access, get_allowed_folders
from .document_repo import ingest_document, scan_expired_leases, get_or_create_folder
from .config import Division, Role, CORE_FOLDERS
from .security import authenticate_user, create_access_token, get_current_active_user, get_password_hash
from .utils import ensure_storage_directories

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
