"""Screen access: the one place that says which role may open which screen, and which API areas back each screen.

The UI reads this (through /api/me) to build navigation and guard routes. The API reads it too, in the
authentication middleware, so a role that cannot open a screen cannot call that screen's data either. Hiding a menu item
is never the control; this is.
"""

from dataclasses import dataclass

ROLES = (
    "admin",
    "finance",
    "it",
    "analyst",
    "sales_audit_manager",
    "auditor",
    "store_manager",
    "store_staff",
    "viewer",
)
ROLE_LABELS = {
    "admin": "Administrator",
    "finance": "Finance Approver",
    "it": "IT Support Admin",
    "analyst": "Sales Auditor",
    "sales_audit_manager": "Sales Audit Manager",
    "auditor": "Internal Auditor",
    "store_manager": "Store Manager",
    "store_staff": "Store Sales Staff",
    "viewer": "Viewer",
}
EVERYONE = ROLES


@dataclass(frozen=True)
class Screen:
    key: str
    path: str
    label: str
    group: str
    roles: tuple[str, ...]  # admin is always allowed, so it is not repeated here
    apis: tuple[str, ...] = ()  # API path prefixes whose data this screen needs
    optional: tuple[
        str, ...
    ] = ()  # extra calls the screen tolerates being refused (a feature that only some roles see)

    @property
    def all_apis(self) -> tuple[str, ...]:
        return self.apis + self.optional


SCREENS: tuple[Screen, ...] = (
    Screen(
        "my-preferences",
        "/preferences",
        "My Preferences",
        "Your Account",
        EVERYONE,
        ("/api/me/preferences",),
    ),
    Screen(
        "command-center",
        "/",
        "Command Center",
        "Workspace",
        ("analyst", "finance", "it", "auditor"),
        (
            "/api/action-center",
            "/api/command-center",
            "/api/connectors",
            "/api/workflows",
            "/api/validations",
            "/api/decisions",
            "/api/policy-evaluations",
        ),
    ),
    Screen(
        "guided-tasks",
        "/tasks",
        "Guided Tasks",
        "Workspace",
        EVERYONE,
    ),
    Screen(
        "actions",
        "/actions",
        "Action Center",
        "Workspace",
        ("analyst", "finance", "auditor"),
        ("/api/action-center", "/api/me/saved-views"),
        optional=("/api/agent/eligible",),
    ),  # one-click agent approvals are Finance only; the page copes without them
    Screen(
        "cases",
        "/cases",
        "Cases",
        "Workspace",
        ("analyst", "finance", "auditor"),
        (
            "/api/cases",
            "/api/exceptions",
            "/api/findings",
            "/api/recommendations",
            "/api/workflows",
            "/api/validations",
            "/api/policy-evaluations",
            "/api/decisions",
            "/api/reconciliation",
            "/api/action-center",
        ),
    ),
    Screen(
        "store-days",
        "/store-days",
        "Store Days & Totals",
        "Store Day Audit",
        ("analyst", "finance", "it", "store_manager", "store_staff", "auditor"),
        ("/api/store-days", "/api/me/saved-views"),
    ),
    Screen(
        "total-history",
        "/total-history",
        "Total Audit Trail",
        "Store Day Audit",
        ("analyst", "finance", "it", "auditor"),
        ("/api/total-history", "/api/stores"),
    ),
    Screen(
        "misc-totals",
        "/misc-totals",
        "Miscellaneous Totals",
        "Store Day Audit",
        ("analyst", "finance", "it", "auditor"),
        ("/api/misc-totals", "/api/stores"),
    ),
    Screen(
        "transactions",
        "/transactions",
        "Transactions",
        "Store Day Audit",
        ("analyst", "finance", "it", "store_manager", "auditor"),
        ("/api/transactions", "/api/me/saved-views"),
    ),
    Screen(
        "item-summary",
        "/item-summary",
        "Item Summary",
        "Store Day Audit",
        ("analyst", "finance", "it", "auditor"),
        ("/api/item-summary", "/api/stores"),
    ),
    Screen(
        "tender-summary",
        "/tender-summary",
        "Tender Summary",
        "Store Day Audit",
        ("analyst", "finance", "it", "auditor"),
        ("/api/tender-summary", "/api/stores"),
    ),
    Screen(
        "ledger-view",
        "/ledger",
        "General Ledger View",
        "Store Day Audit",
        ("finance", "it"),
        ("/api/gl/journal-preview", "/api/stores"),
    ),
    Screen(
        "register-events",
        "/register-events",
        "Register Events",
        "Store Day Audit",
        ("analyst", "finance", "it", "auditor"),
        ("/api/register-events",),
    ),
    Screen(
        "register-summary",
        "/register-summary",
        "Cashier / Register Summary",
        "Store Day Audit",
        ("analyst", "finance", "it", "auditor"),
        ("/api/register-summary", "/api/stores"),
    ),
    Screen(
        "cash-office",
        "/cash-office",
        "Cash Office",
        "Store Day Audit",
        ("analyst", "finance", "store_manager", "auditor"),
        ("/api/cash-controls", "/api/cash", "/api/vouchers"),
    ),
    Screen(
        "ach-maintenance",
        "/ach",
        "ACH Maintenance",
        "Store Day Audit",
        ("finance", "it"),
        ("/api/ach", "/api/stores"),
    ),
    Screen(
        "foundation",
        "/foundation",
        "Foundation Data",
        "Store Day Audit",
        ("finance", "it"),
        ("/api/foundation", "/api/ingest/shopify/sync-reference"),
    ),
    Screen(
        "audit-errors",
        "/audit-errors",
        "Audit Errors",
        "Investigate & Correct",
        ("analyst", "finance", "it", "auditor"),
        ("/api/audit-errors", "/api/me/saved-views"),
    ),
    Screen(
        "corrections",
        "/corrections",
        "Transaction Corrections",
        "Investigate & Correct",
        ("analyst", "finance", "auditor"),
        ("/api/transaction-adjustments",),
    ),
    Screen(
        "audit",
        "/audit",
        "Audit Trail",
        "Investigate & Correct",
        ("finance", "it", "auditor"),
        ("/api/audit",),
    ),
    # Configuration. General (appearance, account) is personal, so everyone has it and it needs no data from the API.
    Screen(
        "reports-variances",
        "/reports/variances",
        "Total variances",
        "Reports",
        ("analyst", "finance", "it", "auditor"),
        ("/api/reports",),
    ),
    Screen(
        "reports-errors",
        "/reports/errors",
        "Errors by store",
        "Reports",
        ("analyst", "finance", "it", "auditor"),
        ("/api/reports",),
    ),
    Screen(
        "reports-store-days",
        "/reports/store-days",
        "Store day status",
        "Reports",
        ("analyst", "finance", "it", "auditor"),
        ("/api/reports",),
    ),
    Screen(
        "reports-tax",
        "/reports/tax",
        "Tax / VAT",
        "Reports",
        ("analyst", "finance", "it", "auditor"),
        ("/api/reports",),
    ),
    Screen(
        "reports-tax-corrections",
        "/reports/tax-corrections",
        "Tax corrections",
        "Reports",
        ("analyst", "finance", "it", "auditor"),
        ("/api/reports",),
    ),
    Screen(
        "reports-flash-totals",
        "/reports/flash-totals",
        "Flash Totals",
        "Reports",
        ("analyst", "finance", "it", "auditor"),
        ("/api/reports",),
    ),
    Screen(
        "reports-flash-sales-ytd",
        "/reports/flash-sales-ytd",
        "Flash Sales Year to Date",
        "Reports",
        ("analyst", "finance", "it", "auditor"),
        ("/api/reports",),
    ),
    Screen(
        "imports",
        "/imports",
        "Imports",
        "Integrations",
        ("it", "auditor"),
        ("/api/ingest", "/api/reconcile", "/api/batch/runs", "/api/batch/run"),
    ),
    Screen(
        "exports",
        "/exports",
        "Exports",
        "Integrations",
        ("finance", "it", "auditor"),
        ("/api/exports",),
    ),
    Screen(
        "diagnostics-health",
        "/diagnostics/health",
        "System health",
        "Diagnostics",
        ("finance", "it", "auditor"),
        ("/api/diagnostics",),
    ),
    Screen(
        "diagnostics-feeds",
        "/diagnostics/feeds",
        "Data feeds",
        "Diagnostics",
        ("finance", "it", "auditor"),
        ("/api/diagnostics",),
    ),
    Screen(
        "diagnostics-integrity",
        "/diagnostics/integrity",
        "Audit integrity",
        "Diagnostics",
        ("finance", "it", "auditor"),
        ("/api/diagnostics",),
    ),
    Screen(
        "diagnostics-activity",
        "/diagnostics/activity",
        "Audit activity",
        "Diagnostics",
        ("finance", "it", "auditor"),
        ("/api/diagnostics",),
    ),
    Screen(
        "activity",
        "/activity",
        "Agent Activity",
        "Diagnostics",
        ("finance", "it", "auditor"),
        ("/api/audit", "/api/agent/config", "/api/me/saved-views"),
    ),
    Screen("settings", "/settings", "General", "Configuration", EVERYONE),
    Screen(
        "settings-exchange",
        "/settings/exchange",
        "Data Exchange",
        "Configuration",
        ("analyst", "finance", "it", "auditor"),
    ),
    Screen(
        "settings-integrations",
        "/settings/integrations",
        "Integrations & readiness",
        "Configuration",
        ("finance", "it", "auditor"),
        (
            "/api/ingest/profiles",
            "/api/ingest/shopify",
            "/api/ontology/integration",
            "/api/production-readiness",
            "/api/reconciliation/policy",
            "/api/system-options",
            "/api/vouchers/options",
            "/api/vouchers/policy-package",
            "/api/vouchers/routing-policy",
            "/api/vouchers/retention",
        ),
    ),
    Screen(
        "settings-controls",
        "/settings/controls",
        "Totals, rules & GL mapping",
        "Configuration",
        ("analyst", "finance", "it", "auditor"),
        (
            "/api/total-definitions",
            "/api/audit-rules",
            "/api/gl",
            "/api/transaction-audit-rules",
        ),
    ),
    Screen(
        "settings-close",
        "/settings/close",
        "Tolerances, store-day order and period close",
        "Configuration",
        ("finance", "it", "auditor"),
        (
            "/api/variance-tolerances",
            "/api/periods",
            "/api/system-options/store-days-worked-in-order",
        ),
    ),
    Screen(
        "settings-tenders",
        "/settings/tenders",
        "Tender types",
        "Configuration",
        ("analyst", "finance", "it", "auditor"),
        ("/api/tender-types",),
    ),
    Screen(
        "settings-retention",
        "/settings/retention",
        "Data retention",
        "Configuration",
        ("it", "auditor"),
        ("/api/operations",),
    ),
    Screen(
        "settings-agents",
        "/settings/agents",
        "Agents",
        "Configuration",
        ("analyst", "finance", "it", "auditor"),
        ("/api/agent/config", "/api/agent/settings", "/api/agent/ontology"),
    ),
    Screen(
        "settings-destinations",
        "/settings/destinations",
        "Export destinations",
        "Configuration",
        ("finance", "it", "auditor"),
        ("/api/exports/destinations",),
    ),
    Screen(
        "settings-access",
        "/settings/access",
        "Roles & access",
        "Configuration",
        EVERYONE,
        ("/api/access/matrix",),
        optional=(
            "/api/access/store-assignments",
            "/api/access/role-assignments",
            "/api/foundation/location-traits",
        ),
    ),
    Screen(
        "settings-admin",
        "/settings/admin",
        "Admin",
        "Configuration",
        (),
        ("/api/admin",),
    ),
)
# Screens an administrator has switched off for every other role. Admins are never locked out, and the pages
# that manage this setting (or every signed-in role's own preferences) can never be switched off.
HIDDEN_SCREENS: frozenset[str] = frozenset()
PROTECTED_SCREENS = frozenset({"my-preferences", "settings-admin", "settings-access"})


def set_hidden_screens(keys) -> None:
    global HIDDEN_SCREENS
    known = {s.key for s in SCREENS} - PROTECTED_SCREENS
    HIDDEN_SCREENS = frozenset(k for k in keys if k in known)


# Needed by every signed-in role to render the page chrome (freshness banner, identity).
OPEN_PATHS = {
    "/api/me",
    "/api/batch/status",
    "/api/access/matrix",
    "/api/stores",
    "/api/lookups/registers",
    "/api/lookups/cashiers",
}


def can_open(role: str, screen: Screen) -> bool:
    if role != "admin" and screen.key in HIDDEN_SCREENS:
        return False
    return (
        role == "admin"
        or role in screen.roles
        or (role == "sales_audit_manager" and "analyst" in screen.roles)
    )


def screens_for(role: str) -> list[dict]:
    return [
        {
            "key": s.key,
            "path": s.path,
            "label": s.label,
            "group": s.group,
            "allowed": can_open(role, s),
            "roles": [ROLE_LABELS[r] for r in ROLES if can_open(r, s)],
        }
        for s in SCREENS
    ]


def _owners(path: str) -> list[Screen]:
    """Screens that own the longest API prefix matching this path."""
    best, owners = "", []
    for s in SCREENS:
        for prefix in s.all_apis:
            if path == prefix or path.startswith(prefix + "/"):
                if len(prefix) > len(best):
                    best, owners = prefix, [s]
                elif len(prefix) == len(best) and s not in owners:
                    owners.append(s)
    return owners


def api_allowed(role: str, path: str) -> tuple[bool, list[str]]:
    """May this role call this API path? A path no screen owns is left to the endpoint's own role check."""
    if path in OPEN_PATHS:
        return True, []
    owners = _owners(path)
    if not owners:
        return True, []
    return any(can_open(role, s) for s in owners), [s.label for s in owners]


def matrix() -> dict:
    return {
        "roles": [{"key": r, "label": ROLE_LABELS[r]} for r in ROLES],
        "screens": [
            {
                "key": s.key,
                "path": s.path,
                "label": s.label,
                "group": s.group,
                "roles": [r for r in ROLES if can_open(r, s)],
            }
            for s in SCREENS
        ],
    }
