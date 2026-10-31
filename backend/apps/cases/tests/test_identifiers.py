"""Case identifiers retain their date prefix and use the collision-resistant suffix length."""
from types import SimpleNamespace

from apps.cases import identifiers


def test_case_number_uses_a_12_character_uppercase_hex_suffix(monkeypatch):
    monkeypatch.setattr(
        identifiers.uuid,
        "uuid4",
        lambda: SimpleNamespace(hex="a1b2c3d4e5f678901234567890abcdef"),
    )

    assert identifiers.generate_case_number("2026-10-31") == "ZA-20261031-A1B2C3D4E5F6"
