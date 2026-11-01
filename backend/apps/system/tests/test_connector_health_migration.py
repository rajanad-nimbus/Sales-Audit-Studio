import importlib.util
from pathlib import Path

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text


def test_connector_health_migration_downgrade_does_not_invent_healthy_state():
    path = Path(__file__).resolve().parents[3] / "alembic/versions/0046_connector_health_truth.py"
    spec = importlib.util.spec_from_file_location("connector_health_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE connectors (id VARCHAR(36) PRIMARY KEY, name VARCHAR(100) NOT NULL, "
                    "state VARCHAR(50) NOT NULL, mode VARCHAR(50) NOT NULL, "
                    "last_checked_at DATETIME NOT NULL)"
                )
            )
            connection.execute(
                text(
                    "INSERT INTO connectors (id, name, state, mode, last_checked_at) "
                    "VALUES ('connector', 'POS Feed', 'Healthy', 'Governed Automation', CURRENT_TIMESTAMP)"
                )
            )
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                upgraded = connection.execute(
                    text(
                        "SELECT state, mode, last_checked_at FROM connectors WHERE id = 'connector'"
                    )
                ).one()
                assert tuple(upgraded) == ("Unknown", "Not Configured", None)

                migration.downgrade()
                downgraded = connection.execute(
                    text(
                        "SELECT state, mode, last_checked_at FROM connectors WHERE id = 'connector'"
                    )
                ).one()
                assert downgraded.state == "Unknown"
                assert downgraded.mode == "Not Configured"
                assert downgraded.last_checked_at is not None
                last_checked = next(
                    column
                    for column in inspect(connection).get_columns("connectors")
                    if column["name"] == "last_checked_at"
                )
                assert last_checked["nullable"] is False
    finally:
        engine.dispose()
