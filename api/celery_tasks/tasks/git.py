"""
Git and GitHub operation tasks.

Handles repository forking, configuration, and synchronization.
"""

import os
import sys

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

# Import tasks from original file for backward compatibility
# TODO: Migrate these tasks to this module
from neurolibre_celery_tasks import (
    fork_configure_repository_task,
    sync_fork_from_upstream_task
)

__all__ = [
    "fork_configure_repository_task",
    "sync_fork_from_upstream_task"
]
