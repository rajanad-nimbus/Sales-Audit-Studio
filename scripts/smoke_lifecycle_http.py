"""Walk one store day through the running app over HTTP, on a real PostgreSQL database.

Usage (a disposable database only; it creates data):
    DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/db backend/.venv/bin/python scripts/smoke_lifecycle_http.py

It applies the Alembic migrations, starts a stub identity server standing in for Ontology Studio, starts the
real app under uvicorn, and drives import, audit, total, sign-off, closure, export and acknowledgement as
different roles. Unlike the unit tests it exercises real routing, authentication, role checks, middleware,
serialization and the migrated schema (triggers included).
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
STORE, DAY = "S1", "2026-10-01"
ROLES = {
    "it": "it.operator@example.test",
    "analyst": "sales.auditor@example.test",
    "finance": "finance.lead@example.test",
    "finance2": "finance.approver@example.test",
    "viewer": "viewer@example.test",
    "admin": "admin.owner@example.test",
    "newhire": "new.hire@example.test",
    "store_manager": "manager.s1@example.test",
    "store_staff": "staff.s1@example.test",
}
# Stores Ontology Studio would assign to the store roles (everyone else is unscoped).
# What Ontology Studio itself calls each person: only its coarse roles exist there.
STUDIO_ROLE = {"newhire": "viewer", "admin": "admin"}
ASSIGNED = {"store_manager": ["S1"], "store_staff": ["S1"]}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Identity(BaseHTTPRequestHandler):
    """Stands in for Ontology Studio: the bearer token names the person."""

    def do_GET(self):  # noqa: N802
        token = (self.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if token not in ROLES:
            self.send_response(401)
            self.end_headers()
            return
        role = STUDIO_ROLE.get(token) or ("finance" if token.startswith("finance") else token)
        user = {"id": token, "email": ROLES[token], "role": role}
        if token in ASSIGNED:
            user["stores"] = ASSIGNED[token]
        body = json.dumps({"user": user}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def sale(record_id: str, amount: str) -> dict:
    return {
        "source_record_id": record_id,
        "record_type": "Sale",
        "event_time": f"{DAY}T10:00:00Z",
        "store_id": STORE,
        "register_id": "R1",
        "tender_type": "Cash",
        "amount": amount,
        "currency": "USD",
        "lines": [
            {
                "item_code": "SKU-1",
                "description": "Shirt",
                "quantity": "1",
                "unit_price": amount,
                "gross_amount": amount,
                "tax_rate": "0",
                "tax_amount": "0",
            }
        ],
        "tenders": [{"tender_type": "Cash", "amount": amount}],
    }


def main() -> int:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url or "postgresql" not in database_url:
        print("Set DATABASE_URL to a disposable PostgreSQL database.", file=sys.stderr)
        return 2
    identity_port, app_port = free_port(), free_port()
    identity = HTTPServer(("127.0.0.1", identity_port), Identity)
    threading.Thread(target=identity.serve_forever, daemon=True).start()
    export_dir = tempfile.mkdtemp(prefix="zetsa-smoke-exports-")
    env = {
        **os.environ,
        "DATABASE_URL": database_url,
        "NIMBUS_ONTOLOGY_AUTH_URL": f"http://127.0.0.1:{identity_port}/me",
        "NIMBUS_EXPORT_DIR": export_dir,
    }
    python = str(BACKEND / ".venv" / "bin" / "python")
    subprocess.run([python, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True)
    server = subprocess.Popen(
        [python, "-m", "uvicorn", "main:app", "--port", str(app_port), "--log-level", "warning"],
        cwd=BACKEND,
        env=env,
    )
    base = f"http://127.0.0.1:{app_port}"
    failures: list[str] = []

    def check(label: str, condition: bool, detail: object = "") -> None:
        print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"  -> {detail}"))
        if not condition:
            failures.append(label)

    try:
        for _ in range(60):
            try:
                if httpx.get(f"{base}/api/health", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(1)
        else:
            print("The app did not start.", file=sys.stderr)
            return 1

        def call(role, method, path, **kwargs):
            headers = {"Authorization": f"Bearer {role}"} if role else {}
            return httpx.request(method, base + path, headers=headers, timeout=30, **kwargs)

        check("health is public", call(None, "GET", "/api/health").status_code == 200)
        check("no token is refused", call(None, "GET", "/api/me").status_code == 401)
        check("bad token is refused", call("nobody", "GET", "/api/me").status_code == 401)
        me = call("it", "GET", "/api/me")
        check(
            "identity maps to the IT role",
            me.status_code == 200 and me.json()["role"] == "it",
            me.text,
        )

        # The handover with Ontology Studio: Studio says who someone is; Nimbus gives them a business role.
        hire = call("newhire", "GET", "/api/me")
        check(
            "a new hire is a plain viewer at first",
            hire.status_code == 200
            and hire.json()["role"] == "viewer"
            and hire.json()["role_source"] == "identity",
            hire.text,
        )
        check(
            "and cannot open store days",
            call("newhire", "GET", "/api/store-days").status_code == 403,
        )
        assign = {
            "assignment_identifier": "newhire",
            "role": "finance",
            "reason": "Covers the finance desk",
        }
        check(
            "only an administrator can assign roles",
            call("it", "PUT", "/api/access/role-assignments", json=assign).status_code == 403,
        )
        check(
            "a role must carry a real reason",
            call(
                "admin", "PUT", "/api/access/role-assignments", json={**assign, "reason": "short"}
            ).status_code
            == 422,
        )
        check(
            "administrator cannot be assigned as a role",
            call(
                "admin", "PUT", "/api/access/role-assignments", json={**assign, "role": "admin"}
            ).status_code
            == 422,
        )
        ghost = call(
            "admin",
            "PUT",
            "/api/access/role-assignments",
            json={**assign, "assignment_identifier": "never-signed-in"},
        )
        check(
            "someone who never signed in cannot be assigned", ghost.status_code == 422, ghost.text
        )
        listed = call("admin", "GET", "/api/access/role-assignments", params={"q": "hire"})
        names = (
            [row["assignment_identifier"] for row in listed.json().get("items", [])]
            if listed.status_code == 200
            else []
        )
        check(
            "the new hire appears in the directory after signing in",
            names == ["newhire"],
            listed.text[:200],
        )
        granted = call("admin", "PUT", "/api/access/role-assignments", json=assign)
        check(
            "an administrator assigns the Finance role",
            granted.status_code == 200 and granted.json()["assigned_role"] == "finance",
            granted.text,
        )
        acting = call("newhire", "GET", "/api/me").json()
        check(
            "the new hire now acts as Finance, assigned here",
            (acting["role"], acting["role_source"], acting["real_role"])
            == ("finance", "assignment", "viewer"),
            acting,
        )
        check(
            "and can open store days", call("newhire", "GET", "/api/store-days").status_code == 200
        )
        check(
            "an administrator is never reassigned",
            call("admin", "GET", "/api/me").json()["role_source"] == "identity",
        )
        removed = call(
            "admin",
            "DELETE",
            "/api/access/role-assignments/newhire",
            params={"reason": "Moved to another team"},
        )
        check("removing the assignment succeeds", removed.status_code == 200, removed.text)
        check(
            "and the new hire is a viewer again",
            call("newhire", "GET", "/api/store-days").status_code == 403,
        )

        destination = call(
            "it", "POST", "/api/exports/destinations", json={"name": "ERP / GL", "kind": "file"}
        )
        check(
            "IT creates a file destination", destination.status_code in (200, 201), destination.text
        )
        destination_id = destination.json().get("id")

        check(
            "a viewer cannot import",
            call(
                "viewer",
                "POST",
                "/api/ingest",
                json={"source_system": "POS", "delivery_id": "x", "records": []},
            ).status_code
            == 403,
        )
        for source, delivery, records in (
            ("POS", "pos-1", [sale("t1", "10.00"), sale("t2", "20.00")]),
            (
                "POSControl",
                "control-1",
                [
                    {
                        "source_record_id": "control",
                        "store_id": STORE,
                        "business_date": DAY,
                        "declared_count": 2,
                        "declared_total": "30.00",
                    }
                ],
            ),
            (
                "ERP",
                "gl-1",
                [
                    {
                        "source_record_id": "gl-posting",
                        "record_type": "Posting",
                        "event_time": f"{DAY}T23:00:00Z",
                        "store_id": STORE,
                        "amount": "30.00",
                        "currency": "USD",
                    }
                ],
            ),
        ):
            response = call(
                "it",
                "POST",
                "/api/ingest",
                json={"source_system": source, "delivery_id": delivery, "records": records},
            )
            check(f"IT imports {source}", response.status_code == 200, response.text)
        rerun = call(
            "it",
            "POST",
            "/api/ingest",
            json={
                "source_system": "POS",
                "delivery_id": "pos-1",
                "records": [sale("t1", "10.00"), sale("t2", "20.00")],
            },
        )
        check(
            "a repeated delivery adds nothing",
            rerun.status_code == 200 and rerun.json().get("canonical_created", 0) == 0,
            rerun.text,
        )

        reconcile = call("it", "POST", "/api/reconcile", params={"business_date": DAY})
        check("reconciliation runs", reconcile.status_code == 200, reconcile.text)
        opened = call(
            "it", "POST", "/api/store-days", json={"store_id": STORE, "business_date": DAY}
        )
        check("IT opens the store day", opened.status_code in (200, 201), opened.text)
        path = f"/api/store-days/{STORE}/{DAY}"
        # Over/Short compares what the store is accountable for with what it can account for. The
        # installed totals define the first side only, so Finance configures the second, as a governed change.
        created = call(
            "finance",
            "POST",
            "/api/total-definitions",
            json={
                "name": "Cash Accounted For",
                "category": "Over/Short",
                "over_short_group": "Accounted For",
                "over_short_operator": "+",
                "source_system": "POS",
                "transaction_types": ["Sale", "Return"],
                "tender_type": "Cash",
            },
        )
        check(
            "Finance drafts an Accounted For total", created.status_code in (200, 201), created.text
        )
        definition_id = created.json().get("id")
        impact = call("finance", "POST", f"/api/total-definitions/{definition_id}/impact")
        check("its impact is previewed", impact.status_code == 200, impact.text)
        activation = {"impact_fingerprint": impact.json().get("fingerprint")}
        submitted = call(
            "finance", "POST", f"/api/total-definitions/{definition_id}/submit", json=activation
        )
        check("it is submitted for approval", submitted.status_code == 200, submitted.text)
        self_approval = call(
            "finance", "POST", f"/api/total-definitions/{definition_id}/activate", json=activation
        )
        check(
            "its author cannot approve it",
            self_approval.status_code in (403, 409),
            self_approval.text,
        )
        approved = call(
            "finance2", "POST", f"/api/total-definitions/{definition_id}/activate", json=activation
        )
        check("a second Finance user activates it", approved.status_code == 200, approved.text)

        # The cash office counts the till and banks the cash; the standard Over/Short total needs it.
        declared = call(
            "finance",
            "POST",
            "/api/cash-controls",
            json={
                "store_id": STORE,
                "business_date": DAY,
                "register_id": "R1",
                "tender_type": "Cash",
                "declared_cash": "30.00",
            },
        )
        check(
            "the cash office declares the till", declared.status_code in (200, 201), declared.text
        )
        deposit = call(
            "finance",
            "POST",
            "/api/cash-controls/deposits",
            json={
                "store_id": STORE,
                "business_date": DAY,
                "deposit_reference": "DEP-1",
                "deposited_amount": "30.00",
            },
        )
        check("the bank deposit is recorded", deposit.status_code in (200, 201), deposit.text)
        check(
            "an analyst cannot re-total",
            call("viewer", "POST", f"{path}/retotal").status_code == 403,
        )
        retotal = call("finance", "POST", f"{path}/retotal")
        check("Finance re-totals", retotal.status_code == 200, retotal.text)

        detail = call("finance", "GET", path).json()
        readiness = detail["readiness"]
        check("ingest is complete", readiness["ingest"]["state"] == "Complete", readiness["ingest"])
        check(
            "audit is clean and unsigned",
            readiness["audit"]["state"] == "Clean" and not readiness["audit"]["signed_off"],
            readiness["audit"],
        )

        early = call("it", "POST", f"{path}/closure-requests", json={"evidence_basis": "Reviewed"})
        check("closure cannot be requested before sign-off", early.status_code == 422, early.text)
        signed = call(
            "analyst",
            "POST",
            f"{path}/audit-signoff",
            json={"rationale": "Totals match the control total"},
        )
        check(
            "the auditor signs off",
            signed.status_code == 200 and signed.json()["signed_off"],
            signed.text,
        )
        requested = call(
            "it", "POST", f"{path}/closure-requests", json={"evidence_basis": "Totals reviewed"}
        )
        check("IT requests closure", requested.status_code in (200, 201), requested.text)
        same = call("it", "POST", f"{path}/close")
        check(
            "the requester cannot approve their own closure",
            same.status_code in (401, 403),
            same.text,
        )
        closed = call("finance2", "POST", f"{path}/close")
        check(
            "a second Finance user closes the day",
            closed.status_code == 200 and closed.json()["status"] == "Closed",
            closed.text,
        )

        for dataset in ("audited_totals", "audited_transactions"):
            run = call(
                "it",
                "POST",
                "/api/exports/run",
                json={"destination_id": destination_id, "dataset": dataset},
            )
            check(
                f"IT exports {dataset}",
                run.status_code == 200 and run.json().get("status") in ("Sent", "Acknowledged"),
                run.text,
            )
        batches = call("it", "GET", "/api/exports/batches")
        rows = (
            batches.json() if isinstance(batches.json(), list) else batches.json().get("items", [])
        )
        check("two batches exist", len(rows) == 2, batches.text[:300])
        for batch in rows:
            if batch.get("status") == "Sent":
                ack = call(
                    "it",
                    "POST",
                    f"/api/exports/batches/{batch['id']}/ack",
                    json={"reference": "ERP-1"},
                )
                check(f"acknowledge {batch['dataset']}", ack.status_code == 200, ack.text)
        final = call("finance", "GET", path).json()["readiness"]["export"]["state"]
        check("the day is Final", final == "Final", final)
        check("export files were written", any(Path(export_dir).iterdir()))
        # Store roles see only their assigned stores: a second store exists, and neither role may reach it.
        master = call(
            "finance",
            "POST",
            "/api/foundation",
            json={"category": "Store", "code": "S2", "name": "Store 2", "active": True},
        )
        check(
            "Finance adds the second store to the master",
            master.status_code in (200, 201),
            master.text,
        )
        other = call("it", "POST", "/api/store-days", json={"store_id": "S2", "business_date": DAY})
        check("IT opens a second store's day", other.status_code in (200, 201), other.text)
        other_path = f"/api/store-days/S2/{DAY}"
        check(
            "a store manager reads their own store day",
            call("store_manager", "GET", path).status_code == 200,
        )
        refused = call("store_manager", "GET", other_path)
        check(
            "a store manager is refused another store's day",
            refused.status_code == 403,
            refused.text,
        )
        listed = call("store_manager", "GET", "/api/store-days")
        seen = {row["store_id"] for row in listed.json()} if listed.status_code == 200 else None
        check("the store manager's list holds only their store", seen == {"S1"}, seen)
        no_records = {"source_system": "POS", "delivery_id": "m", "records": []}
        check(
            "a store manager cannot import",
            call("store_manager", "POST", "/api/ingest", json=no_records).status_code == 403,
        )
        check(
            "a store manager cannot re-total",
            call("store_manager", "POST", f"{path}/retotal").status_code == 403,
        )
        run = {"destination_id": destination_id, "dataset": "audited_totals"}
        check(
            "a store manager cannot export",
            call("store_manager", "POST", "/api/exports/run", json=run).status_code == 403,
        )
        check(
            "store staff read their own store day",
            call("store_staff", "GET", path).status_code == 200,
        )
        check(
            "store staff are refused another store's day",
            call("store_staff", "GET", other_path).status_code == 403,
        )
        declare = {"store_id": STORE, "business_date": DAY, "declared_cash": "1"}
        check(
            "store staff cannot declare cash",
            call("store_staff", "POST", "/api/cash-controls", json=declare).status_code == 403,
        )
        check(
            "store staff cannot read cases",
            call("store_staff", "GET", "/api/cases").status_code == 403,
        )

        reopen = call(
            "finance", "POST", f"{path}/reopen", json={"rationale": "Found another error"}
        )
        check("a Final day cannot be reopened", reopen.status_code == 409, reopen.text)
    finally:
        server.terminate()
        server.wait(timeout=15)
        identity.shutdown()
    print(f"\n{len(failures)} failure(s)" if failures else "\nAll checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
