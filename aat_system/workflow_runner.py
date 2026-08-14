"""Executes a use case, one step at a time, reporting progress as it goes.

The runner walks the workflow's saved definition — the same rows the diagram and
the narrative render from — so what the status bar reports is what the narrative
says will happen. Add a step in the narrative and the run picks it up; there is
no second list of steps hiding in the code.

Each step's `kind` decides what actually happens:

    intake    check the required documents against the repository
    analysis  grade an attached document against the rubric (real model call),
              or report what is on file when nothing was attached
    decision  apply the pass rule to everything gathered so far
    human     queue an approval case when the run could not clear on its own
    record    write the run to the workflow's record file
    note      descriptive only — reported, but takes no action

Steps are generators of events rather than a single return value so the UI can
show progress while the model call is in flight, which is the slow part.
"""

from typing import Iterator, Optional

import anthropic
from sqlalchemy.orm import Session

from . import approval_repo, llm_analyzer, workflow_repo
from .config import Division


class Attachment:
    """A file supplied with the run, to be graded as part of the analysis step."""

    def __init__(self, content: bytes, filename: str, media_type: str):
        self.content = content
        self.filename = filename or "document"
        self.media_type = media_type or "application/octet-stream"


def _friendly(exc: Exception) -> str:
    if isinstance(exc, anthropic.AuthenticationError):
        return "Anthropic rejected the API key, so the document was not graded."
    if isinstance(exc, anthropic.RateLimitError):
        return "Rate limited by Anthropic, so the document was not graded."
    if isinstance(exc, anthropic.APIConnectionError):
        return "Could not reach the Anthropic API, so the document was not graded."
    if isinstance(exc, anthropic.APIStatusError):
        return f"Anthropic API error: {exc.message}"
    return str(exc) or exc.__class__.__name__


def run(
    db: Session,
    workflow_id: str,
    division: Division,
    property_id: str = "",
    unit: str = "",
    actor: str = "",
    attachment: Optional[Attachment] = None,
) -> Iterator[dict]:
    """Run the workflow, yielding one event per state change.

    Raises ValueError for an unknown workflow. Everything after that is reported
    as an event rather than raised, so a failure mid-run still produces an
    outcome and a record row instead of vanishing.
    """
    definition = workflow_repo.get_definition(db, workflow_id, division)
    wf = workflow_repo.WORKFLOW_CATALOG[workflow_id]
    steps = definition["steps"]

    yield {
        "type": "run",
        "workflow": workflow_id,
        "title": definition["title"],
        "steps": [{"key": s["key"], "title": s["title"], "kind": s["kind"]} for s in steps],
    }

    checked: Optional[dict] = None
    verdict = None
    analysis_failed = ""
    blockers: list = []
    clean = True

    for step in steps:
        yield {"type": "step", "key": step["key"], "status": "running", "title": step["title"]}

        kind = step["kind"]
        facts: list = []
        status = "done"
        detail = step["summary"] or ""

        if kind == "intake":
            checked = workflow_repo.required_documents(db, workflow_id, division)
            for item in checked["items"]:
                if item["present"]:
                    facts.append(f"{item['name']} — {item['matched_document']['filename']}")
                else:
                    facts.append(f"{item['name']} — not on file in {item['folder']}")
            detail = f"{checked['present']} of {checked['total']} required documents on file."
            if checked["missing"]:
                status = "blocked"
                blockers.extend(checked["missing"])
                clean = False

        elif kind == "analysis":
            if attachment is not None:
                try:
                    verdict = llm_analyzer.analyze_document(
                        workflow_id=workflow_id,
                        file_bytes=attachment.content,
                        filename=attachment.filename,
                        media_type=attachment.media_type,
                        property_id=property_id,
                        unit_id=unit,
                    )
                except Exception as exc:  # reported, not fatal — the run still ends properly
                    analysis_failed = _friendly(exc)
                    status = "blocked"
                    detail = analysis_failed
                    clean = False
                else:
                    met = sum(1 for f in verdict.findings if f.status == "met")
                    detail = (
                        f"{attachment.filename} graded as {verdict.document_type}: "
                        f"{met} of {len(verdict.findings)} requirements met."
                    )
                    facts = [f"{f.requirement} — {f.status.replace('_', ' ')}" for f in verdict.findings]
                    if verdict.missing_information:
                        facts.extend(f"Missing: {m}" for m in verdict.missing_information)
            elif checked and checked["present"]:
                detail = f"No file attached — read the {checked['present']} document(s) already on file."
                facts = [
                    f"{i['name']} — {i['matched_document']['filename']}"
                    for i in checked["items"]
                    if i["present"]
                ]
            else:
                status = "blocked"
                detail = "Nothing to read: no file attached and none on file for this use case."
                clean = False

        elif kind == "decision":
            if verdict is not None:
                clean = clean and verdict.decision == "approve"
                detail = (
                    f"Model decision: {verdict.decision.replace('_', ' ')} "
                    f"({verdict.confidence} confidence)."
                )
                facts = [verdict.reasoning] if verdict.reasoning else []
                if verdict.decision != "approve":
                    blockers.extend(
                        f.requirement for f in verdict.findings if f.status != "met"
                    )
            elif blockers:
                detail = "Cannot pass: " + "; ".join(dict.fromkeys(blockers))
            else:
                detail = "Everything this step checks is present, but nothing was graded this run."
                clean = False
                blockers.append("No graded document — attach one to reach a decision")
            status = "done" if clean else "blocked"

        elif kind == "human":
            if clean:
                detail = "Nothing queued — this run cleared every check. A person still signs off before filing."
            else:
                subject = attachment.filename if attachment else (unit or property_id or wf["title"])
                created = approval_repo.create(
                    db,
                    workflow_id=workflow_id,
                    division=division,
                    subject=f"{subject} — {wf['title']}",
                    reason=(verdict.summary if verdict else "") or detail,
                    property_id=property_id,
                    unit=unit,
                    found=[i["name"] for i in (checked["items"] if checked else []) if i["present"]],
                    missing=list(dict.fromkeys(blockers)),
                    source="run",
                    dedupe=True,  # one open case per unit, not one per run
                )
                if created:
                    detail = f"Queued as {approval_repo.to_dict(created)['reference']} for human review."
                    facts = list(dict.fromkeys(blockers))
                else:
                    detail = "Already queued — an open case exists for this property and unit."

        elif kind == "record":
            outcome = "cleared" if clean else "queued_for_review"
            record = workflow_repo.log_record(
                db,
                workflow_id=workflow_id,
                division=division,
                outcome=outcome,
                property_id=property_id,
                unit=unit,
                subject=(attachment.filename if attachment else "") or wf["title"],
                decision_note=detail if not clean else "Cleared every check.",
                document_name=attachment.filename if attachment else "",
                recorded_by=actor,
            )
            detail = f"Logged to {workflow_id}-records.csv as row #{record.id} ({outcome.replace('_', ' ')})."

        else:  # note, or a step someone added without an automated action
            facts = step["bullets"]
            detail = step["summary"] or "Noted — this step has no automated action."

        yield {
            "type": "step",
            "key": step["key"],
            "status": status,
            "title": step["title"],
            "detail": detail,
            "facts": facts,
        }

    yield {
        "type": "outcome",
        "workflow": workflow_id,
        "status": "cleared" if clean else "needs_review",
        "headline": (
            f"{definition['title']} cleared every check."
            if clean
            else f"{definition['title']} needs a human before it can be closed."
        ),
        "blockers": list(dict.fromkeys(blockers)),
        "documents": checked,
        "analysis_error": analysis_failed,
        "verdict": verdict.model_dump() if verdict is not None else None,
    }
