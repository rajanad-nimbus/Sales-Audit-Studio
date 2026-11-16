"""Voucher purges require a reviewed batch, a durable archive and permanent identity."""

import hashlib
import importlib.util
import json
from datetime import date, timedelta
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import HTTPException
from sqlalchemy import create_engine, func, select, text

from apps.operations.models import RetentionRun
from apps.vouchers import retention
from apps.vouchers.models import Voucher, VoucherIdentity
from apps.vouchers.retention import (
    VoucherRetentionBody,
    execute_voucher_retention,
    list_retired_vouchers,
    preview_voucher_retention,
)
from apps.vouchers.routes import (
    EscheatBody,
    IssueBody,
    RedeemBody,
    VoucherOptionsBody,
    escheat_vouchers,
    issue_voucher,
    redeem_voucher,
    save_voucher_options,
    today,
)
from apps.vouchers.tests.test_jurisdiction_routing import FINANCE, IT, run_db


async def configure(db, purge=30):
    await save_voucher_options(
        VoucherOptionsBody(
            options=[
                {
                    "tender_type": "Voucher",
                    "escheat_days": 0,
                    "purge_days": purge,
                }
            ]
        ),
        db,
        IT,
    )


async def settled(db, number="CLOSED", *, age=100, store="S1"):
    issued = (date.fromisoformat(today()) - timedelta(days=age)).isoformat()
    await issue_voucher(
        IssueBody(voucher_number=number, store_id=store, amount="25", issued_on=issued), db, FINANCE
    )
    await redeem_voucher(number, RedeemBody(store_id=store), db, FINANCE)


def test_archive_then_purge_preserves_liabilities_and_prevents_number_reuse(tmp_path, monkeypatch):
    monkeypatch.setenv("NIMBUS_ARCHIVE_DIR", str(tmp_path))

    async def scenario(db):
        await configure(db)
        await settled(db)
        await settled(db, "YOUNG", age=1)
        await issue_voucher(
            IssueBody(
                voucher_number="OPEN",
                store_id="S1",
                amount="25",
                issued_on="2020-01-01",
                expires_on="2020-01-02",
            ),
            db,
            FINANCE,
        )
        await issue_voucher(
            IssueBody(
                voucher_number="ESCHEATED", store_id="S2", amount="15", issued_on="2020-01-01"
            ),
            db,
            FINANCE,
        )
        await escheat_vouchers(EscheatBody(voucher_numbers=["ESCHEATED"]), db, FINANCE)
        preview = await preview_voucher_retention("voucher", db, IT)
        assert {row["voucher_number"] for row in preview["vouchers"]} == {"CLOSED", "ESCHEATED"}
        body = {"tender_type": "Voucher", "preview_token": preview["preview_token"]}
        archived = await execute_voucher_retention(VoucherRetentionBody(**body), db, IT)
        assert archived["archived_records"] == 2 and archived["purged_records"] == 0
        archive = next(tmp_path.glob("vouchers/*.jsonl"))
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == archived["archive_hash"]
        contents = [json.loads(line) for line in archive.read_text().splitlines()]
        assert {row["voucher_number"] for row in contents} == {"CLOSED", "ESCHEATED"}
        assert (
            next(row for row in contents if row["voucher_number"] == "ESCHEATED")[
                "escheat_routing_snapshot"
            ]["destination_type"]
            == "Manual"
        )
        assert await db.scalar(select(func.count()).select_from(Voucher)) == 4
        done = await execute_voucher_retention(
            VoucherRetentionBody(**body, confirm_purge=True), db, IT
        )
        assert done["purged_records"] == 2
        assert set((await db.scalars(select(Voucher.voucher_number))).all()) == {"OPEN", "YOUNG"}
        retired = await list_retired_vouchers(None, 100, db, FINANCE)
        assert {row["voucher_number"] for row in retired} == {"CLOSED", "ESCHEATED"}
        scoped = {**FINANCE, "store_scoped": True, "stores": ["S1"]}
        assert [
            row["voucher_number"] for row in await list_retired_vouchers(None, 100, db, scoped)
        ] == ["CLOSED"]
        with pytest.raises(HTTPException) as reuse:
            await issue_voucher(
                IssueBody(voucher_number="CLOSED", store_id="S1", amount="25"), db, FINANCE
            )
        assert reuse.value.status_code == 409
        with pytest.raises(HTTPException) as redeem:
            await redeem_voucher("CLOSED", RedeemBody(store_id="S1"), db, FINANCE)
        assert redeem.value.status_code == 410

    run_db(scenario)


def test_policy_changes_and_scoped_operators_cannot_purge_a_previous_preview(tmp_path, monkeypatch):
    monkeypatch.setenv("NIMBUS_ARCHIVE_DIR", str(tmp_path))

    async def scenario(db):
        await settled(db)
        with pytest.raises(HTTPException) as disabled:
            await preview_voucher_retention("Voucher", db, IT)
        assert disabled.value.status_code == 409
        await configure(db)
        preview = await preview_voucher_retention("Voucher", db, IT)
        body = VoucherRetentionBody(
            tender_type="Voucher", preview_token=preview["preview_token"], confirm_purge=True
        )
        with pytest.raises(HTTPException) as scoped:
            await execute_voucher_retention(
                body, db, {**IT, "store_scoped": True, "stores": ["S1"]}
            )
        assert scoped.value.status_code == 403
        await configure(db, 60)
        with pytest.raises(HTTPException) as changed:
            await execute_voucher_retention(body, db, IT)
        assert changed.value.status_code == 409
        assert list(tmp_path.rglob("*.jsonl")) == []
        assert await db.scalar(select(func.count()).select_from(Voucher)) == 1

    run_db(scenario)


def test_purge_failure_rolls_back_retired_identities_and_removes_uncommitted_archive(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("NIMBUS_ARCHIVE_DIR", str(tmp_path))

    async def fail(*args):
        raise RuntimeError("delete unavailable")

    monkeypatch.setattr(retention, "purge_retention_candidates", fail)

    async def scenario(db):
        await configure(db)
        await settled(db)
        preview = await preview_voucher_retention("Voucher", db, IT)
        with pytest.raises(RuntimeError, match="delete unavailable"):
            await execute_voucher_retention(
                VoucherRetentionBody(
                    tender_type="Voucher",
                    preview_token=preview["preview_token"],
                    confirm_purge=True,
                ),
                db,
                IT,
            )
        assert (await db.get(VoucherIdentity, "CLOSED")).retired_at is None
        assert await db.scalar(select(func.count()).select_from(Voucher)) == 1
        assert await db.scalar(select(func.count()).select_from(RetentionRun)) == 0
        assert list(tmp_path.rglob("*.jsonl")) == []

    run_db(scenario)


def test_archive_failure_never_deletes_a_voucher(tmp_path, monkeypatch):
    monkeypatch.setenv("NIMBUS_ARCHIVE_DIR", str(tmp_path))

    async def fail(*args):
        raise OSError("archive disk unavailable")

    monkeypatch.setattr(retention, "archive_retention_candidates", fail)

    async def scenario(db):
        await configure(db)
        await settled(db)
        preview = await preview_voucher_retention("Voucher", db, IT)
        with pytest.raises(OSError, match="archive disk unavailable"):
            await execute_voucher_retention(
                VoucherRetentionBody(
                    tender_type="Voucher",
                    preview_token=preview["preview_token"],
                    confirm_purge=True,
                ),
                db,
                IT,
            )
        assert (await db.get(VoucherIdentity, "CLOSED")).retired_at is None
        assert await db.scalar(select(func.count()).select_from(Voucher)) == 1

    run_db(scenario)


def test_only_the_archived_subset_is_purged_when_archive_batch_is_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("NIMBUS_ARCHIVE_DIR", str(tmp_path))
    original = retention.archive_retention_candidates

    async def partial(db, dataset, model, timestamp, ids):
        path, digest, archived_ids, _ = await original(db, dataset, model, timestamp, ids[:1])
        return path, digest, archived_ids, True

    monkeypatch.setattr(retention, "archive_retention_candidates", partial)

    async def scenario(db):
        await configure(db)
        await settled(db, "ONE")
        await settled(db, "TWO")
        preview = await preview_voucher_retention("Voucher", db, IT)
        done = await execute_voucher_retention(
            VoucherRetentionBody(
                tender_type="Voucher", preview_token=preview["preview_token"], confirm_purge=True
            ),
            db,
            IT,
        )
        assert done["archived_records"] == done["purged_records"] == 1
        assert done["more_eligible"] is True
        assert await db.scalar(select(func.count()).select_from(Voucher)) == 1
        assert len(await list_retired_vouchers(None, 100, db, FINANCE)) == 1

    run_db(scenario)


def test_older_settings_clients_preserve_purge_window_until_explicitly_cleared():
    from apps.vouchers.routes import list_voucher_options

    async def scenario(db):
        await configure(db, 60)
        await save_voucher_options(
            VoucherOptionsBody(options=[{"tender_type": "Voucher", "escheat_days": 30}]), db, IT
        )
        assert (await list_voucher_options(db, IT))[0]["purge_days"] == 60
        await configure(db, None)
        assert (await list_voucher_options(db, IT))[0]["purge_days"] is None

    run_db(scenario)


def test_unknown_commit_outcome_preserves_archive(tmp_path, monkeypatch):
    monkeypatch.setenv("NIMBUS_ARCHIVE_DIR", str(tmp_path))

    async def scenario(db):
        await configure(db)
        await settled(db)
        preview = await preview_voucher_retention("Voucher", db, IT)

        async def fail():
            raise OSError("commit response lost")

        monkeypatch.setattr(db, "commit", fail)
        with pytest.raises(OSError, match="commit response lost"):
            await execute_voucher_retention(
                VoucherRetentionBody(
                    tender_type="Voucher",
                    preview_token=preview["preview_token"],
                    confirm_purge=True,
                ),
                db,
                IT,
            )
        assert len(list(tmp_path.rglob("*.jsonl"))) == 1

    run_db(scenario)


def test_migration_reserves_existing_numbers_and_keeps_purging_disabled():
    path = Path(__file__).resolve().parents[3] / "alembic/versions/0112_voucher_purge_windows.py"
    spec = importlib.util.spec_from_file_location("voucher_purge_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text("CREATE TABLE voucher_options (tender_type VARCHAR(100) PRIMARY KEY)")
            )
            connection.execute(text("INSERT INTO voucher_options VALUES ('Voucher')"))
            connection.execute(text("CREATE TABLE retention_runs (id VARCHAR(36) PRIMARY KEY)"))
            connection.execute(
                text(
                    "CREATE TABLE vouchers (id VARCHAR(36), voucher_number VARCHAR(100), issued_store_id VARCHAR(50), tender_type VARCHAR(100), issued_on VARCHAR(10), amount NUMERIC, currency VARCHAR(3), created_at DATETIME)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO vouchers VALUES ('v', 'EXISTING', 'S1', 'Voucher', '2020-01-01', 25, 'USD', '2020-01-01 00:00:00')"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                assert (
                    connection.execute(
                        text("SELECT voucher_number FROM voucher_identities")
                    ).scalar_one()
                    == "EXISTING"
                )
                assert (
                    connection.execute(text("SELECT purge_days FROM voucher_options")).scalar_one()
                    is None
                )
                migration.downgrade()
                assert (
                    connection.execute(text("SELECT voucher_number FROM vouchers")).scalar_one()
                    == "EXISTING"
                )
    finally:
        engine.dispose()
