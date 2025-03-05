"""
NeuroLibre Celery Tasks Package

This package contains all the Celery tasks used by the NeuroLibre application.
"""

# Import the Celery app
from .app import celery_app

# Import all tasks from the tasks package
from .tasks import (
    sleep_task,
    rsync_data_task,
    rsync_book_task,
    rsync_myst_prod_task,
    preview_build_book_task,
    preview_build_book_test_task,
    preview_build_myst_task,
    preview_download_data,
    zenodo_create_buckets_task,
    zenodo_upload_book_task,
    zenodo_upload_data_task,
    zenodo_upload_repository_task,
    zenodo_upload_docker_task,
    zenodo_publish_task,
    zenodo_flush_task,
    binder_build_task,
    fork_configure_repository_task,
    preprint_build_pdf_draft,
    myst_upload_task
)

# Export all tasks
__all__ = [
    'celery_app',
    'sleep_task',
    'rsync_data_task',
    'rsync_book_task',
    'rsync_myst_prod_task',
    'preview_build_book_task',
    'preview_build_book_test_task',
    'preview_build_myst_task',
    'preview_download_data',
    'zenodo_create_buckets_task',
    'zenodo_upload_book_task',
    'zenodo_upload_data_task',
    'zenodo_upload_repository_task',
    'zenodo_upload_docker_task',
    'zenodo_publish_task',
    'zenodo_flush_task',
    'binder_build_task',
    'fork_configure_repository_task',
    'preprint_build_pdf_draft',
    'myst_upload_task'
]

# Uncomment this line in app.py after all modules are created
# app.autodiscover_tasks(['celery_tasks']) 