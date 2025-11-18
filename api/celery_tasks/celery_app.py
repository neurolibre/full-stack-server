"""
Celery application instance and configuration.

Centralized Celery configuration for all NeuroLibre tasks.
"""

import logging
from celery import Celery
from dotenv import load_dotenv

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# Load environment variables
load_dotenv()

# Create Celery app instance
celery_app = Celery(
    'neurolibre_celery_tasks',
    backend='redis://localhost:6379/1',
    broker='redis://localhost:6379/0'
)

# Celery configuration
celery_app.conf.update(
    # Task tracking
    task_track_started=True,
    broker_connection_retry_on_startup=True,

    # Redis optimizations
    broker_pool_limit=10,
    redis_max_connections=50,
    result_expires=86400,  # 24 hours
    result_persistent=True,

    # Long-running task support
    broker_transport_options={
        'visibility_timeout': 7200,  # 2 hours
    },

    # Worker optimizations
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,
)

# Auto-discover tasks from task modules
celery_app.autodiscover_tasks([
    'celery_tasks.tasks.test',
    'celery_tasks.tasks.data',
    'celery_tasks.tasks.book',
    'celery_tasks.tasks.myst',
    'celery_tasks.tasks.zenodo',
    'celery_tasks.tasks.git',
    'celery_tasks.tasks.binder',
    'celery_tasks.tasks.pdf',
    'celery_tasks.tasks.email',
])

logging.info("✅ Celery app configured successfully")
