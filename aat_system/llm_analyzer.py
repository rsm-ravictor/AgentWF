"""LLM-backed document analysis, and the drafting that follows it.

Sends an uploaded document to a model and gets back a structured,
machine-readable verdict (approve / needs human review / reject) plus
per-requirement findings. This is where a use case's decision is actually made.

Two things are read, not one: the attached document, and whatever the use case
already has on file (``build_context``). An intake step finds those on-file
documents; before they were carried into the prompt, nothing read them.

``draft_notice`` is the second call, for a use case that ends in correspondence
rather than in a verdict — Clause Search quotes the breached lease section into
an email. It is a separate call on purpose: see its docstring.

Two routes, chosen by ``LLM_PROVIDER``:

* ``tritonai`` (default) — UCSD's OpenAI-compatible proxy, via
  ``aat_system.connect``. One entry point for every model it offers; switching
  models is the ``TRITONAI_MODEL`` env var and nothing else.
* ``anthropic`` — the direct Anthropic SDK path this started on.

They are not interchangeable in one respect worth knowing, so it is stated here
rather than discovered mid-run: the Anthropic route enforces the response schema
through the API's structured outputs, and reads PDFs and images natively. The
TritonAI route is a text chat completion, so the schema is enforced by JSON mode
plus a schema hint and then validated locally, and a document has to be text by
the time it is sent — PDFs are extracted here, images cannot be.
"""

import base64
import io
import os
from typing import List, Literal, Optional

import anthropic
from pydantic import BaseModel, Field

from . import connect

# Which route a run's analysis step takes. TritonAI by default.
PROVIDER = os.getenv("LLM_PROVIDER", "tritonai").strip().lower()

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-opus-5")
TRITONAI_MODEL = os.getenv("TRITONAI_MODEL", connect.DEFAULT_MODEL)

# The model actually in use, for the dashboard and the run footer to report.
MODEL = TRITONAI_MODEL if PROVIDER == "tritonai" else ANTHROPIC_MODEL

# Requirements each workflow checks a document against. These are the rubric the
# model grades against — keep them concrete and checkable.
WORKFLOW_RUBRICS = {
    "vendor-insurance": {
        "title": "Vendor Insurance",
        "document_kinds": "vendor certificate of insurance (COI), AAT requirements document",
        "requirements": [
            "Certificate is currently active (today falls between the policy effective and expiration dates)",
            "General liability limit is at least $2,000,000 per occurrence",
            "AAT is named as an additional insured",
            "Workers compensation coverage is present",
            "The named insured matches the vendor on file",
        ],
    },
    "renters-insurance": {
        "title": "Renter's Insurance",
        "document_kinds": "tenant renter's insurance policy or certificate, lease agreement",
        "requirements": [
            "Policy is currently active (today falls within the coverage period)",
            "Personal liability coverage is at least $100,000",
            "The property management company is listed as an additional interest or additional insured",
            "The named insured matches the tenant on the lease",
            "The insured address matches the leased unit",
        ],
    },
    "lease-checklist": {
        "title": "Lease & File Checklist",
        "document_kinds": "lease agreement, addenda/riders, file checklist",
        "requirements": [
            "Lease is signed and dated by both tenant and landlord/agent",
            "Lease term start and end dates are present and unambiguous",
            "Monthly rent amount and due date are stated",
            "Security deposit amount is stated",
            "All referenced addenda or riders are attached",
        ],
    },
    "breach-notice": {
        "title": "Breach Notice",
        "document_kinds": "tenant lease, violation report, prior breach history",
        "requirements": [
            "The specific lease section allegedly breached is cited",
            "The factual conduct constituting the breach is described with dates",
            "The cure period or remedy required is stated",
            "Tenant name and unit are identified and match the lease",
            "The notice is dated and identifies who issued it",
        ],
    },
    "security-report": {
        "title": "Security Report",
        "document_kinds": "daily activity report, incident log",
        "requirements": [
            "Report covers a clearly stated date and time range",
            "Each flagged incident has a time, location, and description",
            "Incidents involving injury, police, or property damage are clearly marked",
            "The reporting officer or source is identified",
            "Any follow-up action required is stated",
        ],
    },
    # ---- Office/Retail ----
    #
    # The certificate and coverage rubrics are the residential vendor and renter
    # requirements retargeted at office and retail holders. The clause rubric is
    # its own thing: it grades a pairing of two documents — an incident report
    # and the lease it points at — rather than one submission.
    "insurance-certificate-audit": {
        "title": "Insurance Certificate Audit",
        "document_kinds": "certificate of insurance (COI), AAT requirements document",
        "requirements": [
            "Certificate is currently active (today falls between the policy effective and expiration dates)",
            "General liability limit is at least $2,000,000 per occurrence",
            "AAT is named as an additional insured, not merely as certificate holder",
            "Workers compensation coverage is present",
            "The named insured matches the vendor or tenant on file",
        ],
    },
    "coverage-matching": {
        "title": "Insurance Coverage Matching Workflow",
        "document_kinds": "submitted insurance policy or certificate, governing agreement, coverage matrix",
        "requirements": [
            "Policy is currently active (today falls within the coverage period)",
            "Every coverage line required by the governing agreement appears on the policy",
            "Each limit meets or exceeds the amount the agreement requires",
            "AAT or the managing entity is named as required by the agreement",
            "The named insured and insured address match the party and premises on the agreement",
        ],
    },
    "clause-search": {
        "title": "Clause Search",
        "document_kinds": "incident report or notification, and the lease it concerns",
        "requirements": [
            "The incident report identifies the tenant or party, the unit, the location, and the date the incident occurred",
            "The lease on file is for that same party, unit and location",
            "The lease is signed by both sides and its term covers the date of the incident",
            "The conduct the report describes is prohibited by a specific numbered section of the lease",
            "That section is quoted exactly as the lease words it, together with any notice or cure period it sets",
        ],
    },
}


class Finding(BaseModel):
    """One requirement, graded."""

    requirement: str = Field(description="The requirement being checked, restated briefly.")
    status: Literal["met", "not_met", "unclear"] = Field(
        description="'met' if the document clearly satisfies it, 'not_met' if it clearly does not, "
        "'unclear' if the document does not contain enough information to tell."
    )
    evidence: str = Field(
        description="Short quote or specific reference from the document supporting the status. "
        "Empty string if the document says nothing about this requirement."
    )


class ExtractedField(BaseModel):
    """A key data point pulled off the document."""

    label: str = Field(description="What the value is, e.g. 'Policy number', 'Effective date'.")
    value: str = Field(description="The value exactly as it appears in the document.")


class DocumentVerdict(BaseModel):
    """The full structured decision returned to the UI."""

    document_type: str = Field(description="What this document actually appears to be.")
    is_expected_type: bool = Field(
        description="True if the document is the kind of document this workflow expects."
    )
    summary: str = Field(description="Two or three sentences on what the document contains.")
    decision: Literal["approve", "needs_human_review", "reject"] = Field(
        description="'approve' only if every requirement is met and nothing is ambiguous. "
        "'reject' if a requirement is clearly not met. "
        "'needs_human_review' if anything is unclear, missing, or a judgment call."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Your confidence in the decision, given document quality and completeness."
    )
    reasoning: str = Field(description="Why you reached this decision, in a few sentences.")
    findings: List[Finding] = Field(description="One entry per requirement in the rubric.")
    extracted_fields: List[ExtractedField] = Field(
        description="Key data points from the document: names, dates, amounts, policy numbers."
    )
    missing_information: List[str] = Field(
        description="Anything a reviewer would need that this document does not contain."
    )


class QuotedClause(BaseModel):
    """One passage lifted out of the agreement, word for word."""

    section: str = Field(
        description="The section or clause number exactly as the agreement labels it, "
        "e.g. 'Section 12(b)' or 'Article VII, Paragraph 3'."
    )
    text: str = Field(
        description="The passage copied out of the agreement character for character. "
        "Never paraphrased, never tidied up, never trimmed mid-sentence."
    )
    breached_by: str = Field(
        description="What the incident report describes that this passage prohibits or requires."
    )


class DraftedNotice(BaseModel):
    """An email written for a person to review and send."""

    recipient: str = Field(
        description="Who this should go to, as the documents identify them — the tenant or "
        "party named on the agreement, and the unit."
    )
    subject: str = Field(description="Subject line. Factual, naming the unit and the date.")
    body: str = Field(
        description="The complete email as it would be sent, with the quoted passages already "
        "in place inside it. Plain text with line breaks, no markup, no placeholders to fill in."
    )
    quoted_clauses: List[QuotedClause] = Field(
        description="Every passage the body quotes from the agreement, listed separately so the "
        "quote can be checked against the source. Empty if the agreement supports no citation."
    )
    unresolved: List[str] = Field(
        description="Anything the documents did not support and that was therefore left out of "
        "the body: a section that could not be found, a date that did not line up, a party that "
        "did not match."
    )


class ClauseMatch(BaseModel):
    """One section of an agreement, matched to what was reported."""

    section: str = Field(
        description="The section or clause number exactly as the agreement labels it."
    )
    heading: str = Field(
        description="The section's own heading, if it has one. Empty string if it does not."
    )
    text: str = Field(
        description="The section copied out of the agreement character for character, from the "
        "start of the sentence that prohibits or requires the conduct through to the end of the "
        "obligation — including any notice or cure period it sets. Never paraphrased."
    )
    why: str = Field(
        description="Why this section covers what was described, in one or two sentences. Say "
        "what in the account meets what in the section."
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="'high' only when the section plainly covers the conduct described."
    )


class ClauseSearchResult(BaseModel):
    """What reading an agreement against an account of an incident turned up."""

    matches: List[ClauseMatch] = Field(
        description="The sections the conduct actually breaches, strongest first. Empty if the "
        "agreement does not address what was described — that is a real answer, not a failure."
    )
    also_considered: List[str] = Field(
        description="Sections that looked relevant and were ruled out, each with a few words on "
        "why not. This is what makes the search checkable."
    )
    summary: str = Field(
        description="Two or three sentences on what the agreement says about this conduct."
    )
    party: str = Field(
        description="The party the agreement binds, as the agreement names them. Empty if the "
        "text does not say."
    )
    premises: str = Field(
        description="The premises or unit the agreement covers, as the agreement names them."
    )
    term: str = Field(
        description="The term of the agreement as stated, e.g. '1 January 2026 to 31 December "
        "2028'. Empty if the text does not say."
    )


SYSTEM_PROMPT = """You review property-management documents for AAT, a real estate asset \
and lease management company. You grade a single uploaded document against a fixed rubric \
and return a structured verdict.

Ground every finding in what the document actually says. If the document does not contain \
the information a requirement asks about, mark that requirement 'unclear' and say so in \
missing_information — do not infer it, and do not treat absence as satisfaction.

Reserve 'approve' for documents where every requirement is met and nothing is ambiguous. \
Anything that turns on a judgment call, or where a date or amount is close to a threshold, \
belongs in 'needs_human_review'. A human signs off on the final decision; your job is to do \
the reading and make the call auditable, not to clear things through."""


CLAUSE_SYSTEM_PROMPT = """You read commercial agreements for AAT, a real estate asset and lease management company. You \
are given one agreement and an account of something that happened at the premises. You find \
the section or sections that the conduct described actually breaches.

This is a reading task, not a search task. The account and the agreement will usually share no \
vocabulary at all — a report of contractors moving equipment at 23:40 is covered by a clause \
about use of the premises outside permitted hours, which mentions neither contractors nor \
equipment. Match on what the section governs, not on the words it happens to use.

Quote every section you return character for character, from the text you were given. Do not \
paraphrase, do not tidy the wording, and do not stitch two passages into one quotation. Give \
the section number the agreement itself uses.

Return no match rather than a weak one. An agreement that does not address the conduct is a \
real and useful answer; a section stretched to fit is not. Put anything you weighed and ruled \
out in `also_considered`, so the reading can be checked."""


DRAFT_SYSTEM_PROMPT = """You draft correspondence for AAT, a real estate asset and lease management company. You are \
given an incident report, the agreement it concerns, and the reading of the two that has \
already been made. You write one email for a person at AAT to review and send.

Quote, never paraphrase. Any wording you present as coming from the agreement must be copied \
out of the text you were given, character for character, with the section number the agreement \
gives it. If the wording you need is not in that text, say so in `unresolved` and leave it out \
of the body rather than reconstructing it.

Assert nothing the documents do not support. Do not cite a section that is not there, do not \
state a notice or cure period the agreement does not set, and do not characterise conduct as a \
breach where the agreement does not address it. A draft that says what is missing is worth \
more than one the reviewer has to correct.

The email is a draft, not correspondence: you do not send it. Write it the way a property \
manager would — plain, dated, factual, and carrying no conclusion beyond what the quoted \
section states."""


# How much of the documents already on file is carried into a prompt. Generous
# per document, because a clause can sit anywhere in a lease, and capped in total
# so a folder of long agreements cannot quietly turn one run into a large bill.
CONTEXT_DOC_CHARS = 24000
CONTEXT_TOTAL_CHARS = 72000


def build_context(documents: Optional[List[dict]]) -> dict:
    """Turn the documents an intake step matched into a block a prompt can carry.

    Each entry is ``{"name", "filename", "media_type", "content"}`` — the shape
    ``workflow_repo.archived_file`` returns, plus the requirement name it
    satisfied.

    This exists because an intake step used to find the documents on file and
    then nothing read them: a narrative promising a certificate "graded against
    the standard on file" was only half true, and Clause Search cannot work at
    all without it — the clause is in the lease, and the lease is on file rather
    than attached.

    Anything that cannot be turned into text is reported in ``skipped`` rather
    than dropped, so a run says a scanned lease could not be read instead of
    reading as though the lease said nothing.
    """
    included: List[str] = []
    skipped: List[str] = []
    blocks: List[str] = []
    budget = CONTEXT_TOTAL_CHARS

    for spec in documents or []:
        filename = spec.get("filename") or "document"
        label = spec.get("name") or filename
        if budget <= 0:
            skipped.append(f"{label} ({filename}) — prompt budget reached")
            continue
        try:
            text = _document_text(
                spec["content"], spec.get("media_type") or "", filename
            ).strip()
        except Exception as exc:
            skipped.append(f"{label} ({filename}) — {exc}")
            continue
        if not text:
            skipped.append(f"{label} ({filename}) — no readable text")
            continue

        limit = min(CONTEXT_DOC_CHARS, budget)
        if len(text) > limit:
            text = text[:limit] + "\n[...truncated: this document is longer than the prompt allows]"
        budget -= len(text)
        included.append(f"{label} ({filename})")
        blocks.append(
            f'<document role="{label}" name="{filename}">\n{text}\n</document>'
        )

    return {
        "text": "\n\n".join(blocks),
        "included": included,
        "skipped": skipped,
    }


def _client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def _usable(value: Optional[str]) -> bool:
    """Whether a credential string is real rather than a shipped placeholder.

    The placeholders in .env.example resolve to non-empty strings, which would
    otherwise report "configured" right up until the API rejects them mid-run.
    """
    return bool(value) and "replace-with" not in value


def has_api_key() -> bool:
    """Whether the active provider has a usable credential.

    Checked so the dashboard and the run footer can say so up front rather than
    failing halfway through a run.
    """
    if PROVIDER == "tritonai":
        return _usable(os.getenv("TRITONAI_API_KEY"))

    # The Anthropic client constructor does not raise on missing credentials —
    # it fails at request time — so inspect the resolved values instead.
    try:
        client = _client()
    except Exception:
        return False
    resolved = (
        getattr(client, "api_key", None)
        or getattr(client, "auth_token", None)
        or os.getenv("ANTHROPIC_API_KEY")
        or os.getenv("ANTHROPIC_AUTH_TOKEN")
        or ""
    )
    return _usable(resolved)


def active_route() -> dict:
    """What the UI reports about where decisions are being made."""
    return {
        "provider": PROVIDER,
        "model": MODEL,
        "configured": has_api_key(),
        "key_env": "TRITONAI_API_KEY" if PROVIDER == "tritonai" else "ANTHROPIC_API_KEY",
    }


def _document_block(file_bytes: bytes, media_type: str) -> dict:
    """Build the content block for the uploaded file."""
    if media_type == "application/pdf":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.standard_b64encode(file_bytes).decode("utf-8"),
            },
        }
    if media_type in ("image/png", "image/jpeg", "image/gif", "image/webp"):
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64.standard_b64encode(file_bytes).decode("utf-8"),
            },
        }
    # Plain text, markdown, csv — inline it.
    text = file_bytes.decode("utf-8", errors="replace")
    return {"type": "text", "text": f"<document>\n{text}\n</document>"}


def _document_text(file_bytes: bytes, media_type: str, filename: str) -> str:
    """The document as text, for a route that can only take text.

    PDFs are extracted with pypdf, which is already a dependency for redaction.
    Images raise: there is no OCR on this path, and silently grading a document
    the model never actually read would be worse than refusing.
    """
    if media_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        text = "\n\n".join(p for p in pages if p)
        if not text:
            raise RuntimeError(
                f"No text could be extracted from {filename}. It is likely a scanned "
                "image inside a PDF, which this route cannot read — set "
                "LLM_PROVIDER=anthropic to grade it, or upload a text-based copy."
            )
        return text

    if media_type.startswith("image/"):
        raise RuntimeError(
            f"{filename} is an image, and the TritonAI route sends text only. Set "
            "LLM_PROVIDER=anthropic to grade images, or upload a PDF or text copy."
        )

    return file_bytes.decode("utf-8", errors="replace")


def _run_context(
    rubric: dict,
    filename: str,
    property_id: Optional[str],
    unit_id: Optional[str],
) -> str:
    """The header every prompt opens with: what is being run, on what, for whom."""
    lines = [
        f"Workflow: {rubric.get('title') or 'this use case'}",
        f"Uploaded file: {filename}",
    ]
    if property_id:
        lines.append(f"Property ID on file: {property_id}")
    if unit_id:
        lines.append(f"Unit on file: {unit_id}")
    return "\n".join(lines)


def _on_file_section(context: str) -> str:
    """The documents already in the repository, framed so they are not confused
    with the attached one.

    Said explicitly because the two are graded differently: the attachment is
    what is under review, and these are what it is reviewed against.
    """
    if not context:
        return ""
    return (
        "\n\nAlso in the repository for this use case, and part of what you are reading:"
        "\n\n" + context + "\n\n"
        "Treat those as the documents on file. Anything you quote from them must be attributed "
        "to them rather than to the attached document."
    )


def document_pages(file_bytes: bytes, media_type: str, filename: str) -> List[str]:
    """The document as a list of pages, where the format actually has pages.

    A PDF knows where its pages break, and a lease shown as paper should break
    where the lease breaks — a citation reading "page 3" has to mean the page a
    person would find it on. Anything else comes back as a single page and is
    paginated for display by whatever renders it, which is a presentation choice
    rather than a claim about the document.
    """
    if media_type == "application/pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(file_bytes))
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
        kept = [p for p in pages if p]
        if not kept:
            raise RuntimeError(
                f"No text could be extracted from {filename}. It is likely a scanned "
                "image inside a PDF."
            )
        return kept
    return [_document_text(file_bytes, media_type, filename)]


def page_offsets(pages: List[str]) -> List[int]:
    """Where each page starts, in the joined text the rest of the system uses.

    Offsets are computed against the same join `_document_text` performs, so a
    span located in the joined text can be mapped back to the page it sits on.
    """
    offsets = []
    at = 0
    for index, page in enumerate(pages):
        offsets.append(at)
        at += len(page) + (2 if index < len(pages) - 1 else 0)
    return offsets


def _instructions(
    rubric: dict,
    filename: str,
    property_id: Optional[str],
    unit_id: Optional[str],
    context: str = "",
) -> str:
    requirements = "\n".join(f"{i}. {r}" for i, r in enumerate(rubric["requirements"], 1))

    return (
        _run_context(rubric, filename, property_id, unit_id)
        + (
            f"\n\nThis workflow expects: {rubric['document_kinds']}."
            if rubric.get("document_kinds")
            else ""
        )
        + _on_file_section(context)
        + f"\n\nGrade the attached document against these requirements:\n{requirements}\n\n"
        "Return one finding per numbered requirement, in order."
    )


def _draft_instructions(
    rubric: dict,
    filename: str,
    verdict: "DocumentVerdict",
    property_id: Optional[str],
    unit_id: Optional[str],
    sender: Optional[str],
    context: str = "",
) -> str:
    """What the drafting call is told: the reading already made, then the brief.

    The verdict is passed in rather than re-derived. The reading and the draft
    are two calls on purpose — the reading has to stand on its own, and a draft
    that quietly re-decided what the reading concluded would leave a person
    reviewing an email against findings it no longer matches.
    """
    findings = "\n".join(
        f"- {f.requirement}: {f.status.replace('_', ' ')}"
        + (f" — quoted: {f.evidence}" if f.evidence else "")
        for f in verdict.findings
    )
    fields = "\n".join(f"- {f.label}: {f.value}" for f in verdict.extracted_fields)

    parts = [
        _run_context(rubric, filename, property_id, unit_id),
        "",
        "The reading already made of these documents:",
        f"Decision: {verdict.decision.replace('_', ' ')} ({verdict.confidence} confidence)",
        f"Summary: {verdict.summary}",
        findings or "- (no findings were returned)",
    ]
    if fields:
        parts += ["", "Extracted from the documents:", fields]
    if verdict.missing_information:
        parts += ["", "Reported as missing:"] + [f"- {m}" for m in verdict.missing_information]

    brief = [
        "",
        "Write the email that follows from that reading. It must, in this order:",
        "1. Open with the context supplied with this run — the party, the unit, the location "
        "and the date of the incident — taken from the documents, not invented.",
        "2. Summarise what the incident report says happened, in the report's own terms.",
        "3. Quote the section of the agreement the conduct breaches, word for word, with its "
        "number, introduced so the reader can see it is a quotation.",
        "4. State the notice or cure period only if the agreement states one, in the agreement's "
        "own words.",
        "5. Close on what happens next, without asserting a remedy the agreement does not give.",
        "",
        "Put every passage you quote in `quoted_clauses` as well as in the body, so the quote can "
        "be checked against the agreement. Put anything you could not ground in `unresolved` and "
        "leave it out of the body.",
    ]
    if sender:
        brief.append(f"Sign it from {sender}.")

    return "\n".join(parts + brief) + _on_file_section(context)


def analyze_document(
    workflow_id: str,
    file_bytes: bytes,
    filename: str,
    media_type: str,
    property_id: Optional[str] = None,
    unit_id: Optional[str] = None,
    rubric: Optional[dict] = None,
    context: str = "",
) -> DocumentVerdict:
    """Grade one document against a workflow's rubric.

    Routes through TritonAI or Anthropic depending on ``LLM_PROVIDER``, and lets
    provider errors propagate so the API layer can map them to status codes —
    neither route retries on another model, which is deliberate.

    `rubric` is passed in by the runner, which reads it from the use case being
    run. Use cases are created at runtime now, so their requirements live in the
    database and cannot be looked up here; WORKFLOW_RUBRICS remains the fallback
    for the shipped set and for callers that only have an id.

    `context` is what the use case already has on file, from ``build_context``.
    It is a string rather than a list of files because both routes carry it the
    same way — as text in the prompt — and the caller has already decided what
    was readable.
    """
    if rubric is None:
        rubric = WORKFLOW_RUBRICS.get(workflow_id)
    if not rubric or not rubric.get("requirements"):
        raise ValueError(
            f"No requirements are set on '{workflow_id}', so there is nothing to grade against."
        )

    instructions = _instructions(rubric, filename, property_id, unit_id, context)

    if PROVIDER == "tritonai":
        return _via_tritonai(
            file_bytes, media_type, filename, instructions, SYSTEM_PROMPT, DocumentVerdict
        )
    return _via_anthropic(
        file_bytes, media_type, instructions, SYSTEM_PROMPT, DocumentVerdict
    )


def draft_notice(
    workflow_id: str,
    file_bytes: bytes,
    filename: str,
    media_type: str,
    verdict: DocumentVerdict,
    rubric: Optional[dict] = None,
    property_id: Optional[str] = None,
    unit_id: Optional[str] = None,
    sender: Optional[str] = None,
    context: str = "",
) -> DraftedNotice:
    """Write the correspondence a run's reading calls for.

    A second call rather than a second field on the verdict. Grading and drafting
    want different instructions — one is told to withhold judgment where a
    document is silent, the other is told to write nothing it cannot quote — and
    a single call asked to do both tends to soften the first to serve the second.

    The documents go in again alongside the verdict, because the point of the
    draft is the verbatim quotation: the wording has to come off the source text
    rather than out of a summary of it.
    """
    if rubric is None:
        rubric = WORKFLOW_RUBRICS.get(workflow_id) or {"title": workflow_id, "requirements": []}

    instructions = _draft_instructions(
        rubric, filename, verdict, property_id, unit_id, sender, context
    )

    if PROVIDER == "tritonai":
        return _via_tritonai(
            file_bytes, media_type, filename, instructions, DRAFT_SYSTEM_PROMPT, DraftedNotice
        )
    return _via_anthropic(
        file_bytes, media_type, instructions, DRAFT_SYSTEM_PROMPT, DraftedNotice
    )


def find_clause(
    agreement_text: str,
    account: str,
    filename: str = "agreement",
    company: str = "",
    property_id: str = "",
    unit_id: str = "",
) -> ClauseSearchResult:
    """Read an agreement against an account of an incident, and return the sections it breaches.

    The intelligent half of Clause Search. Keyword lookup over the account would
    find the sections that reuse its words, which is a different question from
    which section the conduct breaches — usually a different answer too. So the
    whole agreement goes in and the model does the reading.

    Offsets are deliberately not asked for. The model quotes the passage; where
    that passage sits is computed by matching in `clause_search.locate`, because
    a model-supplied offset that is wrong by a line highlights the wrong clause
    convincingly.
    """
    if not (agreement_text or "").strip():
        raise ValueError("There is no agreement text to search.")
    if not (account or "").strip():
        raise ValueError("There is nothing to search for — the incident summary is empty.")

    context = [f"Agreement on file: {filename}"]
    if company:
        context.append(f"Company given: {company}")
    if property_id:
        context.append(f"Property given: {property_id}")
    if unit_id:
        context.append(f"Unit given: {unit_id}")

    prompt = (
        "<agreement>\n" + agreement_text + "\n</agreement>\n\n"
        + "<account>\n" + account + "\n</account>\n\n"
        + "\n".join(context)
        + "\n\nFind the section or sections of the agreement that the conduct in the "
        "account breaches. Quote each one exactly as the agreement words it, with its number. "
        "If the agreement does not address this conduct, return no matches and say so in the "
        "summary."
    )

    if PROVIDER == "tritonai":
        return connect.ask_json(
            prompt,
            schema=ClauseSearchResult,
            model=TRITONAI_MODEL,
            system=CLAUSE_SYSTEM_PROMPT,
            temperature=0,
            max_tokens=16000,
        )

    response = _client().messages.parse(
        model=ANTHROPIC_MODEL,
        max_tokens=16000,
        system=CLAUSE_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        output_format=ClauseSearchResult,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to read this agreement.")
    return response.parsed_output


def draft_from_clause(
    clause_result: ClauseSearchResult,
    account: str,
    agreement_text: str,
    company: str = "",
    property_id: str = "",
    unit_id: str = "",
    sender: str = "",
) -> DraftedNotice:
    """Write the notice from a clause search, rather than from a graded document.

    The typed route into Clause Search: someone describes what happened, the
    agreement is read against that account, and this turns the two into an email.
    The agreement text goes in again so the quotation comes off the source rather
    than out of the search result's own copy of it.
    """
    sections = "\n\n".join(
        f"{m.section} {m.heading}\nQuoted: {m.text}\nWhy it fits: {m.why} ({m.confidence} confidence)"
        for m in clause_result.matches
    )
    given = [f"{label}: {value}" for label, value in (
        ("Company", company), ("Property", property_id), ("Unit", unit_id)
    ) if value]

    prompt = (
        "<agreement>\n" + agreement_text + "\n</agreement>\n\n"
        + "<account>\n" + account + "\n</account>\n\n"
        + ("Given with the report:\n" + "\n".join(given) + "\n\n" if given else "")
        + "The reading already made of the agreement:\n"
        + (clause_result.summary or "")
        + "\n\n"
        + (sections or "No section of the agreement was found to cover this conduct.")
        + "\n\nWrite the email that follows. It must, in this order: open with the "
        "context given above; summarise the account in its own terms; quote the section breached "
        "word for word with its number, introduced so the reader can see it is a quotation; state "
        "the notice or cure period only if the agreement sets one; and close on what happens next. "
        "Put every passage you quote in `quoted_clauses` as well as in the body. Put anything the "
        "agreement did not support in `unresolved` and leave it out of the body."
        + (f"\nSign it from {sender}." if sender else "")
    )

    if PROVIDER == "tritonai":
        return connect.ask_json(
            prompt,
            schema=DraftedNotice,
            model=TRITONAI_MODEL,
            system=DRAFT_SYSTEM_PROMPT,
            temperature=0,
            max_tokens=16000,
        )

    response = _client().messages.parse(
        model=ANTHROPIC_MODEL,
        max_tokens=16000,
        system=DRAFT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        output_format=DraftedNotice,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to draft this notice.")
    return response.parsed_output


def _via_tritonai(
    file_bytes: bytes,
    media_type: str,
    filename: str,
    instructions: str,
    system: str,
    schema: type,
):
    """Send through TritonAI's proxy and validate the reply against `schema`.

    Text-only, so the document is inlined. The schema round-trips as JSON mode
    plus a schema hint and is validated by Pydantic on the way back, so a
    malformed reply raises here rather than reaching the UI half-formed.
    """
    document = _document_text(file_bytes, media_type, filename)
    prompt = f"<document name=\"{filename}\">\n{document}\n</document>\n\n{instructions}"

    return connect.ask_json(
        prompt,
        schema=schema,
        model=TRITONAI_MODEL,
        system=system,
        temperature=0,
        max_tokens=16000,
    )


def _via_anthropic(
    file_bytes: bytes, media_type: str, instructions: str, system: str, schema: type
):
    """Send through the Anthropic SDK, which reads PDFs and images natively."""
    response = _client().messages.parse(
        model=ANTHROPIC_MODEL,
        max_tokens=16000,
        system=system,
        messages=[
            {
                "role": "user",
                "content": [
                    _document_block(file_bytes, media_type),
                    {"type": "text", "text": instructions},
                ],
            }
        ],
        output_format=schema,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to read these documents.")

    return response.parsed_output
