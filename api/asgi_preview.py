"""
ASGI entry point for preview server.

Used by Uvicorn/Gunicorn to serve the preview FastAPI application.

Usage:
    uvicorn asgi_preview:app --host 0.0.0.0 --port 5000

    Or with Gunicorn:
    gunicorn asgi_preview:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:5000
"""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

# Set server type for configuration
os.environ.setdefault("SERVER_TYPE", "preview")

from fast.main import preview_app as app

# Export for ASGI server
__all__ = ["app"]
