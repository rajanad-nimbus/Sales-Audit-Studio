import asyncio

import pytest
from fastapi import HTTPException

from apps.ingest import reconciliation
from apps.ingest.manifests import manifest_query, validate_business_date
from apps.ingest.routes import run_reconciliation
from apps.operations.batch import RunRequest, run_batch, run_daily_batch


class NoDatabase:
    async def execute(self, *_args, **_kwargs):
        raise AssertionError("invalid dates must be rejected before database access")


@pytest.mark.parametrize("invalid", ["", "2026-02-30", "20261001", "2026-10-01T00:00:00", "not-a-date"])
def test_invalid_dates_are_rejected_before_database_access(invalid):
    async def run():
        with pytest.raises(ValueError):
            await reconciliation.reconcile(NoDatabase(), invalid)
        for operation in (
            run_reconciliation(invalid, NoDatabase(), {}),
            run_daily_batch(NoDatabase(), invalid, feeds={}),
            run_batch(RunRequest(business_date=invalid), NoDatabase(), {}),
        ):
            with pytest.raises(HTTPException) as error:
                await operation
            assert error.value.status_code == 422

    asyncio.run(run())


def test_maximum_date_does_not_overflow_manifest_query():
    assert validate_business_date("9999-12-31") == "9999-12-31"
    assert "source_event_time >=" in str(manifest_query("9999-12-31", ("Archived",)))
