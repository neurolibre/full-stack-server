"""
NeuroLibre FastAPI Application Factory.

Creates and configures the FastAPI application for preview or preprint server.
"""

import logging
import os
from typing import Literal
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import PreviewSettings, PreprintSettings, CommonSettings
from .middleware.error_handling import (
    validation_exception_handler,
    http_exception_handler,
    general_exception_handler
)
from .middleware.logging_middleware import RequestLoggingMiddleware

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_app(server_type: Literal["preview", "preprint"] = "preview") -> FastAPI:
    """
    Application factory for creating FastAPI application.

    Args:
        server_type: Type of server to create ("preview" or "preprint")

    Returns:
        Configured FastAPI application instance
    """

    # Load appropriate configuration
    if server_type == "preprint":
        settings = PreprintSettings.load()
    else:
        settings = PreviewSettings.load()

    # Create FastAPI application
    app = FastAPI(
        title=f"NeuroLibre {server_type.capitalize()} API",
        description="Reproducible preprint publishing platform for neuroscience",
        version="2.0.0",
        docs_url="/documentation",
        redoc_url="/redoc",
        openapi_url="/swagger/openapi.json",
        # OpenAPI tags for organization
        openapi_tags=[
            {"name": "Health", "description": "Health check and status endpoints"},
            {"name": "Books", "description": "Jupyter Book operations"},
            {"name": "MyST", "description": "MyST format article operations"},
            {"name": "Data", "description": "Data download and synchronization"},
            {"name": "Zenodo", "description": "Zenodo archival operations"},
            {"name": "Git", "description": "Git and GitHub operations"},
            {"name": "Logs", "description": "Log viewer and utilities"},
            {"name": "UI", "description": "User interface endpoints"},
            {"name": "Tests", "description": "Testing and debugging endpoints"},
        ]
    )

    # Store settings in app state for access in routes
    app.state.settings = settings
    app.state.server_type = server_type

    # Setup trusted host middleware (security)
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=[
            settings.server_domain,
            f"{settings.server_slug}.{settings.server_domain}",
            "localhost",
            "127.0.0.1",
        ]
    )

    # Setup request logging middleware
    app.add_middleware(RequestLoggingMiddleware)

    # Setup CORS if needed (currently commented in Flask version)
    # app.add_middleware(
    #     CORSMiddleware,
    #     allow_origins=["*"],
    #     allow_credentials=True,
    #     allow_methods=["*"],
    #     allow_headers=["*"],
    # )

    # Setup Jinja2 templates
    templates_dir = os.path.join(os.path.dirname(__file__), "..", "templates")
    app.state.templates = Jinja2Templates(directory=templates_dir)

    # Mount static files (if served by FastAPI, though NGINX is preferred)
    # assets_dir = os.path.join(os.path.dirname(__file__), "..", "..", "assets")
    # if os.path.exists(assets_dir):
    #     app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    # Register exception handlers
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, general_exception_handler)

    # Register routers
    from .routers import common, ui

    app.include_router(common.router, tags=["Health", "Books", "Data", "Logs"])
    app.include_router(ui.router, tags=["UI"])

    if server_type == "preview":
        from .routers import preview
        app.include_router(preview.router, tags=["Books", "MyST", "Data", "Git", "Tests"])
    else:
        from .routers import preprint
        app.include_router(preprint.router, tags=["Zenodo", "Data", "Books", "MyST", "Tests"])

    # Startup event
    @app.on_event("startup")
    async def startup_event():
        logger.info(f"🚀 Starting NeuroLibre {server_type.capitalize()} API v2.0.0")
        logger.info(f"📍 Server: {settings.server_slug}.{settings.server_domain}")
        logger.info(f"🔗 BinderHub: {settings.binder_name}.{settings.binder_domain}")

    # Shutdown event
    @app.on_event("shutdown")
    async def shutdown_event():
        logger.info(f"Shutting down NeuroLibre {server_type.capitalize()} API")

    # Root redirect
    @app.get("/", include_in_schema=False)
    async def root():
        """Redirect root to documentation"""
        from fastapi.responses import RedirectResponse
        return RedirectResponse(url="/documentation")

    logger.info(f"✅ FastAPI application created for {server_type} server")

    return app


# Create application instances for WSGI/ASGI deployment
preview_app = create_app("preview")
preprint_app = create_app("preprint")

# Default app (can be selected via SERVER_TYPE env var)
app = preview_app if os.getenv("SERVER_TYPE", "preview").lower() == "preview" else preprint_app
