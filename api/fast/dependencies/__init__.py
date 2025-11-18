"""
FastAPI dependency injection components.

Provides reusable dependencies for authentication, configuration, and shared resources.
"""

from .auth import verify_credentials, get_current_user
from .config import get_settings

__all__ = ["verify_credentials", "get_current_user", "get_settings"]
