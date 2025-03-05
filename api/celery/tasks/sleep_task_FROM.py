"""
Sleep Task

A simple task that sleeps for a specified number of seconds.
Used for testing async task functionality.
"""

import time
from ..app import celery_app

@celery_app.task(bind=True)
def sleep_task(self, seconds):
    """
    Sleep for a specified number of seconds.
    
    Args:
        seconds: Number of seconds to sleep
        
    Returns:
        A message indicating the task is complete
    """
    for i in range(seconds):
        time.sleep(1)
        self.update_state(state='PROGRESS', meta={'remaining': seconds - i - 1})
    return 'done sleeping for {} seconds'.format(seconds) 