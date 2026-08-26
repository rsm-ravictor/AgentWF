"""Tests for which LLM route a decision is made through, and what it can read.

No network and no key: these cover the routing, the credential reporting, and the
text extraction that the TritonAI route depends on. The model call itself is
stubbed — a test that reached UCSD's proxy would be a different kind of test.

    python -m pytest tests/ -q
"""

import importlib

import pytest

from aat_system import connect


@pytest.fixture(autouse=True)
def restore_analyzer_module():
    """Put the module back after a test reloads it under a different environment.

    Without this, the last reload's provider would leak into anything else that
    imports llm_analyzer later in the session — including main.py.
    """
    yield
    import aat_system.llm_analyzer as module

    importlib.reload(module)


def analyzer(monkeypatch, **env):
    """Reload llm_analyzer with a given environment, since it reads env at import."""
    for key in ("LLM_PROVIDER", "TRITONAI_API_KEY", "TRITONAI_MODEL", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    import aat_system.llm_analyzer as module

    return importlib.reload(module)


# ---------------- Which route is taken ----------------

def test_tritonai_is_the_default_route(monkeypatch):
    mod = analyzer(monkeypatch)
    assert mod.PROVIDER == "tritonai"
    # The model reported to the UI is the one the active route will actually use.
    assert mod.MODEL == connect.DEFAULT_MODEL


def test_the_model_the_ui_reports_follows_the_provider(monkeypatch):
    triton = analyzer(monkeypatch, LLM_PROVIDER="tritonai", TRITONAI_MODEL="gemini-3-flash")
    assert triton.MODEL == "gemini-3-flash"

    direct = analyzer(monkeypatch, LLM_PROVIDER="anthropic", ANTHROPIC_MODEL="claude-opus-5")
    assert direct.MODEL == "claude-opus-5"


def test_the_default_model_is_one_this_team_can_actually_reach():
    """Changing this is a deliberate act, not a drive-by edit.

    It was changed off claude-opus-4-6-v1 because this deployment's TritonAI team
    is not entitled to it: every call came back 403 team_model_access_denied,
    which presented as runs that could not read any document. `list_models()`
    reports what the key can reach; this is one of those.
    """
    assert connect.DEFAULT_MODEL == "claude-sonnet-4-6"


def test_a_fenced_json_reply_is_still_parsed():
    """Some models on the proxy wrap a json_object reply in a markdown fence.

    Rejecting those made a parsing failure look like a reading failure: the run
    reported that it could not read the document, when the model had answered
    correctly.
    """
    fence = chr(96) * 3
    fenced = fence + 'json' + chr(10) + '{"a": 1}' + chr(10) + fence
    assert connect._unfence(fenced) == '{"a": 1}'
    assert connect._unfence('{"a": 1}') == '{"a": 1}'
    assert connect._unfence("") == ""


# ---------------- Credentials ----------------

def test_each_route_checks_its_own_key(monkeypatch):
    mod = analyzer(monkeypatch, LLM_PROVIDER="tritonai", TRITONAI_API_KEY="sk-real-key")
    assert mod.has_api_key() is True
    assert mod.active_route()["key_env"] == "TRITONAI_API_KEY"

    # An Anthropic key does not make the TritonAI route usable.
    mod = analyzer(monkeypatch, LLM_PROVIDER="tritonai", ANTHROPIC_API_KEY="sk-ant-real")
    assert mod.has_api_key() is False


def test_the_shipped_placeholder_does_not_count_as_configured(monkeypatch):
    mod = analyzer(monkeypatch, LLM_PROVIDER="tritonai", TRITONAI_API_KEY="replace-with-your-tritonai-key")
    # Otherwise the UI reports "configured" right up until the proxy rejects it
    # halfway through a run.
    assert mod.has_api_key() is False


def test_active_route_describes_where_decisions_are_made(monkeypatch):
    mod = analyzer(monkeypatch, LLM_PROVIDER="tritonai", TRITONAI_API_KEY="sk-real", TRITONAI_MODEL="api-llama-4-scout")
    route = mod.active_route()
    assert route == {
        "provider": "tritonai",
        "model": "api-llama-4-scout",
        "configured": True,
        "key_env": "TRITONAI_API_KEY",
    }


# ---------------- What the text-only route can read ----------------

def test_plain_text_is_passed_through(monkeypatch):
    mod = analyzer(monkeypatch)
    text = mod._document_text(b"General liability: $1,000,000", "text/plain", "coi.txt")
    assert "General liability" in text


def test_an_image_is_refused_with_a_way_forward(monkeypatch):
    mod = analyzer(monkeypatch)
    with pytest.raises(RuntimeError) as exc:
        mod._document_text(b"\x89PNG fake", "image/png", "scan.png")
    # The refusal has to say what to do instead, or it is just a dead end.
    assert "LLM_PROVIDER=anthropic" in str(exc.value)
    assert "scan.png" in str(exc.value)


def test_a_pdf_with_no_extractable_text_is_refused_not_graded_blind(monkeypatch):
    from pypdf import PdfWriter

    mod = analyzer(monkeypatch)
    buffer = __import__("io").BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.write(buffer)

    with pytest.raises(RuntimeError) as exc:
        mod._document_text(buffer.getvalue(), "application/pdf", "scanned.pdf")
    assert "No text could be extracted" in str(exc.value)


# ---------------- The call itself ----------------

def test_the_tritonai_route_asks_for_a_validated_schema(monkeypatch):
    """The verdict must come back as DocumentVerdict, not as prose to parse."""
    mod = analyzer(monkeypatch, LLM_PROVIDER="tritonai", TRITONAI_API_KEY="sk-real")
    seen = {}

    def fake_ask_json(prompt, **kwargs):
        seen["prompt"] = prompt
        seen["kwargs"] = kwargs
        return mod.DocumentVerdict(
            document_type="Certificate of insurance",
            is_expected_type=True,
            summary="A COI.",
            decision="needs_human_review",
            confidence="medium",
            reasoning="Limit is short of the requirement.",
            findings=[],
            extracted_fields=[],
            missing_information=["AAT requirements document"],
        )

    monkeypatch.setattr(mod.connect, "ask_json", fake_ask_json)

    verdict = mod.analyze_document(
        "vendor-insurance", b"General liability: $1,000,000", "coi.txt", "text/plain"
    )

    assert verdict.decision == "needs_human_review"
    assert seen["kwargs"]["schema"] is mod.DocumentVerdict
    assert seen["kwargs"]["model"] == mod.TRITONAI_MODEL
    # Deterministic, because a grading decision should not wander between runs.
    assert seen["kwargs"]["temperature"] == 0
    # The rubric and the document both have to reach the model.
    assert "General liability" in seen["prompt"]
    assert "at least $2,000,000" in seen["prompt"]


def test_an_unknown_workflow_is_refused_before_any_model_call(monkeypatch):
    mod = analyzer(monkeypatch)
    with pytest.raises(ValueError):
        mod.analyze_document("not-a-workflow", b"x", "x.txt", "text/plain")
