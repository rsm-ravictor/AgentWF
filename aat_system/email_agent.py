import email
import imaplib
import os
from pathlib import Path
from typing import Optional
from .config import EMAIL_FOLDER_KEYWORDS, Division, IMAP_HOST, IMAP_USER, IMAP_PASSWORD, IMAP_FOLDER, UPLOAD_ROOT
from .document_repo import ingest_document
from .db import SessionLocal
from .models import User
from .utils import normalize_filename

HOST = IMAP_HOST
USER = IMAP_USER
PASSWORD = IMAP_PASSWORD
FOLDER = IMAP_FOLDER


def extract_folder_name(subject: str, body: str) -> Optional[str]:
    search_text = f"{subject}\n{body}".lower()
    for folder, keywords in EMAIL_FOLDER_KEYWORDS.items():
        for keyword in keywords:
            if keyword in search_text:
                return folder
    return None


def download_pdf_attachments(message, target_directory: Path) -> list[Path]:
    attachments = []
    for part in message.walk():
        filename = part.get_filename()
        if filename and filename.lower().endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                safe_name = normalize_filename(filename)
                path = target_directory / safe_name
                path.write_bytes(payload)
                attachments.append(path)
    return attachments


def process_inbox(division: Division, owner_email: str):
    user = None
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == owner_email).first()
        if not user:
            raise ValueError("Owner email not found in user records.")

    mail = imaplib.IMAP4_SSL(HOST)
    mail.login(USER, PASSWORD)
    mail.select(FOLDER)
    status, data = mail.search(None, "ALL")
    if status != "OK":
        raise RuntimeError("Failed to search inbox.")

    target_directory = UPLOAD_ROOT
    target_directory.mkdir(parents=True, exist_ok=True)

    for num in data[0].split():
        status, msg_data = mail.fetch(num, "(RFC822)")
        if status != "OK":
            continue
        message = email.message_from_bytes(msg_data[0][1])
        subject = message.get("subject", "")
        body = ""
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain" and part.get_payload(decode=True):
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
        else:
            body = message.get_payload(decode=True).decode(errors="ignore")

        folder_name = extract_folder_name(subject, body) or "AAT Company Requirements/Documents"
        attachments = download_pdf_attachments(message, target_directory)
        for attachment in attachments:
            ingest_document(SessionLocal(), owner=user, source_path=attachment, folder_name=folder_name, division=division)

    mail.logout()


def extract_folder_name(subject: str, body: str) -> Optional[str]:
    search_text = f"{subject}\n{body}".lower()
    for folder, keywords in EMAIL_FOLDER_KEYWORDS.items():
        for keyword in keywords:
            if keyword in search_text:
                return folder
    return None


def download_pdf_attachments(message, target_directory: Path) -> list[Path]:
    attachments = []
    for part in message.walk():
        content_type = part.get_content_type()
        filename = part.get_filename()
        if filename and filename.lower().endswith(".pdf"):
            payload = part.get_payload(decode=True)
            if payload:
                path = target_directory / filename
                path.write_bytes(payload)
                attachments.append(path)
    return attachments


def process_inbox(division: Division, owner_email: str):
    user = None
    with SessionLocal() as db:
        user = db.query(User).filter(User.email == owner_email).first()
        if not user:
            raise ValueError("Owner email not found in user records.")

    mail = imaplib.IMAP4_SSL(IMAP_HOST)
    mail.login(IMAP_USER, IMAP_PASSWORD)
    mail.select(IMAP_FOLDER)
    status, data = mail.search(None, "ALL")
    if status != "OK":
        raise RuntimeError("Failed to search inbox.")

    target_directory = Path("uploaded_files")
    target_directory.mkdir(exist_ok=True)

    for num in data[0].split():
        status, msg_data = mail.fetch(num, "(RFC822)")
        if status != "OK":
            continue
        message = email.message_from_bytes(msg_data[0][1])
        subject = message.get("subject", "")
        body = ""
        if message.is_multipart():
            for part in message.walk():
                if part.get_content_type() == "text/plain" and part.get_payload(decode=True):
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
        else:
            body = message.get_payload(decode=True).decode(errors="ignore")

        folder_name = extract_folder_name(subject, body) or "AAT Company Requirements/Documents"
        attachments = download_pdf_attachments(message, target_directory)
        for attachment in attachments:
            ingest_document(SessionLocal(), owner=user, source_path=attachment, folder_name=folder_name, division=division)

    mail.logout()
