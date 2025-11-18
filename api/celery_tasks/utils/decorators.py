"""
Decorators for Celery tasks.

Provides timeout handling and other common task decorators.
"""

import os
import logging
import functools
from celery import states
from celery.exceptions import Ignore, SoftTimeLimitExceeded, TimeoutError
from github import Github

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
from github_client import gh_template_respond

from ..config import JOURNAL_NAME

logger = logging.getLogger(__name__)


def handle_soft_timeout(func):
    """
    Decorator to handle SoftTimeLimitExceeded and TimeoutError exceptions for Celery tasks.

    This decorator wraps a Celery task function and catches timeout exceptions,
    updating the task state and raising Ignore to prevent the task from being retried.

    Args:
        func: The Celery task function to wrap

    Returns:
        Wrapped function with timeout handling
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)

        except (SoftTimeLimitExceeded, TimeoutError) as e:
            task_name = func.__name__
            exception_type = e.__class__.__name__
            logger.error(f"Task {task_name} timed out with {exception_type}: {str(e)}")

            # Try to extract issue_id and other info for GitHub notification
            issue_id = None
            comment_id = None
            review_repository = None

            # Extract parameters from different argument patterns
            if args and isinstance(args[0], dict):
                if 'issue_id' in args[0]:
                    issue_id = args[0]['issue_id']
                if 'comment_id' in args[0]:
                    comment_id = args[0]['comment_id']
                if 'review_repository' in args[0]:
                    review_repository = args[0]['review_repository']

            # If we have a BaseNeuroLibreTask
            if hasattr(self, 'screening') and hasattr(self.screening, 'issue_id'):
                self.screening.respond.FAILURE(
                    f"Task timed out after reaching its time limit: {str(e)}"
                )

            # If we have enough info for a GitHub notification
            elif issue_id and comment_id and review_repository:
                try:
                    GH_BOT = os.getenv('GH_BOT')
                    github_client = Github(GH_BOT)
                    gh_template_respond(
                        github_client,
                        "failure",
                        review_repository,
                        comment_id,
                        issue_id,
                        task_name.upper(),
                        {
                            'details': f"{JOURNAL_NAME} celery exception",
                            'message': f"Task timed out: {str(e)}"
                        }
                    )
                except Exception as notify_error:
                    logger.error(f"Failed to notify GitHub about timeout: {notify_error}")

            # Update task state
            self.update_state(
                state=states.FAILURE,
                meta={
                    'exc_type': 'TimeoutError',
                    'exc_message': str(e),
                    'message': f"Task {task_name} exceeded time limit"
                }
            )

            # Raise Ignore to prevent retry
            raise Ignore()

    return wrapper
