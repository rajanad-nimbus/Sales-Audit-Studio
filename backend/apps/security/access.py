"""Screen access: the one place that says which role may open which screen, and which API areas back each screen.

The UI reads this (through /api/me) to build navigation and guard routes. The API reads it too, in the
authentication middleware, so a role that cannot open a screen cannot call that screen's data either. Hiding a menu item
is never the control; this is.
"""
from dataclasses import dataclass

ROLES = ("admin", "finance", "it", "analyst", "auditor", "store_manager", "viewer")
ROLE_LABELS = {"admin": "Administrator", "finance": "Finance Approver", "it": "IT Support Admin", "analyst": "Sales Auditor",
               "auditor": "Internal Auditor", "store_manager": "Store Manager", "viewer": "Viewer"}
EVERYONE = ROLES


@dataclass(frozen=True)
class Screen:
    key: str
    path: str
    label: str
    group: str
    roles: tuple[str, ...]          # admin is always allowed, so it is not repeated here
    apis: tuple[str, ...] = ()      # API path prefixes whose data this screen needs
    optional: tuple[str, ...] = ()  # extra calls the screen tolerates being refused (a feature that only some roles see)

    @property
    def all_apis(self) -> tuple[str, ...]:
        return self.apis + self.optional


SCREENS: tuple[Screen, ...] = (
    Screen("command-center", "/", "Command Center", "Workspace", ("analyst", "finance", "it", "auditor"),
           ("/api/action-center", "/api/connectors", "/api/workflows", "/api/validations", "/api/decisions", "/api/policy-evaluations")),
    Screen("actions", "/actions", "Action Center", "Workspace", ("analyst", "finance", "auditor"), ("/api/action-center",),
           optional=("/api/agent/eligible",)),   # one-click agent approvals are Finance only; the page copes without them
    Screen("cases", "/cases", "Cases", "Workspace", ("analyst", "finance", "auditor"),
           ("/api/cases", "/api/exceptions", "/api/findings", "/api/recommendations", "/api/workflows", "/api/validations",
            "/api/policy-evaluations", "/api/decisions", "/api/reconciliation", "/api/action-center")),
    Screen("activity", "/activity", "Agent Activity", "Workspace", ("analyst", "finance", "it", "auditor"), ("/api/audit", "/api/agent/config")),
    Screen("store-days", "/store-days", "Store Days & Totals", "Data & Control", ("analyst", "finance", "store_manager", "auditor"), ("/api/store-days",)),
    Screen("transactions", "/transactions", "Transactions", "Data & Control", ("analyst", "finance", "it", "auditor"), ("/api/transactions",)),
    Screen("cash-office", "/cash-office", "Cash Office", "Data & Control", ("analyst", "finance", "store_manager", "auditor"), ("/api/cash-controls", "/api/cash")),
    Screen("corrections", "/corrections", "Transaction Corrections", "Data & Control", ("analyst", "finance", "auditor"), ("/api/transaction-adjustments",)),
    Screen("pipeline", "/pipeline", "Data Pipeline", "Data & Control", ("analyst", "it", "auditor"), ("/api/ingest", "/api/reconcile", "/api/batch/runs", "/api/batch/run")),
    Screen("exports", "/exports", "Exports", "Data & Control", ("finance", "it", "auditor"), ("/api/exports",)),
    Screen("audit", "/audit", "Audit Trail", "Data & Control", ("analyst", "finance", "it", "auditor"), ("/api/audit",)),
    # Configuration. General (appearance, account) is personal, so everyone has it and it needs no data from the API.
    Screen("settings", "/settings", "General", "Configuration", EVERYONE),
    Screen("settings-integrations", "/settings/integrations", "Integrations & readiness", "Configuration", ("analyst", "finance", "it", "auditor"),
           ("/api/ingest/profiles", "/api/ingest/shopify", "/api/ontology/integration", "/api/production-readiness", "/api/reconciliation/policy")),
    Screen("settings-controls", "/settings/controls", "Controls & reference data", "Configuration", ("analyst", "finance", "auditor"),
           ("/api/foundation", "/api/total-definitions", "/api/audit-rules", "/api/gl")),
    Screen("settings-retention", "/settings/retention", "Data retention", "Configuration", ("it", "auditor"), ("/api/operations",)),
    Screen("settings-agents", "/settings/agents", "Agents", "Configuration", ("analyst", "finance", "it", "auditor"), ("/api/agent/config", "/api/agent/settings", "/api/agent/ontology")),
    Screen("settings-destinations", "/settings/destinations", "Export destinations", "Configuration", ("finance", "it", "auditor"), ("/api/exports/destinations",)),
    Screen("settings-access", "/settings/access", "Roles & access", "Configuration", EVERYONE, ("/api/access",)),
)
# Needed by every signed-in role to render the page chrome (freshness banner, identity).
OPEN_PATHS = {"/api/me", "/api/batch/status", "/api/access/matrix"}


def can_open(role: str, screen: Screen) -> bool:
    return role == "admin" or role in screen.roles


def screens_for(role: str) -> list[dict]:
    return [{"key": s.key, "path": s.path, "label": s.label, "group": s.group, "allowed": can_open(role, s),
             "roles": [ROLE_LABELS[r] for r in ROLES if can_open(r, s)]} for s in SCREENS]


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
    return {"roles": [{"key": r, "label": ROLE_LABELS[r]} for r in ROLES],
            "screens": [{"key": s.key, "path": s.path, "label": s.label, "group": s.group,
                         "roles": [r for r in ROLES if can_open(r, s)]} for s in SCREENS]}
