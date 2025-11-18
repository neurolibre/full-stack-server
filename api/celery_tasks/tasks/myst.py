"""
MyST article build and upload tasks.

Handles MyST format article builds and Zenodo uploads.
"""

import os
import sys

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

# Import tasks from original file for backward compatibility
# TODO: Migrate these tasks to this module
from neurolibre_celery_tasks import (
    preview_build_myst_task,
    rsync_myst_prod_task,
    myst_upload_task
)

__all__ = [
    "preview_build_myst_task",
    "rsync_myst_prod_task",
    "myst_upload_task"
]
