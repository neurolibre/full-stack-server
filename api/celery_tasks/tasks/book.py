"""
Jupyter Book build and synchronization tasks.

Handles building and transferring Jupyter Books between environments.
"""

import os
import sys

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

# Import tasks from original file for backward compatibility
# TODO: Migrate these tasks to this module
from neurolibre_celery_tasks import (
    rsync_book_task,
    preview_build_book_task,
    preview_build_book_test_task
)

__all__ = [
    "rsync_book_task",
    "preview_build_book_task",
    "preview_build_book_test_task"
]
