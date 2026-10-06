"""Cron entry point. Usage: python batch_job.py --date 2026-10-05 [--rerun] [--simulate]
Real deployments replace simulate_feed with a loader that reads the day's transaction-log extract."""
import argparse
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from database import async_session
from batch import run_daily_batch


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d"))
    ap.add_argument("--rerun", action="store_true")
    ap.parse_args()
    args = ap.parse_args()
    async with async_session() as db:
        run = await run_daily_batch(db, args.date, trigger="schedule", rerun=args.rerun)
    print(f"{run.business_date}: {run.status} loaded={run.transactions_loaded} cases={run.cases_created} {run.error or ''}")
    return 0 if run.status == "Succeeded" else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
