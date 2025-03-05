"""
NeuroLibre Celery Tasks Package

This package contains all the Celery tasks used by the NeuroLibre application.
"""

# Import all tasks
from ..app import celery_app, celery_config
from ..base import BaseNeuroLibreTask
from .sleep_task import sleep_task
from .rsync_data_task import rsync_data_task
from .rsync_book_task import rsync_book_task
from .rsync_myst_prod_task import rsync_myst_prod_task
from .preview_build_book_test_task import preview_build_book_test_task
from .preview_build_myst_task import preview_build_myst_task
from .preview_download_data import preview_download_data
from .zenodo_create_buckets_task import zenodo_create_buckets_task
from .zenodo_upload_book_task import zenodo_upload_book_task
from .zenodo_upload_data_task import zenodo_upload_data_task
from .zenodo_upload_repository_task import zenodo_upload_repository_task
from .zenodo_upload_docker_task import zenodo_upload_docker_task
from .zenodo_publish_task import zenodo_publish_task
from .zenodo_flush_task import zenodo_flush_task
from .binder_build_task import binder_build_task
from .fork_configure_repository_task import fork_configure_repository_task
# from .preprint_build_pdf_draft import preprint_build_pdf_draft
# from .myst_upload_task import myst_upload_task

# Export all tasks
__all__ = [
    'sleep_task',
    'rsync_data_task',
    'rsync_book_task',
    'rsync_myst_prod_task',
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
    # 'preprint_build_pdf_draft',
    # 'myst_upload_task'
] 