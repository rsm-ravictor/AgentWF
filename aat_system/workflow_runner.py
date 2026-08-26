"""Executes a use case, one step at a time, reporting progress as it goes.

The runner walks the workflow's saved definition — the same rows the diagram and
the narrative render from — so what the status bar reports is what the narrative
says will happen. Add a step in the narrative and the run picks it up; there is
no second list of steps hiding in the code.

Each step's `kind` decides what actually happens:

    intake    check the required documents against the repository, and read the
              ones that are there so later steps have them
    analysis  grade an attached document against the rubric (real model call),
              or report what is on file when nothing was attached
    draft     write the correspondence the reading calls for, quoting the
              documents verbatim (a second model call)
    decision  apply the pass rule to everything gathered so far
    human     queue an approval case when the run could not clear on its own
    record    write the run to the workflow's record file
    note      descriptive only — reported, but takes no action

The intake step does not only check presence: it reads the documents it matched,
so the analysis and draft steps can quote out of them. Clause Search depends on
that — the clause it quotes is in the lease on file, not in the attachment — and
every other use case gains the standard it grades against, which its narrative
already claimed it read.

Steps are generators of events rather than a single return value so the UI can
show progress while the model call is in flight, which is the slow part.
"""

from typing import Iterator, Optional

import anthropic
from sqlalchemy.orm import Session

from . import approval_repo, clause_search, llm_analyzer, workflow_catalog, workflow_repo
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


def _rubric(wf: dict) -> dict:
    """What a use case grades against, in the shape the analyzer takes."""
    return {
        "title": wf["title"],
        "document_kinds": wf["document_kinds"],
        "requirements": wf["rubric"],
    }


def _read_on_file(db: Session, checked: dict) -> dict:
    """Open the documents an intake step matched, so later steps can quote them.

    Only the ones that matched: a document the repository does not have is
    already reported as missing, and one whose file has gone from the archive is
    reported by `build_context` as unreadable rather than passed over.
    """
    files = []
    for item in checked["items"]:
        match = item.get("matched_document")
        if not match:
            continue
        archived = workflow_repo.archived_file(db, match["id"])
        if archived is None:
            continue
        files.append({**archived, "name": item["name"]})
    return llm_analyzer.build_context(files)


def run(
    db: Session,
    workflow_id: str,
    division: Division,
    property_id: str = "",
    unit: str = "",
    actor: str = "",
    attachment: Optional[Attachment] = None,
    inputs: Optional[dict] = None,
) -> Iterator[dict]:
    """Run the workflow, yielding one event per state change.

    Raises ValueError for an unknown workflow. Everything after that is reported
    as an event rather than raised, so a failure mid-run still produces an
    outcome and a record row instead of vanishing.

    `inputs` are the answers to the questions this use case declares in
    `run_inputs`. Two roles change what a run does rather than only what it
    reports: `lease_lookup` values pick which agreement to read, and a
    `clause_query` value is the text the agreement is searched against. A use
    case declaring neither runs exactly as it always has.
    """
    definition = workflow_repo.get_definition(db, workflow_id, division)
    # The use case as it is configured in this division — its title names the
    # record, and its requirements are what the analysis step grades against.
    wf = workflow_catalog.entry(db, workflow_id, division, include_archived=True)
    steps = definition["steps"]

    yield {
        "type": "run",
        "workflow": workflow_id,
        "title": definition["title"],
        "steps": [{"key": s["key"], "title": s["title"], "kind": s["kind"]} for s in steps],
    }

    # The typed answers, split by what each one is for. property_id and unit
    # stay as their own arguments because every run has always had them; a use
    # case that declares them as inputs supplies them the same way.
    answers = dict(inputs or {})
    declared = wf.get("run_inputs") or []
    lookup = {
        spec["label"]: (answers.get(spec["name"]) or "").strip()
        for spec in declared
        if spec.get("role") == "lease_lookup" and (answers.get(spec["name"]) or "").strip()
    }
    clause_query = next(
        (
            (answers.get(spec["name"]) or "").strip()
            for spec in declared
            if spec.get("role") == "clause_query"
        ),
        "",
    )
    property_id = property_id or (answers.get("property_id") or "").strip()
    unit = unit or (answers.get("unit") or "").strip()

    checked: Optional[dict] = None
    agreement = None
    clause_result = None
    clause_failed = ""
    highlight = {"spans": [], "unlocatable": []}
    context = {"text": "", "included": [], "skipped": []}
    verdict = None
    draft = None
    analysis_failed = ""
    draft_failed = ""
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
            # A document handed over with the run counts as satisfied. Reporting
            # it as "not on file" while the run went on to read it says the
            # opposite of what happened.
            supplied_here = (
                {
                    spec["name"]
                    for spec in wf["documents"]
                    if spec.get("folder", wf["folder"]) == wf["folder"]
                }
                if (clause_query and attachment is not None)
                else set()
            )
            for item in checked["items"]:
                if item["present"]:
                    facts.append(f"{item['name']} — {item['matched_document']['filename']}")
                elif item["name"] in supplied_here:
                    facts.append(f"{item['name']} — supplied with this run")
                else:
                    facts.append(f"{item['name']} — not on file in {item['folder']}")
            detail = f"{checked['present']} of {checked['total']} required documents on file."

            # Read them, not just count them. A later step that has to quote out
            # of a document needs the file, and an intake step that reported a
            # lease as present while nothing opened it is how a narrative and a
            # run drift apart.
            context = _read_on_file(db, checked)
            if context["included"]:
                facts.append("Read into this run: " + ", ".join(context["included"]))
            for note in context["skipped"]:
                facts.append(f"Could not read: {note}")

            # Lookup values narrow the folder to one agreement. Without them the
            # intake step takes the first file whose name matches a keyword,
            # which is fine for a folder holding one lease and wrong for a
            # folder holding forty.
            # Upload-manually: when a run both asks a clause query and carries a
            # file, the file *is* the agreement to read. Grading it against a
            # rubric would be answering a question nobody asked.
            if clause_query and attachment is not None:
                try:
                    pages = llm_analyzer.document_pages(
                        attachment.content, attachment.media_type, attachment.filename
                    )
                except Exception as exc:
                    status = "blocked"
                    clean = False
                    detail = _friendly(exc)
                    blockers.append(f"{attachment.filename} could not be read")
                else:
                    agreement = {
                        "document": {"id": None, "filename": attachment.filename},
                        "text": "\n\n".join(pages),
                        "pages": pages,
                        "reason": "Uploaded with this run.",
                        "considered": [],
                        "ambiguous": False,
                    }
                    detail = f"Reading {attachment.filename}, uploaded with this run."
                    facts.append(f"Agreement: {attachment.filename} (uploaded)")

            elif lookup:
                agreement = clause_search.select_agreement(db, division, wf["folder"], lookup)
                if agreement["document"] is None:
                    status = "blocked"
                    clean = False
                    blockers.append(agreement["reason"])
                    facts.append(agreement["reason"])
                else:
                    detail = f"Reading {agreement['document']['filename']}."
                    facts.append(f"Agreement: {agreement['document']['filename']}")
                    facts.append(agreement["reason"])
                    if agreement["ambiguous"]:
                        status = "blocked"
                        clean = False
                        blockers.append(
                            "More than one agreement matched equally well — the company, "
                            "property and unit given do not identify one"
                        )
                if len(agreement["considered"]) > 1:
                    facts.append(
                        f"{len(agreement['considered'])} agreements in {wf['folder']} were weighed."
                    )

            # A lease handed over with the run satisfies the requirement for a
            # lease on file. Without this, uploading one still failed intake with
            # "Tenant lease — not on file", which is the opposite of what the
            # upload is for.
            missing = [m for m in checked["missing"] if m not in supplied_here]

            if missing:
                status = "blocked"
                blockers.extend(missing)
                clean = False

        elif kind == "analysis":
            # A typed account plus an agreement is its own kind of reading: there
            # is no submitted document to grade, so the rubric path does not
            # apply. The question is which section the conduct breaches.
            if clause_query and agreement and agreement.get("text"):
                try:
                    clause_result = llm_analyzer.find_clause(
                        agreement_text=agreement["text"],
                        account=clause_query,
                        filename=agreement["document"]["filename"],
                        company=lookup.get("Name of company", ""),
                        property_id=property_id,
                        unit_id=unit,
                    )
                except Exception as exc:
                    clause_failed = _friendly(exc)
                    status = "blocked"
                    detail = clause_failed
                    clean = False
                else:
                    highlight = clause_search.highlights(
                        agreement["text"], clause_result.matches
                    )
                    found = len(clause_result.matches)
                    detail = (
                        f"{found} section{'' if found == 1 else 's'} of "
                        f"{agreement['document']['filename']} cover what was described."
                        if found
                        else "No section of the agreement covers what was described."
                    )
                    facts = [
                        f"{m.section} {m.heading} — {m.why} ({m.confidence} confidence)".strip()
                        for m in clause_result.matches
                    ]
                    facts.extend(
                        f"Ruled out: {note}" for note in clause_result.also_considered
                    )
                    for missing in highlight["unlocatable"]:
                        facts.append(
                            f"Could not locate in the agreement text, so it is not highlighted: {missing}"
                        )
                    if not found:
                        status = "blocked"
                        clean = False
                        blockers.append(
                            "The agreement does not address the conduct described"
                        )
                    elif any(m.confidence == "low" for m in clause_result.matches):
                        status = "blocked"
                        clean = False
                        blockers.append("A section was matched with low confidence")

            elif clause_query and not (agreement and agreement.get("text")):
                status = "blocked"
                detail = "There is no agreement text to search — the lease was not found or could not be read."
                clean = False

            elif attachment is not None and not clause_query:
                try:
                    verdict = llm_analyzer.analyze_document(
                        workflow_id=workflow_id,
                        file_bytes=attachment.content,
                        filename=attachment.filename,
                        media_type=attachment.media_type,
                        property_id=property_id,
                        unit_id=unit,
                        rubric=_rubric(wf),
                        context=context["text"],
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

        elif kind == "draft":
            if clause_result is not None:
                try:
                    draft = llm_analyzer.draft_from_clause(
                        clause_result=clause_result,
                        account=clause_query,
                        agreement_text=agreement["text"],
                        company=lookup.get("Name of company", ""),
                        property_id=property_id,
                        unit_id=unit,
                        sender=actor,
                    )
                except Exception as exc:
                    draft_failed = _friendly(exc)
                    status = "blocked"
                    detail = draft_failed
                    clean = False
                else:
                    quoted = len(draft.quoted_clauses)
                    detail = (
                        f"Drafted “{draft.subject}”, quoting {quoted} passage"
                        f"{'' if quoted == 1 else 's'} verbatim."
                        if quoted
                        else f"Drafted “{draft.subject}”, but nothing in the agreement supported a quotation."
                    )
                    facts = [f"{c.section}: “{c.text}”" for c in draft.quoted_clauses]
                    facts.extend(f"Unresolved: {u}" for u in draft.unresolved)
                    if not quoted or draft.unresolved:
                        status = "blocked"
                        clean = False
                        blockers.extend(
                            draft.unresolved
                            or ["Nothing could be quoted from the agreement on file"]
                        )

            elif verdict is None:
                status = "blocked"
                detail = (
                    "Nothing to draft from: this step writes out of a reading, and no document "
                    "was graded this run."
                )
                clean = False
                blockers.append("No graded document — attach the report this notice is about")
            else:
                try:
                    draft = llm_analyzer.draft_notice(
                        workflow_id=workflow_id,
                        file_bytes=attachment.content,
                        filename=attachment.filename,
                        media_type=attachment.media_type,
                        verdict=verdict,
                        rubric=_rubric(wf),
                        property_id=property_id,
                        unit_id=unit,
                        sender=actor,
                        context=context["text"],
                    )
                except Exception as exc:  # reported, not fatal, as with the analysis
                    draft_failed = _friendly(exc)
                    status = "blocked"
                    detail = draft_failed
                    clean = False
                else:
                    quoted = len(draft.quoted_clauses)
                    detail = (
                        f"Drafted “{draft.subject}”, quoting {quoted} passage"
                        f"{'' if quoted == 1 else 's'} verbatim."
                        if quoted
                        else f"Drafted “{draft.subject}”, but nothing on file supported a quotation."
                    )
                    facts = [f"{c.section}: “{c.text}”" for c in draft.quoted_clauses]
                    facts.extend(f"Unresolved: {u}" for u in draft.unresolved)
                    # A draft with nothing quoted, or with a hole in it, is the
                    # thing this use case exists to avoid sending. It goes to a
                    # person rather than out.
                    if not quoted or draft.unresolved:
                        status = "blocked"
                        clean = False
                        blockers.extend(
                            draft.unresolved
                            or ["Nothing could be quoted from the documents on file"]
                        )

        elif kind == "decision":
            if clause_result is not None:
                matched = len(clause_result.matches)
                detail = (
                    f"{matched} section{'' if matched == 1 else 's'} matched; "
                    f"{len(highlight['spans'])} highlighted in the agreement."
                    if matched
                    else "Nothing matched, so there is no breach to notify."
                )
                facts = [clause_result.summary] if clause_result.summary else []
                status = "done" if clean else "blocked"

            elif verdict is not None:
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
                    # A drafted notice is what the reviewer is being asked about,
                    # so the case says so rather than making them open the run.
                    reason=(
                        f"Draft ready to review: “{draft.subject}”. "
                        if draft is not None
                        else ""
                    )
                    + ((verdict.summary if verdict else "") or detail),
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
        "draft_error": draft_failed,
        "verdict": verdict.model_dump() if verdict is not None else None,
        "draft": draft.model_dump() if draft is not None else None,
        "clause_error": clause_failed,
        # The agreement and where the matched passages sit in it, so the page can
        # show the source with the section marked rather than only the quotation.
        "agreement": (
            {
                "filename": agreement["document"]["filename"],
                "reason": agreement["reason"],
                "text": agreement["text"],
                "considered": agreement["considered"],
                "spans": highlight["spans"],
                "unlocatable": highlight["unlocatable"],
                # Where the document's own pages begin, when it has any. The page
                # a clause sits on should be the page a person would find it on.
                "page_offsets": llm_analyzer.page_offsets(agreement.get("pages") or []),
            }
            if agreement and agreement.get("document")
            else None
        ),
        "clause_search": clause_result.model_dump() if clause_result is not None else None,
        "read_on_file": context["included"],
        "unreadable_on_file": context["skipped"],
    }
