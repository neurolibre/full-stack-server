"""
Request logging middleware.

Logs all incoming requests and responses for debugging and monitoring.
"""

import time
import logging
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware to log all HTTP requests and responses.

    Logs request method, path, processing time, and response status.
    """

    async def dispatch(self, request: Request, call_next):
        start_time = time.time()

        # Log incoming request
        logger.info(f"→ {request.method} {request.url.path}")

        # Process request
        response = await call_next(request)

        # Calculate processing time
        process_time = time.time() - start_time

        # Log response
        logger.info(
            f"← {request.method} {request.url.path} "
            f"[{response.status_code}] {process_time:.3f}s"
        )

        # Add processing time header
        response.headers["X-Process-Time"] = str(process_time)

        return response
