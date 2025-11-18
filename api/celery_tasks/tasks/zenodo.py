"""
Zenodo archival tasks.

Handles creating buckets, uploading artifacts, and publishing to Zenodo.
"""

import os
import sys

# Add parent directories to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../../..'))

# Import tasks from original file for backward compatibility
# TODO: Migrate these tasks to this module
from neurolibre_celery_tasks import (
    zenodo_create_buckets_task,
    zenodo_upload_book_task,
    zenodo_upload_data_task,
    zenodo_upload_repository_task,
    zenodo_upload_docker_task,
    zenodo_publish_task,
    zenodo_flush_task
)

__all__ = [
    "zenodo_create_buckets_task",
    "zenodo_upload_book_task",
    "zenodo_upload_data_task",
    "zenodo_upload_repository_task",
    "zenodo_upload_docker_task",
    "zenodo_publish_task",
    "zenodo_flush_task"
]
