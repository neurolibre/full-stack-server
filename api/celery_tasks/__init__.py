"""
Modular Celery tasks for NeuroLibre.

This package organizes the Celery tasks by domain for better maintainability.
All tasks are re-exported here for backward compatibility with existing code.
"""

# Import Celery app instance
from .celery_app import celery_app

# Import all tasks for backward compatibility
from .tasks.test import sleep_task
from .tasks.data import rsync_data_task, preview_download_data
from .tasks.book import (
    rsync_book_task,
    preview_build_book_task,
    preview_build_book_test_task
)
from .tasks.myst import (
    preview_build_myst_task,
    rsync_myst_prod_task,
    myst_upload_task
)
from .tasks.zenodo import (
    zenodo_create_buckets_task,
    zenodo_upload_book_task,
    zenodo_upload_data_task,
    zenodo_upload_repository_task,
    zenodo_upload_docker_task,
    zenodo_publish_task,
    zenodo_flush_task
)
from .tasks.git import (
    fork_configure_repository_task,
    sync_fork_from_upstream_task
)
from .tasks.binder import binder_build_task
from .tasks.pdf import preprint_build_pdf_draft
from .tasks.email import (
    send_email_celery,
    send_email_with_html_attachment_celery
)

__all__ = [
    # Celery app
    "celery_app",
    # Test tasks
    "sleep_task",
    # Data tasks
    "rsync_data_task",
    "preview_download_data",
    # Book tasks
    "rsync_book_task",
    "preview_build_book_task",
    "preview_build_book_test_task",
    # MyST tasks
    "preview_build_myst_task",
    "rsync_myst_prod_task",
    "myst_upload_task",
    # Zenodo tasks
    "zenodo_create_buckets_task",
    "zenodo_upload_book_task",
    "zenodo_upload_data_task",
    "zenodo_upload_repository_task",
    "zenodo_upload_docker_task",
    "zenodo_publish_task",
    "zenodo_flush_task",
    # Git tasks
    "fork_configure_repository_task",
    "sync_fork_from_upstream_task",
    # Binder tasks
    "binder_build_task",
    # PDF tasks
    "preprint_build_pdf_draft",
    # Email tasks
    "send_email_celery",
    "send_email_with_html_attachment_celery",
]
