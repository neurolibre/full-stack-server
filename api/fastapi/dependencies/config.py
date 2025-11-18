"""
Configuration dependency for dependency injection.

Provides access to settings throughout the application.
"""

from functools import lru_cache
from ..config import PreviewSettings, PreprintSettings, CommonSettings
import os


@lru_cache()
def get_settings() -> CommonSettings:
    """
    Get cached application settings.

    Determines which settings to load based on SERVER_TYPE environment variable.
    Defaults to preview if not specified.

    Returns:
        PreviewSettings or PreprintSettings instance
    """
    server_type = os.getenv('SERVER_TYPE', 'preview').lower()

    if server_type == 'preprint':
        return PreprintSettings.load()
    else:
        return PreviewSettings.load()


def get_preview_settings() -> PreviewSettings:
    """Get preview server settings (for type hints)"""
    return PreviewSettings.load()


def get_preprint_settings() -> PreprintSettings:
    """Get preprint server settings (for type hints)"""
    return PreprintSettings.load()
