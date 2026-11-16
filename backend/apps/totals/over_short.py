"""Over/Short totals: what a store was accountable for against what it accounted for.

A total in the Over/Short category belongs to one group. "Accountable For" totals add up what
the store should hold (sales, for example); "Accounted For" totals add up what it can show
(cash counted, deposits, tenders). Each total's operator says whether it adds to its group or
takes away from it. The variance is accountable minus accounted, so a positive one is short
and a negative one is over, as in the cash office.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

from apps.totals.models import AuditTotalDefinition

ACCOUNTABLE = "Accountable For"
ACCOUNTED = "Accounted For"
TOTAL_NAME = "Over/Short"


@dataclass(frozen=True, slots=True)
class OverShort:
    accountable: Decimal
    accounted: Decimal

    @property
    def variance(self) -> Decimal:
        return self.accountable - self.accounted


def missing_accounted_for(definitions: Sequence[AuditTotalDefinition]) -> bool:
    """True when Over/Short is defined but nothing says what the store can account for.

    The installed totals define only the accountable side (sales). Until Finance configures an
    Accounted For total (cash counted, tenders, deposits), no store day can show an Over/Short of zero.
    """
    members = [row for row in definitions if row.category == "Over/Short"]
    return bool(members) and not any(row.over_short_group == ACCOUNTED for row in members)


def calculate(
    definitions: Sequence[AuditTotalDefinition],
    configured_totals: Mapping[str, Mapping[str | None, Decimal]],
) -> OverShort | None:
    """None when no Over/Short total is defined, so no total is created for the store day."""
    members = [row for row in definitions if row.category == "Over/Short"]
    if not members:
        return None
    sums = {ACCOUNTABLE: Decimal("0"), ACCOUNTED: Decimal("0")}
    for row in members:
        value = sum(configured_totals.get(row.id, {}).values(), Decimal("0"))
        sums[row.over_short_group] += -value if row.over_short_operator == "-" else value
    return OverShort(sums[ACCOUNTABLE], sums[ACCOUNTED])
