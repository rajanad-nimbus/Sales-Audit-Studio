import asyncio
from types import SimpleNamespace

import pytest

from apps.exports.routes import prior_exported_days


class RecordingDatabase:
    def __init__(self, dialect):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self.statements = []

    def get_bind(self):
        return self.bind

    async def execute(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(all=lambda: [])


@pytest.mark.parametrize(
    "dialect,expected_queries",
    [("postgresql", 1), ("sqlite", 3)],
)
def test_prior_exported_day_lookup_batches_by_database_parameter_capacity(dialect, expected_queries):
    async def run():
        db = RecordingDatabase(dialect)
        cases = [
            SimpleNamespace(store_id=f"STORE-{index}", business_date="2026-10-01")
            for index in range(700)
        ]

        assert await prior_exported_days(db, "destination", "resolutions", cases) == {}
        assert len(db.statements) == expected_queries

    asyncio.run(run())
