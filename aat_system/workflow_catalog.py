"""Which use cases a division has — the catalog, held at runtime.

`workflow_repo.WORKFLOW_CATALOG` is the *shipped* set: what a division starts
from. This module owns the live set, one row per use case per division, because
the shipped dict had two limits that a second business line makes untenable:
every division saw the same use cases, and adding one meant editing code.

Keyed by division, so Office/Retail can hold its own use cases, named its own
way, without pretending its paperwork is Residential's — and a division can
start empty and be built up from the UI.

The split of responsibility with `workflow_repo` is: identity here, behaviour
there. A row here says what a use case *is* — its title, the folder it reads,
the documents it requires, the requirements it grades against. The steps it
executes, the versions it has had, and the runs it recorded all live in
`workflow_repo`, keyed by the same (workflow_id, division) pair. That pair is
why nothing here deletes: a retired use case's records and approvals are
history, so retiring sets a flag and leaves the trail intact.

This module deliberately does not import `workflow_repo` — the dependency runs
one way, so a use case's steps are seeded on first read by the existing lazy
path rather than by two modules writing the same table.
"""

import json
import re
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from .config import Division, folders_for
from .models import WorkflowDefinition

# Divisions that ship with the Phase 1 set. Office/Retail is deliberately
# absent: it starts empty and is built up from the UI, which is the whole point
# of holding the catalog in the database rather than in code.
SEEDED_DIVISIONS = (Division.MULTIFAMILY, Division.CONSTRUCTION)


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug or "use-case"


# ---------------- Reading ----------------

def _row(
    db: Session, workflow_id: str, division: Division, include_archived: bool = False
) -> Optional[WorkflowDefinition]:
    query = db.query(WorkflowDefinition).filter(
        WorkflowDefinition.workflow_id == workflow_id,
        WorkflowDefinition.division == division,
    )
    if not include_archived:
        query = query.filter(WorkflowDefinition.archived.is_(False))
    return query.first()


def _as_dict(row: WorkflowDefinition) -> dict:
    """One row in the shape the rest of the system reads a use case in.

    Keeps the keys the shipped catalog entries had — title, folder, purpose,
    documents — so a caller handles a use case the same way whether it shipped
    or was created this morning.
    """
    return {
        "id": row.workflow_id,
        "title": row.title,
        "folder": row.folder,
        "purpose": row.purpose or "",
        "documents": json.loads(row.documents or "[]"),
        "rubric": json.loads(row.rubric or "[]"),
        "document_kinds": row.document_kinds or "",
        "position": row.position,
        "shipped": bool(row.shipped),
        "archived": bool(row.archived),
        "created_by": row.created_by,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "updated_by": row.updated_by,
    }


def entry(
    db: Session, workflow_id: str, division: Division, include_archived: bool = False
) -> dict:
    """One use case's identity. Raises ValueError if this division has no such one."""
    row = _row(db, workflow_id, division, include_archived)
    if row is None:
        raise ValueError(f"Unknown workflow '{workflow_id}' in {division.value}")
    return _as_dict(row)


def exists(
    db: Session, workflow_id: str, division: Division, include_archived: bool = False
) -> bool:
    return _row(db, workflow_id, division, include_archived) is not None


def catalog(db: Session, division: Division, include_archived: bool = False) -> List[dict]:
    """Every use case this division has, in display order."""
    query = db.query(WorkflowDefinition).filter(WorkflowDefinition.division == division)
    if not include_archived:
        query = query.filter(WorkflowDefinition.archived.is_(False))
    rows = query.order_by(
        WorkflowDefinition.position.asc(), WorkflowDefinition.id.asc()
    ).all()
    return [_as_dict(r) for r in rows]


def titles(db: Session, division: Division) -> dict:
    """workflow_id -> title, including retired ones.

    The change log and revision history name use cases that may since have been
    retired; without the retired ones those entries would read as bare slugs.
    """
    return {
        row.workflow_id: row.title
        for row in db.query(WorkflowDefinition).filter(
            WorkflowDefinition.division == division
        )
    }


# ---------------- Writing ----------------

def _clean_documents(documents: Optional[list], folder: str) -> list:
    """Normalise the intake checklist, dropping entries that could never match.

    A required document with no keywords can never be satisfied, so it would
    block every run of the use case forever. That is a broken use case rather
    than a strict one, so it is dropped here rather than at run time.
    """
    cleaned = []
    for spec in documents or []:
        if not isinstance(spec, dict):
            continue
        name = (spec.get("name") or "").strip()
        keywords = spec.get("match") or []
        if isinstance(keywords, str):
            keywords = re.split(r"[,\n]", keywords)
        keywords = [k.strip().lower() for k in keywords if k and k.strip()]
        if not name or not keywords:
            continue
        item = {"name": name, "match": keywords}
        spec_folder = (spec.get("folder") or "").strip()
        if spec_folder and spec_folder != folder:
            item["folder"] = spec_folder
        cleaned.append(item)
    return cleaned


def _clean_rubric(rubric) -> list:
    if isinstance(rubric, str):
        rubric = rubric.split("\n")
    return [r.strip() for r in (rubric or []) if r and r.strip()]


def _validate_folder(division: Division, folder: str) -> str:
    cleaned = (folder or "").strip()
    allowed = folders_for(division)
    if cleaned not in allowed:
        raise ValueError(
            f"'{cleaned}' is not a folder in {division.value}. "
            f"Expected one of: {', '.join(allowed)}."
        )
    return cleaned


def _unique_slug(db: Session, division: Division, title: str) -> str:
    """A slug free within this division, counting retired use cases.

    Retired ones count because records, approvals and revisions are all keyed by
    workflow_id: reusing a retired slug would silently adopt its history.
    """
    base = _slug(title)
    candidate = base
    suffix = 2
    while _row(db, candidate, division, include_archived=True) is not None:
        candidate = f"{base}-{suffix}"
        suffix += 1
    return candidate


def create(
    db: Session,
    division: Division,
    title: str,
    folder: str,
    purpose: str = "",
    documents: Optional[list] = None,
    rubric: Optional[list] = None,
    document_kinds: str = "",
    created_by: Optional[str] = None,
) -> dict:
    """Add a use case to one division.

    Its steps are not written here: the first read seeds them through
    `workflow_repo.get_definition`, the same path the shipped use cases take, so
    there is one place that writes step rows and one meaning for "version 1".
    """
    title = (title or "").strip()
    if not title:
        raise ValueError("A use case needs a title.")
    folder = _validate_folder(division, folder)

    highest = (
        db.query(func.max(WorkflowDefinition.position))
        .filter(WorkflowDefinition.division == division)
        .scalar()
    )
    row = WorkflowDefinition(
        workflow_id=_unique_slug(db, division, title),
        division=division,
        title=title,
        folder=folder,
        purpose=(purpose or "").strip(),
        documents=json.dumps(_clean_documents(documents, folder)),
        rubric=json.dumps(_clean_rubric(rubric)),
        document_kinds=(document_kinds or "").strip() or None,
        position=(highest or 0) + 1,
        created_by=created_by,
        updated_by=created_by,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _as_dict(row)


def update(
    db: Session,
    workflow_id: str,
    division: Division,
    title: Optional[str] = None,
    folder: Optional[str] = None,
    purpose: Optional[str] = None,
    documents: Optional[list] = None,
    rubric: Optional[list] = None,
    document_kinds: Optional[str] = None,
    updated_by: Optional[str] = None,
) -> dict:
    """Rename a use case, repoint its folder, or change what it checks.

    The slug never changes, so records, approvals and revision history stay
    attached to the use case across a rename.
    """
    row = _row(db, workflow_id, division, include_archived=True)
    if row is None:
        raise ValueError(f"Unknown workflow '{workflow_id}' in {division.value}")

    if title is not None:
        cleaned = title.strip()
        if not cleaned:
            raise ValueError("A use case needs a title.")
        row.title = cleaned
    if folder is not None:
        row.folder = _validate_folder(division, folder)
    if purpose is not None:
        row.purpose = purpose.strip()
    # Re-normalised against the folder as it stands after any repoint above, so
    # a document pointed at the new folder is not recorded as an override of it.
    if documents is not None:
        row.documents = json.dumps(_clean_documents(documents, row.folder))
    if rubric is not None:
        row.rubric = json.dumps(_clean_rubric(rubric))
    if document_kinds is not None:
        row.document_kinds = document_kinds.strip() or None
    row.updated_by = updated_by

    db.commit()
    db.refresh(row)
    return _as_dict(row)


def set_archived(
    db: Session,
    workflow_id: str,
    division: Division,
    archived: bool,
    updated_by: Optional[str] = None,
) -> dict:
    """Retire a use case, or bring it back.

    Retiring rather than deleting: the runs it recorded and the approvals it
    raised are history that should outlive the use case being taken out of
    service, and a retired one can be reinstated with its record intact.
    """
    row = _row(db, workflow_id, division, include_archived=True)
    if row is None:
        raise ValueError(f"Unknown workflow '{workflow_id}' in {division.value}")
    row.archived = bool(archived)
    row.updated_by = updated_by
    db.commit()
    db.refresh(row)
    return _as_dict(row)


def seed(db: Session, shipped: dict, rubrics: Optional[dict] = None) -> int:
    """Give each shipping division the shipped set, once.

    Seeds only a division with no rows at all, retired ones included, so a use
    case someone retired stays retired instead of returning on the next restart.
    Office/Retail is not in SEEDED_DIVISIONS and so stays empty.

    The shipped set and the rubrics are passed in rather than imported: they
    belong to `workflow_repo` and the analyzer, and the dependency runs the
    other way.
    """
    rubrics = rubrics or {}
    created = 0
    for division in SEEDED_DIVISIONS:
        already = (
            db.query(WorkflowDefinition)
            .filter(WorkflowDefinition.division == division)
            .first()
        )
        if already is not None:
            continue
        for position, (workflow_id, wf) in enumerate(shipped.items()):
            rubric = rubrics.get(workflow_id, {})
            db.add(
                WorkflowDefinition(
                    workflow_id=workflow_id,
                    division=division,
                    title=wf["title"],
                    folder=wf["folder"],
                    purpose=wf["purpose"],
                    documents=json.dumps(wf["documents"]),
                    rubric=json.dumps(rubric.get("requirements", [])),
                    document_kinds=rubric.get("document_kinds") or None,
                    position=position,
                    shipped=True,
                    created_by="AAT default",
                )
            )
            created += 1
        db.commit()
    return created
