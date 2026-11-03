"""The assistant gets approved glossary definitions only when Studio supplies a release."""

from apps.assistant import chat


def test_no_reference_without_studio(monkeypatch):
    monkeypatch.setattr(chat.ontology_integration, "context", lambda: {"ontology": None})
    assert chat.ontology_reference("what is settlement match") is None


def test_reference_returns_only_mentioned_terms(monkeypatch):
    ctx = {
        "ontology": {"releaseId": 7},
        "definitions": {
            "glossary": [
                {"term": "Settlement Match", "definition": "x"},
                {"term": "Void", "definition": "y"},
            ]
        },
    }
    monkeypatch.setattr(chat.ontology_integration, "context", lambda: ctx)
    out = chat.ontology_reference("Explain settlement match please")
    assert out["release"] == 7 and [g["term"] for g in out["glossary"]] == ["Settlement Match"]
