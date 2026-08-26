"""Turning a coverage check into marks on the two documents it was made from.

Insurance Coverage Matching reads two instruments and has to show its work on
both: the lease passage that imposed each requirement, and the certificate
passage that answered it. This module is the part of that which must not be
guessed.

What lives here is the same discipline as `clause_search`: the model quotes a
passage, and *where that passage sits* is computed here by matching the quote
against the source text. A model-supplied offset that is off by a line marks the
wrong clause convincingly, and on this surface a wrong mark is worse than usual —
a green highlight on the wrong row of a certificate is a claim that coverage
exists.

Two things follow from that:

* **A quote that cannot be found is reported, not approximated.** It appears in
  `unlocatable` and the checklist line still carries its verdict; it simply is
  not highlighted. The verdict came from the reading, not from the highlight.
* **A missing coverage has no quote at all, and that is correct.** There is
  nothing in the certificate to mark, so nothing is marked. The red on the
  checklist is the finding; the absence of a mark on the document *is* the
  evidence.

Spans carry the status they were found for, so the certificate paints green,
amber and red rather than one undifferentiated highlight. The lease's own spans
are neutral: a requirement is not a pass or a fail, it is the thing being asked.
"""

from typing import List, Optional

from sqlalchemy.orm import Session

from . import clause_search, llm_analyzer, workflow_repo
from .config import Division

# The order the checklist is walked in, and the order the certificate is swept
# in. Failures first: someone watching a run should see what is wrong before
# they see what is right, and someone reading the finished screen should not have
# to scroll past six greens to find the red.
STATUS_ORDER = {"missing": 0, "short": 1, "unclear": 2, "met": 3}

# What each verdict is called on screen, and the colour it paints. Kept here
# rather than in the template so the words on the checklist, the words in the
# record row and the words in the email cannot drift apart.
STATUS_LABELS = {
    "met": "Compliant",
    "short": "Below requirement",
    "missing": "Missing",
    "unclear": "Not confirmed",
}

STATUS_TONES = {
    "met": "green",
    "short": "yellow",
    "missing": "red",
    "unclear": "grey",
}


def locate_requirements(lease_text: str, requirements: list) -> dict:
    """Find each requirement's lease wording in the lease, for the first sweep.

    Neutral spans: this pass is showing where the obligations come from, before
    anything has been checked against them. Painting them green or red here would
    be answering a question that has not been asked yet.
    """
    spans = []
    unlocatable = []
    for index, req in enumerate(requirements or []):
        quote = _attr(req, "quote")
        label = _attr(req, "label")
        section = _attr(req, "section")
        found = clause_search.locate(lease_text, quote)
        if found is None:
            unlocatable.append(label or section or (quote or "")[:60])
            continue
        spans.append(
            {
                **found,
                "label": label,
                "section": section,
                "status": "requirement",
                "tone": "neutral",
                "requirement": index,
            }
        )
    return {"spans": _merge(spans), "unlocatable": unlocatable}


def locate_checks(policy_text: str, checks: list) -> dict:
    """Find each answer's evidence in the certificate, coloured by its verdict.

    A check with no evidence is skipped in silence rather than reported as
    unlocatable: a `missing` line is *supposed* to have nothing to quote, so
    listing it as a failed lookup would turn the correct answer into a warning.
    Only a quote that was given and could not be found is worth reporting.
    """
    spans = []
    unlocatable = []
    for index, check in enumerate(checks or []):
        quote = _attr(check, "evidence")
        label = _attr(check, "label")
        status = _attr(check, "status") or "unclear"
        if not (quote or "").strip():
            continue
        found = clause_search.locate(policy_text, quote)
        if found is None:
            unlocatable.append(label or (quote or "")[:60])
            continue
        spans.append(
            {
                **found,
                "label": label,
                "section": "",
                "status": status,
                "tone": STATUS_TONES.get(status, "grey"),
                "requirement": index,
            }
        )
    return {"spans": _merge(spans), "unlocatable": unlocatable}


def _merge(spans: List[dict]) -> List[dict]:
    """Order the spans, and drop one fully inside another.

    Overlaps are not merged the way `clause_search.highlights` merges them.
    There, two findings quoting one section should read as one highlighted
    passage; here, two checklist lines quoting the same row of a certificate can
    have *different verdicts* — a per-occurrence limit that passes and an
    aggregate that does not, both on the same line of the coverages table. Fusing
    them would paint one colour over two answers.

    So the spans stay separate and the later one wins where they genuinely
    collide, which keeps each mark tied to the line that produced it.
    """
    ordered = sorted(spans, key=lambda s: (s["start"], -(s["end"] - s["start"])))
    kept: List[dict] = []
    for span in ordered:
        if kept and span["start"] >= kept[-1]["start"] and span["end"] <= kept[-1]["end"]:
            continue  # wholly contained in a span already kept
        if kept and span["start"] < kept[-1]["end"]:
            # A partial overlap would render as nested markup. Trim rather than
            # drop: the tail of the passage still shows where the answer sits.
            span = {**span, "start": kept[-1]["end"]}
            if span["start"] >= span["end"]:
                continue
        kept.append(span)
    return kept


def checklist(requirements: list, checks: Optional[list] = None) -> List[dict]:
    """The checklist as the page renders it: one row per requirement, answered or not.

    Joined on the label, because that is what the match was asked to echo back.
    A requirement the reply skipped is kept as `unclear` rather than dropped — a
    line that vanishes from the checklist reads as a line that passed, and this
    is the one use case where a silently missing row is the whole failure mode.
    """
    answers = {}
    for index, check in enumerate(checks or []):
        key = (_attr(check, "label") or "").strip().lower()
        answers.setdefault(key, (index, check))

    rows = []
    for index, req in enumerate(requirements or []):
        label = _attr(req, "label")
        match = answers.get((label or "").strip().lower())
        check = match[1] if match else None
        status = (_attr(check, "status") if check else "") or "unclear"
        rows.append(
            {
                "index": index,
                "label": label,
                "category": _attr(req, "category"),
                "section": _attr(req, "section"),
                "required": _attr(req, "required_limit"),
                "required_amount": _attr(req, "required_amount", None),
                "quote": _attr(req, "quote"),
                "mandatory": bool(_attr(req, "mandatory", True)),
                # Everything below is empty until the match step has run, which
                # is what lets the page draw the checklist first and resolve it
                # afterwards, as the mockup does.
                "answered": check is not None,
                "status": status if check is not None else "pending",
                "status_label": STATUS_LABELS.get(status, "Not confirmed")
                if check is not None
                else "Pending",
                "tone": STATUS_TONES.get(status, "grey") if check is not None else "pending",
                "found": _attr(check, "found_limit") if check else "",
                "found_amount": _attr(check, "found_amount", None) if check else None,
                "evidence": _attr(check, "evidence") if check else "",
                "note": _attr(check, "note") if check else "",
                "check": match[0] if match else None,
            }
        )
    return rows


def tally(rows: List[dict]) -> dict:
    """How many lines landed in each verdict, for the summary pills.

    `compliant` is counted rather than derived from a pass/fail, because the
    interesting number on this screen is not whether it passed — it is how much
    of the certificate is already right.
    """
    counts = {"met": 0, "short": 0, "missing": 0, "unclear": 0, "pending": 0}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {
        "total": len(rows),
        "compliant": counts["met"],
        "short": counts["short"],
        "missing": counts["missing"],
        "unclear": counts["unclear"],
        "pending": counts["pending"],
        # What has to be fixed before this tenancy is compliant. `unclear` counts
        # as outstanding: a line nobody could confirm is not a line that passed.
        "outstanding": counts["short"] + counts["missing"] + counts["unclear"],
    }


def select_policy(
    db: Session,
    division: Division,
    folder: str,
    lookup: dict,
    exclude_document_id: Optional[int] = None,
) -> dict:
    """Pick the certificate the lookup values point at, out of the insurance folder.

    The same scoring `clause_search.select_agreement` uses, over a different
    folder, with one addition: the lease already chosen is excluded. Both
    documents can legitimately name the same tenant, the same property and the
    same unit, so without that exclusion a run whose lease and certificate sit in
    one folder would happily check the lease against itself and report that every
    requirement was met.
    """
    result = clause_search.select_agreement(db, division, folder, lookup)
    chosen = result.get("document")
    if chosen is None or exclude_document_id is None or chosen["id"] != exclude_document_id:
        return result

    # The best match was the lease itself. Re-score with it held out rather than
    # failing: the certificate is usually the runner-up.
    return _reselect(db, division, folder, lookup, exclude_document_id)


def _reselect(
    db: Session, division: Division, folder: str, lookup: dict, exclude_document_id: int
) -> dict:
    """Score the folder again with one document held out.

    A thin re-run rather than a parameter on `select_agreement`: the exclusion is
    specific to there being two instruments in play, and Clause Search has no
    business knowing about it.
    """
    values = [v for v in (lookup or {}).values() if (v or "").strip()]
    documents = [
        d
        for d in workflow_repo.folder_documents(db, division, folder)
        if d.id != exclude_document_id
    ]

    candidates = []
    for doc in documents[: clause_search.MAX_CANDIDATES_READ]:
        archived = workflow_repo.archived_file(db, doc.id)
        text = ""
        if archived is not None:
            try:
                text = llm_analyzer._document_text(
                    archived["content"], archived["media_type"], archived["filename"]
                )
            except Exception:
                text = ""
        filename = (doc.filename or "").lower()
        haystack = clause_search._normalise(text)
        matched_on = []
        score = 0
        for value in values:
            phrase = clause_search._normalise(value)
            tokens = clause_search._tokens(value)
            if phrase and phrase in haystack:
                score += 3
                matched_on.append(f"{value} (in the text)")
            elif tokens and all(t in haystack for t in tokens):
                score += 2
                matched_on.append(f"{value} (words in the text)")
            elif tokens and any(t in filename for t in tokens):
                score += 1
                matched_on.append(f"{value} (in the filename)")
        candidates.append(
            {
                "id": doc.id,
                "filename": doc.filename,
                "score": score,
                "matched_on": matched_on,
                "text": text,
            }
        )

    candidates.sort(key=lambda c: (-c["score"], c["filename"] or ""))
    considered = [
        {"filename": c["filename"], "score": c["score"], "matched_on": c["matched_on"]}
        for c in candidates
    ]
    if not candidates or candidates[0]["score"] == 0:
        return {
            "document": None,
            "text": "",
            "reason": f"No certificate in {folder} matched the values given.",
            "considered": considered,
            "ambiguous": False,
        }

    best = candidates[0]
    runner_up = candidates[1]["score"] if len(candidates) > 1 else 0
    return {
        "document": {"id": best["id"], "filename": best["filename"]},
        "text": best["text"],
        "reason": "Matched on " + "; ".join(best["matched_on"]),
        "considered": considered,
        "ambiguous": best["score"] == runner_up,
    }


def _attr(obj, name: str, default=""):
    """Read a field off a pydantic model or a plain dict, whichever arrived.

    A field that is present but null reads as the default, so an optional figure
    the model left out arrives as the caller's chosen empty value rather than as
    a None that later has to be guarded at every use.
    """
    if obj is None:
        return default
    value = obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)
    return default if value is None else value
