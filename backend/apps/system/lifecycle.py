"""Application startup and shutdown lifecycle."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from database import engine
from integrations.ontology.ontology_integration import integration as ontology_integration


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Refresh ontology metadata at startup and release database resources at shutdown."""
    await ontology_integration.refresh()
    yield
    await engine.dispose()
