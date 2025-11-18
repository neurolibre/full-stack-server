"""
Global error handling middleware and exception handlers.

Provides consistent error responses across the API.
"""

from fastapi import Request, status
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
import logging

logger = logging.getLogger(__name__)


async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """
    Custom handler for validation errors (422).

    Provides detailed validation error information while maintaining
    compatibility with existing error handling.
    """
    logger.warning(f"Validation error on {request.url}: {exc.errors()}")

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "detail": exc.errors(),
            "message": "Cannot validate payload. Please check your request data."
        }
    )


async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """
    Custom handler for HTTP exceptions.

    Ensures consistent JSON response format for all HTTP errors.
    """
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "status_code": exc.status_code
        }
    )


async def general_exception_handler(request: Request, exc: Exception):
    """
    Catch-all exception handler for unexpected errors.

    Logs the full exception and returns a generic 500 error to the client.
    """
    logger.error(f"Unexpected error on {request.url}: {exc}", exc_info=True)

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "An internal server error occurred",
            "message": str(exc)
        }
    )
