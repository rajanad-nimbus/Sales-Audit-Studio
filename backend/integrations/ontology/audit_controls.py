"""Sales Audit rules as maintained in Ontology Studio.

ZeTSA does not author or approve audit rules. Every rule it evaluates is an approved ``audit_control``
business rule read from a release-pinned ontology snapshot. When no approved release with a complete,
well-formed set of controls is available, nothing is evaluated: callers get ``AuditRulesUnavailable``
and must stop (fail closed) rather than fall back to built-in defaults.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

RULE_TYPE = "audit_control"
POLICY_LIMITS = ("autonomy_limit", "timing_autonomy_limit")
AGENT_LEVELS = ("collect", "propose", "act")
# What an approved release that defines no level for a failure class falls back to. An unlisted class
# is collect, so a new code can never propose or act by accident.
DEFAULT_LEVEL = "collect"
DEFAULT_LEVELS = {
    "DUPLICATE_SALES": "act",
    "SHOPIFY_DUPLICATE_ORDER": "act",
    **{
        code: "propose"
        for code in (
            "TIMING_DIFFERENCE",
            "AMOUNT_MISMATCH",
            "UNMATCHED_SALE",
            "ORPHAN_PAYMENT",
            "BANK_VARIANCE",
            "MISSING_DEPOSIT",
            "GL_VARIANCE",
            "MISSING_GL_POSTING",
            "DISC_REASON_REQ",
        )
    },
}


def default_level(exception_type: str) -> str:
    return DEFAULT_LEVELS.get(exception_type, DEFAULT_LEVEL)
COMPARISONS = {"ABS_GT", "GT", "GTE", "LT", "LTE", "EQ", "NE"}
BALANCE_LEVELS = {"Store", "Register", "Cashier"}
SEVERITIES = {"Low", "Medium", "High", "Critical"}


class AuditRulesUnavailable(RuntimeError):
    """No approved, complete set of audit controls is available from the ontology."""


@dataclass(frozen=True, slots=True)
class TotalRule:
    """A store-day total threshold. Carries the attributes the evaluator reads from a rule."""

    id: str  # the ontology rule code
    name: str
    total_name: str
    balance_level: str
    comparison: str
    threshold: Decimal
    threshold_currency: str | None
    severity: str
    start_date: str | None = None
    end_date: str | None = None
    location_traits: tuple[str, ...] = ()
    execution_order: int = 100
    stop_processing: bool = False
    owner: str | None = None
    revision: int = 1
    enabled: bool = True
    status: str = "Active"


@dataclass(frozen=True, slots=True)
class TransactionRule:
    id: str
    name: str
    error_code: str
    conditions: tuple[dict, ...]
    record_type: str | None = None
    match_mode: str = "Match"
    evaluation_scope: str = "Transaction"
    start_date: str | None = None
    end_date: str | None = None
    location_traits: tuple[str, ...] = ()
    execution_order: int = 100
    stop_processing: bool = False
    owner: str | None = None
    revision: int = 1
    enabled: bool = True
    status: str = "Active"


@dataclass(frozen=True, slots=True)
class AuditControls:
    release_id: object
    total_rules: tuple[TotalRule, ...] = ()
    transaction_rules: tuple[TransactionRule, ...] = ()
    limits: dict[str, Decimal] = field(default_factory=dict)
    limit_currency: str = "USD"
    levels: dict[str, str] = field(default_factory=dict)

    def level_for(self, exception_type: str) -> str:
        """The agent level the approved release gives a failure class, else the documented default."""
        return self.levels.get(exception_type) or default_level(exception_type)

    @property
    def autonomy_limit(self) -> Decimal:
        return self.limits["autonomy_limit"]

    @property
    def timing_autonomy_limit(self) -> Decimal:
        return self.limits["timing_autonomy_limit"]


def _money(value, label: str) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} is not a number") from None
    if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
        raise ValueError(f"{label} must be a non-negative amount with at most two decimal places")
    return amount


def _day(value, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError(f"{label} must use YYYY-MM-DD")
    return value


def _common(config: dict) -> dict:
    traits = config.get("location_traits") or []
    order = config.get("execution_order", 100)
    if not isinstance(traits, list) or not all(isinstance(t, str) and t for t in traits):
        raise ValueError("location_traits must be a list of codes")
    if not isinstance(order, int) or isinstance(order, bool):
        raise ValueError("execution_order must be a whole number")
    return {
        "start_date": _day(config.get("start_date"), "start_date"),
        "end_date": _day(config.get("end_date"), "end_date"),
        "location_traits": tuple(sorted(traits)),
        "execution_order": order,
        "stop_processing": config.get("stop_processing") is True,
    }


def parse_controls(release_id, rules: list[dict]) -> AuditControls:
    """Build controls from approved ontology rules; any malformed control rejects the release."""
    totals: list[TotalRule] = []
    transactions: list[TransactionRule] = []
    limits: dict[str, Decimal] = {}
    levels: dict[str, str] = {}
    currencies: set[str] = set()
    seen: set[str] = set()
    for rule in rules:
        if rule.get("ruleType") != RULE_TYPE:
            continue
        code = rule.get("ruleCode")
        try:
            if not isinstance(code, str) or not code or code in seen:
                raise ValueError("a control needs a unique rule code")
            seen.add(code)
            config = rule.get("config")
            if not isinstance(config, dict):
                raise ValueError("no config")
            owner = rule.get("owner") if isinstance(rule.get("owner"), str) else None
            name = rule.get("name")
            if not isinstance(name, str) or not name:
                raise ValueError("no name")
            family = config.get("family")
            if family == "policy_limit":
                limit = config.get("limit")
                if limit not in POLICY_LIMITS or limit in limits:
                    raise ValueError("unknown or repeated policy limit")
                limits[limit] = _money(config.get("amount"), "amount")
                currencies.add(config.get("currency") or "USD")
            elif family == "agent_level":
                exception_type = config.get("exception_type")
                if not isinstance(exception_type, str) or not exception_type:
                    raise ValueError("no exception_type")
                if exception_type in levels:
                    raise ValueError("repeated exception_type")
                if config.get("level") not in AGENT_LEVELS:
                    raise ValueError("level must be collect, propose or act")
                levels[exception_type] = config["level"]
            elif family == "store_day_total":
                if config.get("comparison", "ABS_GT") not in COMPARISONS:
                    raise ValueError("unknown comparison")
                if config.get("balance_level", "Store") not in BALANCE_LEVELS:
                    raise ValueError("unknown balance level")
                if config.get("severity", "Medium") not in SEVERITIES:
                    raise ValueError("unknown severity")
                if not isinstance(config.get("total_name"), str) or not config["total_name"]:
                    raise ValueError("no total_name")
                totals.append(
                    TotalRule(
                        id=code,
                        name=name,
                        total_name=config["total_name"],
                        balance_level=config.get("balance_level", "Store"),
                        comparison=config.get("comparison", "ABS_GT"),
                        threshold=_money(config.get("threshold"), "threshold"),
                        threshold_currency=config.get("threshold_currency", "USD"),
                        severity=config.get("severity", "Medium"),
                        owner=owner,
                        **_common(config),
                    )
                )
            elif family == "transaction_field":
                conditions = config.get("conditions")
                if not isinstance(conditions, list) or not conditions:
                    raise ValueError("no conditions")
                if not isinstance(config.get("error_code"), str) or not config["error_code"]:
                    raise ValueError("no error_code")
                transactions.append(
                    TransactionRule(
                        id=code,
                        name=name,
                        error_code=config["error_code"],
                        conditions=tuple(dict(c) for c in conditions),
                        record_type=config.get("record_type"),
                        match_mode=config.get("match_mode", "Match"),
                        evaluation_scope=config.get("evaluation_scope", "Transaction"),
                        owner=owner,
                        **_common(config),
                    )
                )
            else:
                raise ValueError("unknown family")
        except (ValueError, TypeError) as exc:
            raise AuditRulesUnavailable(f"Audit control {code!r} is malformed: {exc}") from exc
    missing = [name for name in POLICY_LIMITS if name not in limits]
    if missing:
        raise AuditRulesUnavailable(
            "The approved ontology release does not define policy limit(s): " + ", ".join(missing)
        )
    if len(currencies) != 1:
        raise AuditRulesUnavailable("Policy limits must all use one currency")
    return AuditControls(
        release_id=release_id,
        total_rules=tuple(sorted(totals, key=lambda r: (r.execution_order, r.name, r.id))),
        transaction_rules=tuple(sorted(transactions, key=lambda r: (r.execution_order, r.name, r.id))),
        limits=limits,
        limit_currency=currencies.pop(),
        levels=levels,
    )
