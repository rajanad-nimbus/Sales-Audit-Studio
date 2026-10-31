import asyncio
from types import SimpleNamespace

import pytest

from apps.exports.routes import closed_case_context


class RecordingResult:
    def all(self):
        return []

    def scalars(self):
        return self


class RecordingDatabase:
    def __init__(self, dialect):
        self.bind = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self.statements = []

    def get_bind(self):
        return self.bind

    async def execute(self, statement):
        self.statements.append(statement)
        return RecordingResult()


@pytest.mark.parametrize(
    "dialect,expected_queries",
    [("postgresql", 2), ("sqlite", 6)],
)
def test_closed_case_context_batches_large_case_sets_by_database(dialect, expected_queries):
    async def run():
        db = RecordingDatabase(dialect)
        cases = [SimpleNamespace(id=f"case-{index}") for index in range(700)]

        context = await closed_case_context(db, cases)

        assert context == {"recs": {}, "decs": {}, "vals": {}, "refs": {}}
        assert len(db.statements) == expected_queries

    asyncio.run(run())
