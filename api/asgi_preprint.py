"""
ASGI entry point for preprint (production) server.

Used by Uvicorn/Gunicorn to serve the preprint FastAPI application.

Usage:
    uvicorn asgi_preprint:app --host 0.0.0.0 --port 5000

    Or with Gunicorn:
    gunicorn asgi_preprint:app --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:5000
"""

import os
import sys

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(__file__))

# Set server type for configuration
os.environ.setdefault("SERVER_TYPE", "preprint")

from fast.main import preprint_app as app

# Export for ASGI server
__all__ = ["app"]
