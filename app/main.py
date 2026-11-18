from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.health import router as health_router
from app.api.routes.holds import router as holds_router
from app.api.routes.products import router as products_router
from app.api.routes.sync import router as sync_router
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    yield
    engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    application = FastAPI(title=settings.app_name, version="0.1.0", lifespan=lifespan)
    application.include_router(health_router)
    application.include_router(holds_router)
    application.include_router(products_router)
    application.include_router(sync_router)
    return application


app = create_app()
