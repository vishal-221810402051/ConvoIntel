"""FastAPI application entrypoint for Convointel."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend.app.api.router import api_router
from backend.app.config import APPLICATION_TITLE, Settings, get_settings
from backend.app.core.exceptions import register_exception_handlers
from backend.app.logging_config import configure_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    configure_logging(resolved_settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        logger.info("Starting %s", APPLICATION_TITLE)
        yield
        logger.info("Stopping %s", APPLICATION_TITLE)

    application = FastAPI(title=APPLICATION_TITLE, lifespan=lifespan)
    application.state.settings = resolved_settings
    application.include_router(api_router, prefix="/api")
    register_exception_handlers(application)
    return application


app = create_app()
