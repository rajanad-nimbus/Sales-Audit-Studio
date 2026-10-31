import asyncio

from apps.system.routes import health_check


class HealthDatabase:
    def __init__(self):
        self.executed = []

    async def execute(self, statement):
        self.executed.append(statement)


def test_public_health_response_exposes_only_database_availability():
    async def run():
        db = HealthDatabase()

        response = await health_check(db)

        assert response == {"status": "healthy", "database": "healthy"}
        assert len(db.executed) == 1

    asyncio.run(run())
