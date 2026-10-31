"""Database invariant for the single active reconciliation policy."""

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def test_policy_migration_keeps_latest_active_and_enforces_singleton():
    migration_path = Path(__file__).resolve().parents[3] / "alembic/versions/0047_single_active_reconciliation_policy.py"
    spec = importlib.util.spec_from_file_location("single_active_policy_migration", migration_path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(text(
                "CREATE TABLE reconciliation_policies ("
                "id VARCHAR(36) PRIMARY KEY, active BOOLEAN NOT NULL, updated_at DATETIME NOT NULL)"
            ))
            connection.execute(text(
                "INSERT INTO reconciliation_policies (id, active, updated_at) VALUES "
                "('older', 1, '2026-01-01'), ('newer', 1, '2026-02-01'), ('inactive', 0, '2026-03-01')"
            ))
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()

            active_ids = connection.execute(text(
                "SELECT id FROM reconciliation_policies WHERE active = 1"
            )).scalars().all()
            assert active_ids == ["newer"]
            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(text(
                        "INSERT INTO reconciliation_policies (id, active, updated_at) "
                        "VALUES ('another', 1, '2026-04-01')"
                    ))
            assert "uq_reconciliation_policies_one_active" in {
                index["name"] for index in inspect(connection).get_indexes("reconciliation_policies")
            }
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
            assert "uq_reconciliation_policies_one_active" not in {
                index["name"] for index in inspect(connection).get_indexes("reconciliation_policies")
            }
    finally:
        engine.dispose()
