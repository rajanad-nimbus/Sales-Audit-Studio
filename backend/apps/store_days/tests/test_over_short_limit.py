"""The $10 over/short limit drafted for every workbook difference total (ontology/extend_sales_audit.py)."""

from decimal import Decimal
from pathlib import Path

import pytest
from apps.store_days.rule_evaluation import rule_breaches
from apps.totals.client_totals import CLIENT_TOTALS

SCRIPT = Path(__file__).resolve().parents[4] / "ontology" / "extend_sales_audit.py"


def drafted():
    source = SCRIPT.read_text()
    namespace: dict = {}
    exec(  # noqa: S102 - the literal block of constants in our own script
        source[source.index("OVER_SHORT_LIMIT") : source.index("# code, name, failure class")],
        namespace,
    )
    return namespace["OVER_SHORT_LIMIT"], namespace["OVER_SHORT_TOTALS"]


def test_the_limit_is_ten_dollars_and_covers_every_workbook_difference_total():
    limit, totals = drafted()
    names = {t["name"] for t in CLIENT_TOTALS}
    expected = {n for n in names if n.endswith("O") and n[:-1] + "P" in names}
    assert limit == "10" and set(totals) == expected and len(totals) == len(set(totals)) == 52


@pytest.mark.parametrize(
    ("difference", "breach"),
    [("0", False), ("3.42", False), ("10", False), ("-10", False),
     ("10.01", True), ("-10.01", True), ("25", True)],
)
def test_a_difference_breaches_only_beyond_ten_dollars_either_way(difference, breach):
    limit, _ = drafted()
    assert rule_breaches(Decimal(difference), Decimal(limit), "ABS_GT") is breach
