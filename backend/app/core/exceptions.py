"""Minimal exception boundary for unexpected application errors."""

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette import status

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Register safe application-level exception handlers."""

    @app.exception_handler(Exception)
    async def unexpected_exception_handler(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        logger.exception(
            "Unhandled exception while processing %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )
