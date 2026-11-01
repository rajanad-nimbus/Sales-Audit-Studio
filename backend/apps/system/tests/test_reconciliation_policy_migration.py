import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_policy_migration_keeps_latest_active_and_removes_index_on_downgrade():
    path = (
        Path(__file__).resolve().parents[3]
        / "alembic/versions/0047_single_active_reconciliation_policy.py"
    )
    spec = importlib.util.spec_from_file_location("single_active_policy_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE reconciliation_policies (id VARCHAR(36) PRIMARY KEY, active BOOLEAN NOT NULL, "
                    "updated_at DATETIME NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO reconciliation_policies (id, active, updated_at) VALUES (:id, :active, :updated_at)"
                ),
                [
                    {"id": "older", "active": True, "updated_at": "2026-01-01T00:00:00+00:00"},
                    {"id": "winner", "active": True, "updated_at": "2026-02-01T00:00:00+00:00"},
                    {"id": "inactive", "active": False, "updated_at": "2026-03-01T00:00:00+00:00"},
                ],
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                active_ids = (
                    connection.execute(
                        text("SELECT id FROM reconciliation_policies WHERE active = 1")
                    )
                    .scalars()
                    .all()
                )
                assert active_ids == ["winner"]
                assert any(
                    index["name"] == migration.INDEX_NAME
                    for index in inspect(connection).get_indexes("reconciliation_policies")
                )

                migration.downgrade()
                assert not any(
                    index["name"] == migration.INDEX_NAME
                    for index in inspect(connection).get_indexes("reconciliation_policies")
                )
    finally:
        engine.dispose()
