"""
Data download and synchronization tasks.

Handles repo2data downloads and rsync operations between servers.
"""

import os
import sys

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

# Import tasks from original file for backward compatibility
# TODO: Migrate these tasks to this module
from neurolibre_celery_tasks import (
    rsync_data_task,
    preview_download_data
)

__all__ = [
    "rsync_data_task",
    "preview_download_data"
]
