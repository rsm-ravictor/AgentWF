"""Finding the right agreement, and finding the right passage inside it.

Clause Search asks four questions. Three of them — company, property, unit —
identify *which* agreement to read. The fourth, a summary of what happened, is
what gets searched for inside it.

That second search is the reason this module exists. Looking up keywords from
the summary would find the sections that happen to reuse the summary's words,
which is not the same thing as the section the conduct breaches: a report saying
"contractors moving equipment at 23:40" shares no vocabulary with a clause about
"use of the Premises outside the hours of 7:00 a.m. to 9:00 p.m." The match has
to be made by reading, so it is made by the model (`llm_analyzer.find_clause`).

What is left here is the part that must not be guessed:

* **Which agreement.** Scored against the three lookup values, over filenames
  first and the text itself second, so the answer can be shown with its reason
  rather than asserted.
* **Where the passage sits.** The model quotes a passage; to highlight it, its
  character offsets in the original text are needed. Those are computed here by
  matching, never reported by the model — a model-supplied offset that is off by
  forty characters highlights the wrong clause convincingly.

A quote that cannot be located is reported as unlocatable rather than
approximated. A highlight over the wrong paragraph is worse than no highlight.
"""

import re
from typing import List, Optional

from sqlalchemy.orm import Session

from . import llm_analyzer, workflow_repo
from .config import Division

# Tokens too common in a lease to be evidence that this is the *right* lease.
STOPWORDS = {
    "the", "and", "for", "inc", "llc", "ltd", "co", "corp", "company",
    "suite", "unit", "no", "number", "lease", "agreement", "of", "at",
}

# How many candidate agreements are opened and read when filenames alone do not
# settle it. Reading is the accurate test and the expensive one, so it is capped.
MAX_CANDIDATES_READ = 12


def _tokens(value: str) -> List[str]:
    return [
        t
        for t in re.split(r"[^a-z0-9]+", (value or "").lower())
        if len(t) > 2 and t not in STOPWORDS
    ]


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


# ---------------- Which agreement ----------------

def select_agreement(
    db: Session,
    division: Division,
    folder: str,
    lookup: dict,
) -> dict:
    """Pick the agreement the three lookup values point at.

    Filenames are checked first because it is free; the text is read only when
    needed, and only for as many candidates as `MAX_CANDIDATES_READ` allows.

    Returns the chosen document with the reason it was chosen, every candidate
    that was weighed, and — when the winner did not clearly beat the runner-up —
    an `ambiguous` flag, so a run can hand that judgment to a person instead of
    quietly reading the wrong tenancy's lease.
    """
    values = [v for v in (lookup or {}).values() if (v or "").strip()]
    documents = workflow_repo.folder_documents(db, division, folder)

    candidates = []
    for doc in documents[:MAX_CANDIDATES_READ]:
        archived = workflow_repo.archived_file(db, doc.id)
        text = ""
        if archived is not None:
            try:
                text = llm_analyzer._document_text(
                    archived["content"], archived["media_type"], archived["filename"]
                )
            except Exception:
                text = ""  # unreadable here is reported by build_context later

        filename = (doc.filename or "").lower()
        haystack = _normalise(text)
        matched_on = []
        score = 0
        for value in values:
            phrase = _normalise(value)
            tokens = _tokens(value)
            # The whole value appearing in the text is the strongest signal a
            # lease is this tenancy's; a token in the filename is the weakest.
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
            "reason": (
                "No agreement in this folder matched the company, property and unit given."
                if candidates
                else f"No agreement is on file in {folder}."
            ),
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
        # A tie is not a match. Two leases scoring the same means the values
        # given do not distinguish them, which is a question for a person.
        "ambiguous": best["score"] == runner_up,
    }


# ---------------- Where the passage sits ----------------

def locate(haystack: str, needle: str) -> Optional[dict]:
    """Character offsets of `needle` within `haystack`, tolerant of whitespace.

    An agreement wraps its lines; a quotation of it does not wrap in the same
    places. Matching the raw strings therefore fails on passages that are
    verbatim in every way that matters, so both sides are compared with runs of
    whitespace flattened, and the result is mapped back to offsets in the
    original text.

    Returns None when the passage is not there — which is the answer that keeps
    a highlight honest.
    """
    if not haystack or not needle:
        return None

    # Flatten the haystack, remembering where each kept character came from.
    flat_chars = []
    origin = []
    previous_was_space = False
    for index, char in enumerate(haystack):
        if char.isspace():
            if previous_was_space or not flat_chars:
                continue
            flat_chars.append(" ")
            origin.append(index)
            previous_was_space = True
        else:
            flat_chars.append(char.lower())
            origin.append(index)
            previous_was_space = False

    flat = "".join(flat_chars)
    target = _normalise(needle)
    if not target:
        return None

    at = flat.find(target)
    if at < 0:
        # A quotation that has been trimmed at one end still identifies the
        # passage, so fall back to its opening run of words before giving up.
        opening = " ".join(target.split()[:12])
        at = flat.find(opening) if len(opening) > 24 else -1
        if at < 0:
            return None
        target = opening

    start = origin[at]
    end = origin[min(at + len(target) - 1, len(origin) - 1)] + 1
    return {"start": start, "end": end, "text": haystack[start:end]}


def highlights(lease_text: str, matches: list) -> dict:
    """Turn quoted passages into spans the UI can paint, plus what it could not.

    Overlapping spans are merged: two findings that quote the same section should
    read as one highlighted passage rather than as a nested pair.
    """
    spans = []
    unlocatable = []
    for match in matches or []:
        quote = getattr(match, "text", None) or (match.get("text") if isinstance(match, dict) else "")
        section = getattr(match, "section", None) or (
            match.get("section") if isinstance(match, dict) else ""
        )
        found = locate(lease_text, quote)
        if found is None:
            unlocatable.append(section or (quote or "")[:60])
            continue
        spans.append({**found, "section": section})

    spans.sort(key=lambda s: s["start"])
    merged: List[dict] = []
    for span in spans:
        if merged and span["start"] <= merged[-1]["end"]:
            merged[-1]["end"] = max(merged[-1]["end"], span["end"])
            if span["section"] and span["section"] not in merged[-1]["section"]:
                merged[-1]["section"] += f"; {span['section']}"
        else:
            merged.append(dict(span))

    return {"spans": merged, "unlocatable": unlocatable}
