"""LLM-backed document analysis.

Sends an uploaded document to a model and gets back a structured,
machine-readable verdict (approve / needs human review / reject) plus
per-requirement findings. This is where a use case's decision is actually made.

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


def _instructions(rubric: dict, filename: str, property_id: Optional[str], unit_id: Optional[str]) -> str:
    requirements = "\n".join(f"{i}. {r}" for i, r in enumerate(rubric["requirements"], 1))
    context_lines = [
        f"Workflow: {rubric.get('title') or 'this use case'}",
        f"Uploaded file: {filename}",
    ]
    if property_id:
        context_lines.append(f"Property ID on file: {property_id}")
    if unit_id:
        context_lines.append(f"Unit on file: {unit_id}")

    return (
        "\n".join(context_lines)
        + (
            f"\n\nThis workflow expects: {rubric['document_kinds']}."
            if rubric.get("document_kinds")
            else ""
        )
        + f"\n\nGrade the attached document against these requirements:\n{requirements}\n\n"
        "Return one finding per numbered requirement, in order."
    )


def analyze_document(
    workflow_id: str,
    file_bytes: bytes,
    filename: str,
    media_type: str,
    property_id: Optional[str] = None,
    unit_id: Optional[str] = None,
    rubric: Optional[dict] = None,
) -> DocumentVerdict:
    """Grade one document against a workflow's rubric.

    Routes through TritonAI or Anthropic depending on ``LLM_PROVIDER``, and lets
    provider errors propagate so the API layer can map them to status codes —
    neither route retries on another model, which is deliberate.

    `rubric` is passed in by the runner, which reads it from the use case being
    run. Use cases are created at runtime now, so their requirements live in the
    database and cannot be looked up here; WORKFLOW_RUBRICS remains the fallback
    for the shipped set and for callers that only have an id.
    """
    if rubric is None:
        rubric = WORKFLOW_RUBRICS.get(workflow_id)
    if not rubric or not rubric.get("requirements"):
        raise ValueError(
            f"No requirements are set on '{workflow_id}', so there is nothing to grade against."
        )

    instructions = _instructions(rubric, filename, property_id, unit_id)

    if PROVIDER == "tritonai":
        return _analyze_via_tritonai(file_bytes, media_type, filename, instructions)
    return _analyze_via_anthropic(file_bytes, media_type, instructions)


def _analyze_via_tritonai(
    file_bytes: bytes, media_type: str, filename: str, instructions: str
) -> DocumentVerdict:
    """Grade through TritonAI's proxy.

    Text-only, so the document is inlined. The schema round-trips as JSON mode
    plus a schema hint and is validated by Pydantic on the way back, so a
    malformed reply raises here rather than reaching the UI half-formed.
    """
    document = _document_text(file_bytes, media_type, filename)
    prompt = f"<document name=\"{filename}\">\n{document}\n</document>\n\n{instructions}"

    return connect.ask_json(
        prompt,
        schema=DocumentVerdict,
        model=TRITONAI_MODEL,
        system=SYSTEM_PROMPT,
        temperature=0,
        max_tokens=16000,
    )


def _analyze_via_anthropic(file_bytes: bytes, media_type: str, instructions: str) -> DocumentVerdict:
    """Grade through the Anthropic SDK, which reads PDFs and images natively."""
    response = _client().messages.parse(
        model=ANTHROPIC_MODEL,
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": [
                    _document_block(file_bytes, media_type),
                    {"type": "text", "text": instructions},
                ],
            }
        ],
        output_format=DocumentVerdict,
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to analyze this document.")

    return response.parsed_output
