"""Walk one store day through the rendered UI in a real browser, signed in through Ontology Studio.

Needs (all local and disposable): the Nimbus API and web app running against a migrated PostgreSQL database,
Ontology Studio running, and Playwright (`pip install playwright && playwright install chromium`).
Credentials come from the environment only:

    ONTOLOGY_USER=... ONTOLOGY_PASSWORD=... python scripts/ui_walkthrough.py

Environment: STUDIO_URL (default http://localhost:3050), WEB_URL (http://localhost:3000),
API_URL (http://localhost:8010), SHOTS (directory for screenshots), PSQL_CMD (a command that runs SQL on the
disposable database from stdin; used only to stand in for a second person where the app demands one).
"""

import os
import subprocess
import sys
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

STUDIO = os.environ.get("STUDIO_URL", "http://localhost:3050")
WEB = os.environ.get("WEB_URL", "http://localhost:3000")
API = os.environ.get("API_URL", "http://localhost:8010")
SHOTS = Path(os.environ.get("SHOTS", "ui-shots"))
STORE, DAY = "S1", "2026-10-01"
PATH = f"/api/store-days/{STORE}/{DAY}"


def sql(statement: str) -> None:
    command = os.environ["PSQL_CMD"]
    subprocess.run(command, shell=True, input=statement.encode(), check=True, capture_output=True)


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
    user, password = os.environ.get("ONTOLOGY_USER"), os.environ.get("ONTOLOGY_PASSWORD")
    if not user or not password:
        print("Set ONTOLOGY_USER and ONTOLOGY_PASSWORD.", file=sys.stderr)
        return 2
    SHOTS.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []

    def check(label: str, condition: bool, detail: object = "") -> None:
        print(("PASS " if condition else "FAIL ") + label + ("" if condition else f"  -> {detail}"))
        if not condition:
            failures.append(label)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        console_errors: list[str] = []
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(str(e)))

        # Sign in through Ontology Studio's own form; the session cookie is shared with the app.
        page.goto(STUDIO, wait_until="networkidle")
        page.get_by_placeholder("Enter username or corporate email").fill(user)
        page.get_by_placeholder("Enter password").fill(password)
        page.get_by_role("button", name="Sign In to Studio").click()
        page.get_by_text("Welcome").first.wait_for(
            timeout=20000
        )  # the session cookie is set by then
        me = context.request.get(f"{API}/api/me")
        check("signed in through Ontology Studio", me.ok, me.text()[:200])
        role = me.json().get("role") if me.ok else None
        check("the session maps to a Nimbus role", role in {"admin", "finance", "it"}, role)

        def api(method: str, path: str, **kwargs):
            return context.request.fetch(f"{API}{path}", method=method, **kwargs)

        # Seed the day through the API: an export destination, three deliveries, reconciliation, an open day.
        check(
            "destination",
            api("POST", "/api/exports/destinations", data={"name": "ERP / GL", "kind": "file"}).ok,
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
            response = api(
                "POST",
                "/api/ingest",
                data={"source_system": source, "delivery_id": delivery, "records": records},
            )
            check(f"import {source}", response.ok, response.text()[:200])
        check("reconcile", api("POST", f"/api/reconcile?business_date={DAY}").ok)
        check(
            "open the day",
            api("POST", "/api/store-days", data={"store_id": STORE, "business_date": DAY}).ok,
        )
        check(
            "cash declared",
            api(
                "POST",
                "/api/cash-controls",
                data={
                    "store_id": STORE,
                    "business_date": DAY,
                    "register_id": "R1",
                    "tender_type": "Cash",
                    "declared_cash": "30.00",
                },
            ).ok,
        )

        # The UI: the day opens, and a re-total warns that Over/Short has nothing to balance against.
        page.goto(f"{WEB}/store-days/{STORE}/{DAY}", wait_until="networkidle")
        expect(page.get_by_text(f"{STORE} · {DAY}")).to_be_visible(timeout=20000)
        page.screenshot(path=str(SHOTS / "01-store-day-opened.png"))
        page.get_by_role("button", name="Re-total", exact=True).first.click()
        warned = page.get_by_text("Over/Short has no Accounted For total")
        expect(warned).to_be_visible(timeout=20000)
        check("the re-total warns that Over/Short cannot balance", True)
        page.screenshot(path=str(SHOTS / "02-retotal-warning.png"))

        # Finance configures what the store can account for. One person cannot approve their own change,
        # so a second person is recorded on the disposable database before the admin approves it.
        draft = api(
            "POST",
            "/api/total-definitions",
            data={
                "name": "Cash Accounted For",
                "category": "Over/Short",
                "over_short_group": "Accounted For",
                "over_short_operator": "+",
                "source_system": "POS",
                "transaction_types": ["Sale", "Return"],
                "tender_type": "Cash",
            },
        )
        check("draft the Accounted For total", draft.ok, draft.text()[:200])
        definition_id = draft.json()["id"]
        fingerprint = api("POST", f"/api/total-definitions/{definition_id}/impact").json()[
            "fingerprint"
        ]
        activation = {"impact_fingerprint": fingerprint}
        check(
            "submit it",
            api("POST", f"/api/total-definitions/{definition_id}/submit", data=activation).ok,
        )
        sql(
            "update audit_total_definitions set submitted_by = 'other.author@example.test', "
            f"updated_by = 'other.author@example.test' where id = '{definition_id}';"
        )
        approval = api("POST", f"/api/total-definitions/{definition_id}/activate", data=activation)
        check("approve it", approval.ok, f"{approval.status} {approval.text()[:300]}")

        def as_role(role: str | None) -> None:
            """Use the app's own role switcher (an administrator acting as another role)."""
            script = (
                f"localStorage.setItem('nimbus_impersonate', '{role}')"
                if role
                else "localStorage.removeItem('nimbus_impersonate')"
            )
            page.evaluate(script)
            page.goto(f"{WEB}/store-days/{STORE}/{DAY}", wait_until="networkidle")
            expect(page.get_by_text(f"{STORE} · {DAY}")).to_be_visible(timeout=20000)

        # Re-total: now clean, with no warning.
        as_role(None)
        page.get_by_role("button", name="Re-total", exact=True).first.click()
        expect(page.get_by_text("Re-totaled 3 transaction(s)")).to_be_visible(timeout=20000)
        page.wait_for_timeout(1000)
        check(
            "no Over/Short warning once an Accounted For total exists",
            page.get_by_text("Over/Short has no Accounted For total").count() == 0,
        )
        expect(page.get_by_text("Clean").locator("visible=true").first).to_be_visible(timeout=20000)
        page.screenshot(path=str(SHOTS / "03-clean-totals.png"))

        # The auditor signs off.
        as_role("analyst")
        page.get_by_role("button", name="Sign audit…").click()
        page.get_by_placeholder("Summarize the checks and evidence reviewed").fill(
            "Totals match the control total and ledger"
        )
        page.get_by_role("button", name="Sign off audit").click()
        expect(
            page.get_by_text("Auditor sign-off recorded for the current totals version.")
        ).to_be_visible(timeout=20000)
        page.screenshot(path=str(SHOTS / "04-signed-off.png"))

        # IT requests closure with evidence.
        as_role("it")
        page.get_by_role("button", name="Request closure…").click()
        page.get_by_label("Evidence basis").fill(
            "Totals reviewed against the control total and ledger"
        )
        page.get_by_role("button", name="Request closure", exact=True).click()
        expect(page.get_by_text("Approve & close")).to_be_visible(timeout=20000)
        sql("update store_day_closure_requests set requested_by = 'other.requester@example.test';")
        page.screenshot(path=str(SHOTS / "05-closure-requested.png"))

        # Finance approves and the day closes.
        as_role("finance")
        page.get_by_role("button", name="Approve & close").click()
        expect(page.get_by_role("button", name="Reopen…")).to_be_visible(timeout=20000)
        check("the day is closed in the UI", True)
        page.screenshot(path=str(SHOTS / "06-closed.png"))

        # Export, then acknowledge, and the day reads Final.
        destination_id = api("GET", "/api/exports/destinations").json()[0]["id"]
        for dataset in ("audited_totals", "audited_transactions"):
            run = api(
                "POST",
                "/api/exports/run",
                data={"destination_id": destination_id, "dataset": dataset},
            )
            check(
                f"export {dataset}",
                run.ok and run.json().get("status") in ("Sent", "Acknowledged"),
                run.text()[:200],
            )
        batches = api("GET", "/api/exports/batches").json()
        for batch in batches if isinstance(batches, list) else batches.get("items", []):
            if batch.get("status") == "Sent":
                check(
                    "acknowledge " + batch["dataset"],
                    api(
                        "POST",
                        f"/api/exports/batches/{batch['id']}/ack",
                        data={"reference": "ERP-1"},
                    ).ok,
                )
        as_role(None)
        expect(page.get_by_text("Final").locator("visible=true").first).to_be_visible(timeout=20000)
        check("the day reads Final in the UI", True)
        page.screenshot(path=str(SHOTS / "07-final.png"))

        # The other screens render with this data and no errors.
        for route, heading in (
            ("/store-days", "Store Days"),
            ("/exports", "Exports"),
            ("/cash-office", "Cash Office"),
            ("/audit-errors", "Audit Errors"),
            ("/reports/variances", "Total variances"),
            ("/settings/controls", "Totals, rules"),
        ):
            page.goto(f"{WEB}{route}", wait_until="networkidle")
            check(
                f"{route} renders",
                page.get_by_text(heading).locator("visible=true").count() > 0,
                page.url,
            )
            page.screenshot(path=str(SHOTS / f"page{route.replace('/', '-')}.png"))

        check("no console or page errors", not console_errors, console_errors[:3])
        browser.close()
    print(f"\n{len(failures)} failure(s)" if failures else "\nAll checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
