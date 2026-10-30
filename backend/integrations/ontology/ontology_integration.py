"""Read approved Nimbus audit rules from Ontology Studio's integration API.

The remote API is advisory to availability, never to safety: if it is not
configured, unavailable, or returns an incomplete approved release, Nimbus
continues with its built-in deterministic controls.
"""
import os
import asyncio
from datetime import datetime, timezone

import httpx


RULE_CODES = frozenset({
    "NSA-EVIDENCE-REQUIRED", "NSA-APPROVAL-BOUND", "NSA-VALIDATION-CLOSE",
    "NSA-FINANCIAL-DETERMINISM", "NSA-SOURCE-LINEAGE",
    "NSA-TRANSACTION-AMOUNT-SIGN", "NSA-TRANSACTION-CURRENCY", "NSA-SOURCE-UNIQUE",
    "NSA-POS-CONTROL-COMPLETE", "NSA-SETTLEMENT-MATCH", "NSA-REFUND-CONFIRMATION",
    "NSA-BANK-RECONCILIATION", "NSA-GL-RECONCILIATION", "NSA-STALE-EVIDENCE",
    "NSA-ACTION-IDEMPOTENCY", "NSA-CLOSE-ELIGIBILITY", "NSA-APPROVAL-AUTHORITY",
})


class OntologyIntegration:
    """An atomic, release-pinned view of the existing Ontology Studio workspace.

    Definitions are context, never executable code or a substitute for local gates.
    A failed refresh invalidates the active view, including revoked credentials.
    """
    SECTIONS = ("business-rules", "entities", "relationships", "metrics", "glossary", "workflows")

    def __init__(self):
        self._lock = asyncio.Lock()
        self.last_checked_at = None
        self.last_success_at = None
        self.last_error = None
        self.rule_codes = frozenset()
        self._snapshot = None

    @property
    def configured(self) -> bool:
        return bool(os.getenv("NIMBUS_ONTOLOGY_API_URL") and os.getenv("NIMBUS_ONTOLOGY_API_KEY"))

    def context(self) -> dict:
        from copy import deepcopy
        if not self.configured or self._snapshot is None:
            return {"source": "built-in-fallback", "ontology": None, "definitions": {}}
        return deepcopy(self._snapshot)

    def provenance(self) -> dict:
        context = self.context()
        return {"source": context["source"], "ontology": context["ontology"]}

    def status(self) -> dict:
        context = self.context()
        codes = self.rule_codes if context["ontology"] else frozenset()
        return {
            "configured": self.configured,
            "endpoint_configured": bool(os.getenv("NIMBUS_ONTOLOGY_API_URL")),
            "credential_configured": bool(os.getenv("NIMBUS_ONTOLOGY_API_KEY")),
            **self.provenance(),
            "last_checked_at": self.last_checked_at,
            "last_success_at": self.last_success_at,
            "last_error": self.last_error,
            "approved_rule_codes": sorted(codes),
            "expected_rule_codes": sorted(RULE_CODES),
            "missing_rule_codes": sorted(RULE_CODES - codes),
            "ready": bool(context["ontology"]) and RULE_CODES <= codes,
            "definition_counts": {k: len(v) for k, v in context["definitions"].items()},
        }

    @staticmethod
    def _release(payload):
        if not isinstance(payload, dict):
            raise ValueError("Expected ontology envelope")
        meta = payload.get("ontology")
        if (not isinstance(meta, dict) or meta.get("approved") is not True
                or not meta.get("workspaceId") or not meta.get("releaseId")
                or not meta.get("contentHash") or meta.get("releaseHash") != meta["contentHash"]):
            raise ValueError("An unchanged approved release is required")
        return meta

    async def refresh(self, client=None) -> frozenset[str]:
        async with self._lock:
            self.last_checked_at = datetime.now(timezone.utc)
            if not self.configured:
                self._snapshot, self.rule_codes = None, frozenset()
                missing = [name for name in ("NIMBUS_ONTOLOGY_API_URL", "NIMBUS_ONTOLOGY_API_KEY")
                           if not os.getenv(name)]
                self.last_error = "Missing configuration: " + ", ".join(missing)
                return self.rule_codes
            try:
                if client is None:
                    async with httpx.AsyncClient(timeout=10.0) as owned:
                        snapshot = await asyncio.wait_for(self._read(owned), timeout=30)
                else:
                    snapshot = await asyncio.wait_for(self._read(client), timeout=30)
                codes = frozenset(r.get("ruleCode") for r in snapshot["definitions"]["business-rules"]
                                  if r.get("ruleCode") in RULE_CODES)
                if not RULE_CODES <= codes:
                    raise ValueError("Approved release is missing required Sales Audit controls")
                self._snapshot, self.rule_codes = snapshot, codes
                self.last_success_at = datetime.now(timezone.utc)
                self.last_error = None
            except (httpx.HTTPError, ValueError, TypeError, KeyError, asyncio.TimeoutError) as exc:
                self._snapshot, self.rule_codes = None, frozenset()
                self.last_error = f"Ontology context refresh failed: {exc.__class__.__name__}"
            return self.rule_codes

    async def _read(self, client):
        base = os.environ["NIMBUS_ONTOLOGY_API_URL"].rstrip("/")
        headers = {"Authorization": f"Bearer {os.environ['NIMBUS_ONTOLOGY_API_KEY']}"}
        release, sections = None, {}
        for section in self.SECTIONS:
            rows, cursor, seen = [], None, set()
            for _ in range(100):
                params = {"limit": 100, "requireApproved": "true"}
                if release:
                    params["releaseId"] = release["releaseId"]
                if cursor:
                    params["cursor"] = cursor
                response = await client.get(f"{base}/{section}", headers=headers, params=params)
                response.raise_for_status()
                payload = response.json()
                meta = self._release(payload)
                if release and any(meta.get(k) != release.get(k) for k in
                                   ("workspaceId", "releaseId", "contentHash", "releaseHash", "version")):
                    raise ValueError("Ontology release changed during retrieval")
                release = release or meta
                page = payload.get("data")
                if not isinstance(page, list) or any(not isinstance(r, dict) for r in page):
                    raise ValueError("Expected definition list")
                rows.extend(page)
                pagination = payload.get("pagination", {})
                if not isinstance(pagination, dict):
                    raise ValueError("Invalid pagination")
                cursor = pagination.get("nextCursor")
                if cursor is None:
                    break
                if not isinstance(cursor, str) or not cursor or cursor in seen:
                    raise ValueError("Invalid pagination cursor")
                seen.add(cursor)
            else:
                raise ValueError("Ontology pagination limit exceeded")
            sections[section] = rows
        return {"source": "ontology-integration", "ontology": release, "definitions": sections}


integration = OntologyIntegration()
