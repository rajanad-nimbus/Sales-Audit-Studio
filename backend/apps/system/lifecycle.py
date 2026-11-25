"""Application startup and shutdown lifecycle."""

import asyncio
from contextlib import asynccontextmanager, suppress

import httpx
from database import async_session, engine
from fastapi import FastAPI
from integrations.ontology.ontology_integration import (
    integration as ontology_integration,
)
from integrations.ontology.ontology_integration import (
    refresh_loop as ontology_refresh_loop,
)
from integrations.ontology.ontology_integration import (
    refresh_loop_enabled as ontology_refresh_enabled,
)

from apps.ingest.shopify import scheduler as shopify_scheduler
from apps.store_days import automation as store_day_automation
from apps.system.connectors import ensure_default_connectors


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Provision required records, refresh ontology metadata, and release resources."""
    try:
        limits = httpx.Limits(max_connections=100, max_keepalive_connections=20)
        timeout = httpx.Timeout(5.0, connect=2.0, pool=2.0)
        async with httpx.AsyncClient(timeout=timeout, limits=limits) as identity_client:
            _app.state.identity_http_client = identity_client
            try:
                export_limits = httpx.Limits(max_connections=50, max_keepalive_connections=10)
                export_timeout = httpx.Timeout(15.0, connect=3.0, pool=2.0)
                async with httpx.AsyncClient(
                    timeout=export_timeout, limits=export_limits
                ) as export_client:
                    _app.state.export_http_client = export_client
                    try:
                        async with async_session() as db:
                            await ensure_default_connectors(db)
                        await ontology_integration.refresh()
                        sync_schedule = (
                            asyncio.create_task(shopify_scheduler.loop())
                            if shopify_scheduler.enabled()
                            else None
                        )
                        refresh_schedule = (
                            asyncio.create_task(ontology_refresh_loop())
                            if ontology_refresh_enabled()
                            else None
                        )
                        agent_schedule = (
                            asyncio.create_task(store_day_automation.loop())
                            if store_day_automation.loop_enabled()
                            else None
                        )
                        try:
                            yield
                        finally:
                            if refresh_schedule:
                                refresh_schedule.cancel()
                                with suppress(asyncio.CancelledError):
                                    await refresh_schedule
                            if agent_schedule:
                                agent_schedule.cancel()
                                with suppress(asyncio.CancelledError):
                                    await agent_schedule
                            if sync_schedule:
                                sync_schedule.cancel()
                                with suppress(asyncio.CancelledError):
                                    await sync_schedule
                    finally:
                        del _app.state.export_http_client
            finally:
                del _app.state.identity_http_client
    finally:
        await engine.dispose()
