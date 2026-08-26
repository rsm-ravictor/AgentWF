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


class CoverageRequirement(BaseModel):
    """One insurance obligation the lease imposes, as a line that can be checked.

    The unit of work for Insurance Coverage Matching. A lease states its
    insurance duties as prose — one sentence can carry a limit, an aggregate and
    three additional insureds — and prose cannot be ticked off. This splits that
    prose into lines that each have exactly one answer.

    `required_amount` is the same figure as `required_limit` with the money
    parsed out, and it is what decides amber from red: a policy that carries the
    coverage but not enough of it is a different problem from one that does not
    carry it at all, and only a number can tell those apart.
    """

    label: str = Field(
        description="What must be carried, as a short noun phrase a person can tick off, "
        "e.g. 'Commercial general liability — per occurrence' or "
        "'Landlord named as additional insured'. One obligation per entry."
    )
    category: Literal[
        "liability", "property", "workers_comp", "additional_insured", "endorsement", "administrative"
    ] = Field(
        description="Which kind of obligation this is. 'additional_insured' for a party that must "
        "be named, 'endorsement' for waiver of subrogation, primary/non-contributory and the like, "
        "'administrative' for a duty about the certificate itself rather than about coverage."
    )
    required_limit: str = Field(
        default="",
        description="The limit the lease requires, worded as the lease words it, e.g. "
        "'$2,000,000 per occurrence'. Empty string where the lease requires the coverage but "
        "sets no figure — workers' compensation at statutory limits, for instance.",
    )
    required_amount: Optional[float] = Field(
        default=None,
        description="The figure in `required_limit` as a plain number of dollars, e.g. 2000000. "
        "Null where the lease sets no figure. This is what a shortfall is measured against, so "
        "give the number only when the lease genuinely states one.",
    )
    section: str = Field(
        description="The section number the obligation comes from, exactly as the lease labels "
        "it, e.g. 'Section 12(a)'."
    )
    quote: str = Field(
        description="The words of the lease that impose this obligation, copied character for "
        "character. Never paraphrased. This is what makes the requirement checkable."
    )
    mandatory: bool = Field(
        default=True,
        description="True where the lease requires it of the tenant. False only where the lease "
        "offers it as an alternative or leaves it to the landlord's discretion.",
    )


class CoverageRequirements(BaseModel):
    """The checklist a lease's insurance clauses amount to."""

    party: str = Field(
        description="The tenant the lease binds, as the lease names them. Empty if the text does "
        "not say."
    )
    premises: str = Field(
        description="The premises the lease covers, as the lease names them."
    )
    lease_expiration: str = Field(
        description="When the term ends, as the lease states it. Empty string where the lease "
        "gives no date — 'five years from the Commencement Date' is not a date, and inventing "
        "one here would be a fabrication."
    )
    requirements: List[CoverageRequirement] = Field(
        description="Every insurance obligation the lease puts on the tenant, one line per "
        "obligation. Split a sentence carrying several duties into one entry each: a clause "
        "requiring $2M per occurrence, $4M aggregate and three named additional insureds is "
        "five lines, not one."
    )
    notes: List[str] = Field(
        description="Anything about the insurance obligations a reviewer should know that is not "
        "itself a checkable line — a cross-reference to an exhibit, a renewal notice period, an "
        "obligation stated too vaguely to check."
    )


class CoverageCheck(BaseModel):
    """One requirement, answered against the policy.

    Three real answers and one honest refusal. `met`, `short` and `missing` are
    the green, amber and red of the checklist; `unclear` is what a policy that
    does not say gets, and it is never quietly promoted to `met`.
    """

    label: str = Field(
        description="The requirement being answered, copied from the checklist line it answers "
        "so the two can be lined up."
    )
    status: Literal["met", "short", "missing", "unclear"] = Field(
        description="'met' where the policy plainly carries what the lease requires. 'short' "
        "where the policy carries this coverage but not enough of it — a $1,000,000 limit "
        "against a $2,000,000 requirement. 'missing' where the policy does not carry it at all. "
        "'unclear' where the policy is silent or too ambiguous to tell. Absence is never 'met'."
    )
    found_limit: str = Field(
        default="",
        description="What the policy actually provides for this line, worded as the policy words "
        "it. Empty string where the policy provides nothing.",
    )
    found_amount: Optional[float] = Field(
        default=None,
        description="The figure in `found_limit` as a plain number of dollars. Null where the "
        "policy states no figure.",
    )
    evidence: str = Field(
        description="The words of the policy that answer this line, copied character for "
        "character so they can be found and marked in the document. Empty string where the "
        "policy says nothing — an empty quote is the correct answer for a missing coverage, and "
        "inventing one would put a mark on a passage that does not say what it is cited for."
    )
    note: str = Field(
        description="One sentence on why this is the status. For a shortfall, say what was "
        "required against what was found."
    )


class CoverageMatch(BaseModel):
    """What reading a policy against a lease's checklist established."""

    carrier: str = Field(description="The insurer named on the policy. Empty if not stated.")
    policy_number: str = Field(description="The policy number. Empty if not stated.")
    policy_expiration: str = Field(
        description="When the policy lapses, as the document states it. Empty if not stated."
    )
    insured: str = Field(
        description="The named insured on the policy, as the policy names them. This is what "
        "says whether the policy even belongs to the tenant the lease binds."
    )
    checks: List[CoverageCheck] = Field(
        description="Exactly one entry per checklist line, in the order the checklist gave them. "
        "Never drop a line because the policy is silent about it — that is a 'missing', and it is "
        "the whole point of the exercise."
    )
    summary: str = Field(
        description="Two or three sentences on where the policy stands against the lease."
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


COVERAGE_REQ_SYSTEM_PROMPT = """You read commercial leases for AAT, a real estate asset and lease \
management company. You are given one lease. You turn its insurance obligations into a checklist \
that can be ticked off against a certificate of insurance.

Read the whole lease, not one section. Insurance duties are routinely split: the limits sit in the \
insurance section, the certificate and additional-insured duties often sit in a separate section \
later on, and an exhibit can add more. Find them wherever they are, and give each one the section \
number the lease itself uses.

One obligation per line. A single sentence requiring $2,000,000 per occurrence, $4,000,000 in the \
aggregate, and three named additional insureds is five separate lines, because a certificate can \
satisfy any one of them and fail the others. A line that bundles two duties cannot be answered.

Only what the lease requires of the tenant. Coverage the landlord carries is not the tenant's \
obligation and does not belong on the checklist.

Quote the words that impose each obligation, character for character, from the text you were \
given. The quote is what the requirement is later checked and marked against, so a paraphrase \
makes the line unverifiable. Where the lease states a dollar figure, put it in `required_amount` \
as a plain number as well; where it states none, leave it null rather than guessing at a market \
norm."""


COVERAGE_MATCH_SYSTEM_PROMPT = """You check certificates and policies of insurance for AAT, a real \
estate asset and lease management company. You are given a checklist of what a lease requires and \
one certificate or policy. You answer every line of the checklist against that document.

Answer every line, in the order given. A line the document is silent about is 'missing' — that \
silence is the finding the whole check exists to produce, so never drop the line and never let \
absence read as satisfaction.

Distinguish short from missing, because they are different conversations with a tenant. Coverage \
that is carried but below the required limit is 'short': say what was required and what was found. \
Coverage that is not carried at all is 'missing'. Where the document mentions something related \
but does not let you tell — an endorsement referred to but not described, a limit stated without \
saying what it applies to — that is 'unclear', not 'met'.

Compare amounts as amounts. A $2,000,000 aggregate against a $4,000,000 requirement is short even \
though both are stated in millions, and a per-occurrence limit is not an aggregate limit. Check \
that the named insured is actually the party the lease binds; a certificate for a different entity \
satisfies nothing.

Quote your evidence character for character out of the document you were given. That quote is used \
to mark the passage in the document, so an invented or tidied quote puts a mark on a passage that \
does not say what it is cited for. Where there is no evidence because the coverage is absent, \
return an empty quote — that is the correct answer, not a failure."""


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


# ---------------- Insurance Coverage Matching ----------------
#
# Three calls, in the order the work actually happens: read the lease into a
# checklist, answer the checklist against the certificate, then write the notice
# from the answers. They are separate calls for the same reason grading and
# drafting are separate — each wants instructions the others would undermine.
#
# The first two are deliberately not merged. A single call given both documents
# tends to read the certificate first and then find requirements that suit it,
# which produces a checklist that always passes. Extracting what the lease
# demands *before* the policy is in view is what makes the check mean anything.


def read_coverage_requirements(
    lease_text: str,
    filename: str = "lease",
    company: str = "",
    property_id: str = "",
    unit_id: str = "",
) -> CoverageRequirements:
    """Turn a lease's insurance clauses into a checklist that can be ticked off.

    The certificate is deliberately absent here. This call answers only "what
    does the lease demand", so that the answer cannot be shaped by what the
    tenant happens to have supplied.

    Offsets are not asked for, as with `find_clause`: the model quotes the words
    that impose each obligation, and where that passage sits is computed by
    matching in `coverage_match.locate_requirements`.
    """
    if not (lease_text or "").strip():
        raise ValueError("There is no lease text to read.")

    given = [f"Lease on file: {filename}"]
    if company:
        given.append(f"Company given: {company}")
    if property_id:
        given.append(f"Property given: {property_id}")
    if unit_id:
        given.append(f"Unit given: {unit_id}")

    prompt = (
        "<lease>\n" + lease_text + "\n</lease>\n\n"
        + "\n".join(given)
        + "\n\nList every insurance obligation this lease places on the tenant, one line per "
        "obligation, each with the section number the lease gives it and the words that impose "
        "it quoted exactly. Include the certificate and additional-insured duties wherever in "
        "the lease they sit, not only the ones in the insurance section."
    )

    if PROVIDER == "tritonai":
        return connect.ask_json(
            prompt,
            schema=CoverageRequirements,
            model=TRITONAI_MODEL,
            system=COVERAGE_REQ_SYSTEM_PROMPT,
            temperature=0,
            max_tokens=16000,
        )

    response = _client().messages.parse(
        model=ANTHROPIC_MODEL,
        max_tokens=16000,
        system=COVERAGE_REQ_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        output_format=CoverageRequirements,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to read this lease.")
    return response.parsed_output


def match_coverage(
    requirements: CoverageRequirements,
    policy_text: str,
    filename: str = "certificate",
) -> CoverageMatch:
    """Answer every checklist line against the certificate on file.

    The checklist goes in as the question, numbered, so the reply can be lined
    back up with it line for line. Every line must come back — a certificate
    silent about property coverage produces a `missing`, which is the finding the
    use case exists to surface, not an omission.
    """
    if not (policy_text or "").strip():
        raise ValueError("There is no certificate text to check.")
    if not requirements.requirements:
        raise ValueError("The lease produced no insurance requirements to check against.")

    checklist = "\n".join(
        f"{i + 1}. {r.label}"
        + (f" — requires {r.required_limit}" if r.required_limit else "")
        + f" [{r.section}] "
        + f"lease wording: “{r.quote}”"
        for i, r in enumerate(requirements.requirements)
    )

    prompt = (
        "<certificate name=\"" + filename + "\">\n" + policy_text + "\n</certificate>\n\n"
        + "<checklist>\n" + checklist + "\n</checklist>\n\n"
        + (f"The lease binds: {requirements.party}\n" if requirements.party else "")
        + (f"The premises: {requirements.premises}\n" if requirements.premises else "")
        + "\nAnswer every numbered line above against this certificate, in the same order and "
        "using the same label, one entry per line. Quote your evidence out of the certificate "
        "exactly. Where the certificate carries the coverage but not enough of it, mark it short "
        "and give both figures. Where it does not carry it at all, mark it missing and leave the "
        "evidence empty."
    )

    if PROVIDER == "tritonai":
        return connect.ask_json(
            prompt,
            schema=CoverageMatch,
            model=TRITONAI_MODEL,
            system=COVERAGE_MATCH_SYSTEM_PROMPT,
            temperature=0,
            max_tokens=16000,
        )

    response = _client().messages.parse(
        model=ANTHROPIC_MODEL,
        max_tokens=16000,
        system=COVERAGE_MATCH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
        output_format=CoverageMatch,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to read this certificate.")
    return response.parsed_output


def draft_from_coverage(
    requirements: CoverageRequirements,
    match: CoverageMatch,
    lease_text: str,
    company: str = "",
    property_id: str = "",
    unit_id: str = "",
    sender: str = "",
) -> DraftedNotice:
    """Write the tenant the letter the gaps call for.

    Grouped rather than listed: a tenant reading this needs to know what is short
    and what is absent, and those are two different asks. Lines that passed are
    named too — a notice that lists only failures reads as though nothing on the
    certificate was right, and the tenant then re-sends everything.

    The lease text goes in again so the requirement can be quoted off the source
    rather than out of the checklist's copy of it.
    """
    by_status = {"short": [], "missing": [], "unclear": [], "met": []}
    for check in match.checks:
        by_status.setdefault(check.status, []).append(check)

    def block(title: str, checks: list) -> str:
        if not checks:
            return ""
        lines = "\n".join(
            f"- {c.label}: required {_required_for(requirements, c.label) or 'as stated in the lease'}; "
            f"certificate shows {c.found_limit or 'nothing'}. {c.note}"
            for c in checks
        )
        return f"{title}\n{lines}\n\n"

    findings = (
        block("BELOW THE REQUIRED AMOUNT", by_status.get("short", []))
        + block("MISSING ENTIRELY", by_status.get("missing", []))
        + block("COULD NOT BE CONFIRMED FROM THE CERTIFICATE", by_status.get("unclear", []))
        + block("IN GOOD STANDING", by_status.get("met", []))
    )

    given = [f"{label}: {value}" for label, value in (
        ("Company", company), ("Property", property_id), ("Unit", unit_id)
    ) if value]

    prompt = (
        "<lease>\n" + lease_text + "\n</lease>\n\n"
        + ("Given with the run:\n" + "\n".join(given) + "\n\n" if given else "")
        + f"Tenant on the lease: {requirements.party or 'not stated'}\n"
        + f"Premises: {requirements.premises or 'not stated'}\n"
        + f"Named insured on the certificate: {match.insured or 'not stated'}\n"
        + f"Carrier: {match.carrier or 'not stated'}; policy {match.policy_number or 'not stated'}; "
        + f"expires {match.policy_expiration or 'not stated'}\n\n"
        + "The check that was made, line by line:\n\n"
        + findings
        + "Write the email to the tenant that follows from this. It must: open by naming the "
        "certificate reviewed and the lease section it was checked against; list what is below "
        "the required amount, giving the required figure and the figure on the certificate for "
        "each; list what is missing entirely; name what was confirmed as in good standing, "
        "briefly, so the tenant does not re-send it; and close by asking for an updated "
        "certificate. Quote the lease's own wording for any requirement you assert, with its "
        "section number, and put every such passage in `quoted_clauses`. Put anything the lease "
        "or the certificate did not support in `unresolved` and leave it out of the body."
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


def _required_for(requirements: CoverageRequirements, label: str) -> str:
    """The limit the lease set for a checklist line, looked up by its label.

    The match reply carries the label it was asked about but not the requirement
    behind it, and the notice has to state both figures to be worth sending.
    """
    target = (label or "").strip().lower()
    for req in requirements.requirements:
        if (req.label or "").strip().lower() == target:
            return req.required_limit
    return ""


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
