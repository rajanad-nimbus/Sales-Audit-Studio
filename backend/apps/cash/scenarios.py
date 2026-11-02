"""A matrix of cash-office combinations with the status and variance each one must produce.

Each scenario seeds a store day's sales, then runs declarations and deposits in order. `expect` lists the final
(status, variance) of every step in that order: a later declaration can re-reconcile an earlier deposit, so results are
read after all steps have run (see tests/test_cash_scenario_matrix.py).
"""

from dataclasses import dataclass, field

STORE = "S1"
DAY = "2026-10-01"
BALANCED = ("Balanced", "0.00")


@dataclass
class Scenario:
    name: str
    group: str
    sales: list[tuple]  # (register, cashier, signed amount, type, tender)
    steps: list[tuple]  # ("declare", fields) or ("deposit", amount)
    expect: list[tuple[str, str]] = field(default_factory=list)


def over_short(variance: str) -> tuple[str, str]:
    return ("Over/Short", variance)


def sale(register="R1", amount="100.00", cashier=None, tender="Cash") -> tuple:
    return (register, cashier, amount, "Sale", tender)


def refund(register="R1", amount="20.00", cashier=None, tender="Cash") -> tuple:
    return (register, cashier, f"-{amount}", "Return", tender)


def declare(
    register="R1", cash="0.00", paid_out="0.00", safe_drop="0.00", cashier=None, tender="Cash"
) -> tuple:
    fields = {
        "register_id": register,
        "cashier_id": cashier,
        "tender_type": tender,
        "declared_cash": cash,
        "paid_out": paid_out,
        "safe_drop": safe_drop,
    }
    return ("declare", fields)


# R1: 100 + 50 - 20 = 130 of cash; R2: 200 of cash, all rung by cashier K2; R3 sold nothing.
DAY_SALES = [
    sale("R1", "100.00"),
    sale("R1", "50.00"),
    refund("R1", "20.00"),
    sale("R2", "200.00", "K2"),
]


def scenario(name, group, steps, expect, sales=None) -> Scenario:
    return Scenario(name, group, DAY_SALES if sales is None else sales, steps, expect)


def register_scenarios() -> list[Scenario]:
    return [
        scenario("register balanced", "register", [declare(cash="130.00")], [BALANCED]),
        scenario("register short", "register", [declare(cash="120.00")], [over_short("10.00")]),
        scenario("register over", "register", [declare(cash="140.00")], [over_short("-10.00")]),
        scenario(
            "register paid-out explains the gap",
            "register",
            [declare(cash="120.00", paid_out="10.00")],
            [BALANCED],
        ),
        scenario(
            "register safe drop",
            "register",
            [declare(cash="30.00", safe_drop="100.00")],
            [BALANCED],
        ),
        scenario(
            "register paid-out and safe drop",
            "register",
            [declare(cash="100.00", paid_out="10.00", safe_drop="20.00")],
            [BALANCED],
        ),
        scenario(
            "register paid-out larger than the gap",
            "register",
            [declare(cash="120.00", paid_out="25.00")],
            [over_short("-15.00")],
        ),
        scenario(
            "register with no sales balanced at zero",
            "register",
            [declare("R3", cash="0.00")],
            [BALANCED],
        ),
        scenario(
            "register with no sales but cash declared",
            "register",
            [declare("R3", cash="5.00")],
            [over_short("-5.00")],
        ),
        scenario(
            "register recounted twice",
            "register",
            [declare(cash="120.00"), declare(cash="130.00")],
            [over_short("10.00"), BALANCED],
        ),
        scenario(
            "register cashier balanced",
            "register",
            [declare("R2", cash="200.00", cashier="K2")],
            [BALANCED],
        ),
        scenario(
            "register cashier with no sales",
            "register",
            [declare("R2", cash="0.00", cashier="K9")],
            [BALANCED],
        ),
        scenario(
            "register cashier short",
            "register",
            [declare("R2", cash="190.00", cashier="K2")],
            [over_short("10.00")],
        ),
        scenario(
            "store-wide declaration balanced",
            "register",
            [declare(None, cash="330.00")],
            [BALANCED],
        ),
        scenario(
            "store-wide declaration short",
            "register",
            [declare(None, cash="320.00")],
            [over_short("10.00")],
        ),
        scenario(
            "a day with no sales", "register", [declare(None, cash="0.00")], [BALANCED], sales=[]
        ),
        scenario(
            "card tender declaration",
            "register",
            [declare(cash="300.00", tender="Card")],
            [BALANCED],
            sales=DAY_SALES + [sale("R1", "300.00", tender="Card")],
        ),
        scenario(
            "voucher tender declaration short",
            "register",
            [declare(cash="40.00", tender="Voucher")],
            [over_short("10.00")],
            sales=DAY_SALES + [sale("R1", "50.00", tender="Voucher")],
        ),
    ]


def deposit_scenarios() -> list[Scenario]:
    counted = declare(cash="100.00", safe_drop="30.00")  # 130.00 reaches the bank
    both = [declare("R1", cash="130.00"), declare("R2", cash="200.00", cashier=None)]
    return [
        scenario(
            "deposit matches", "deposit", [counted, ("deposit", "130.00")], [BALANCED, BALANCED]
        ),
        scenario(
            "deposit short",
            "deposit",
            [counted, ("deposit", "115.00")],
            [BALANCED, over_short("15.00")],
        ),
        scenario(
            "deposit over",
            "deposit",
            [counted, ("deposit", "140.00")],
            [BALANCED, over_short("-10.00")],
        ),
        scenario(
            "deposit recorded before the count",
            "deposit",
            [("deposit", "130.00"), declare(cash="130.00")],
            [BALANCED, BALANCED],  # the later declaration re-reconciles the deposit
        ),
        scenario(
            "deposit of two registers",
            "deposit",
            [*both, ("deposit", "330.00")],
            [BALANCED, BALANCED, BALANCED],
        ),
        scenario(
            "deposit of two registers short",
            "deposit",
            [*both, ("deposit", "300.00")],
            [BALANCED, BALANCED, over_short("30.00")],
        ),
        scenario(
            "store-wide declaration replaces register counts",
            "deposit",
            [declare("R1", cash="130.00"), declare(None, cash="330.00"), ("deposit", "330.00")],
            [BALANCED, BALANCED, BALANCED],
        ),
        scenario(
            "deposit of a safe drop only",
            "deposit",
            [declare(cash="0.00", safe_drop="130.00"), ("deposit", "130.00")],
            [BALANCED, BALANCED],
        ),
        scenario(
            "paid-out is not deposited",
            "deposit",
            [declare(cash="120.00", paid_out="10.00"), ("deposit", "120.00")],
            [BALANCED, BALANCED],
        ),
        scenario(
            "paid-out deposited by mistake",
            "deposit",
            [declare(cash="120.00", paid_out="10.00"), ("deposit", "130.00")],
            [BALANCED, over_short("-10.00")],
        ),
        scenario(
            "deposit with nothing counted",
            "deposit",
            [("deposit", "130.00")],
            [over_short("-130.00")],
        ),
    ]


def scenarios() -> list[Scenario]:
    return register_scenarios() + deposit_scenarios()
