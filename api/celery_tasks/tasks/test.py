"""
Test and utility Celery tasks.

Simple tasks for testing Celery worker functionality.
"""

import os
import sys
import time

# Add parent directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))

from ..celery_app import celery_app


@celery_app.task(bind=True)
def sleep_task(self, seconds):
    """
    Simple sleep task for testing Celery workers.

    Args:
        seconds: Number of seconds to sleep

    Returns:
        dict: Task completion message
    """
    time.sleep(seconds)
    return {"status": "completed", "slept_for": seconds}
