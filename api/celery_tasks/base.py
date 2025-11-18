"""
Base task class for all NeuroLibre Celery tasks.

Provides common functionality for task lifecycle management,
GitHub integration, and error handling.
"""

import os
from celery import states
from celery.exceptions import Ignore

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from screening_client import ScreeningClient
from common import get_owner_repo_provider, get_deposit_dir, get_archive_dir, format_commit_hash

from .config import JOURNAL_NAME, DATA_ROOT_PATH, MYST_FOLDER


class BaseNeuroLibreTask:
    """
    Base class for all NeuroLibre Celery tasks.

    Provides standardized methods for:
    - Task state management
    - GitHub comment updates
    - Email notifications
    - Error handling

    Args:
        celery_task: The Celery task instance (bound task)
        screening: Screening dictionary with task parameters
        payload: Legacy payload format (deprecated)
    """

    def __init__(self, celery_task, screening=None, payload=None):
        self.celery_task = celery_task
        self.payload = payload
        self.task_id = celery_task.request.id

        if screening:
            screening['notify_target'] = True
            self.screening = ScreeningClient.from_dict(screening)
            self.screening.task_id = self.task_id
            self.owner_name, self.repo_name, self.provider_name = get_owner_repo_provider(
                self.screening.target_repo_url,
                provider_full_name=True
            )
        elif payload:
            # Backward compatibility - will be deprecated
            self.screening = ScreeningClient(
                payload['task_name'],
                payload['issue_id'],
                payload['repo_url'],
                self.task_id,
                payload.get('comment_id')
            )
            self.owner_name, self.repo_name, self.provider_name = get_owner_repo_provider(
                payload['repo_url'],
                provider_full_name=True
            )
        else:
            raise ValueError("Either screening or payload must be provided.")

    def start(self, message=""):
        """
        Mark task as started.

        Updates GitHub issue and Celery task state.
        """
        if self.screening.issue_id is not None:
            self.screening.respond.STARTED(message)
            self.update_state(states.STARTED, {'message': message})

    def fail(self, message, attachment_path=None):
        """
        Mark task as failed.

        Updates GitHub issue, Celery state, and raises Ignore to prevent retry.

        Args:
            message: Failure message
            attachment_path: Optional file path to attach to GitHub comment
        """
        if self.screening.issue_id is not None:
            if attachment_path and os.path.exists(attachment_path):
                self.screening.STATE_WITH_ATTACHMENT(message, attachment_path, failure=True)
            else:
                self.screening.respond.FAILURE(message, collapsable=False)

            self.update_state(
                state=states.FAILURE,
                meta={
                    'exc_type': f"{JOURNAL_NAME} celery exception",
                    'exc_message': "Custom",
                    'message': message
                }
            )
            raise Ignore()

    def succeed(self, message, collapsable=True, attachment_path=None):
        """
        Mark task as successful.

        Updates GitHub issue with success message.

        Args:
            message: Success message
            collapsable: Whether GitHub comment should be collapsable
            attachment_path: Optional file path to attach to GitHub comment
        """
        if self.screening.issue_id is not None:
            if attachment_path:
                self.screening.STATE_WITH_ATTACHMENT(message, attachment_path, failure=False)
            else:
                self.screening.respond.SUCCESS(message, collapsable=collapsable)

    def email_user(self, message):
        """
        Send email notification to user.

        Args:
            message: Email message content
        """
        if self.screening.email_address is not None:
            self.screening.send_user_email(message)

    def update_state(self, state, meta):
        """
        Update Celery task state.

        Args:
            state: Celery state (states.STARTED, states.SUCCESS, etc.)
            meta: Metadata dictionary
        """
        self.celery_task.update_state(state=state, meta=meta)

    def get_commit_hash(self):
        """Get formatted commit hash from payload"""
        return format_commit_hash(
            self.payload['repo_url'],
            self.payload.get('commit_hash', 'HEAD')
        )

    def get_dotenv_path(self):
        """Get path to .env file"""
        return self.path_join(os.environ.get('HOME'), 'full-stack-server', 'api')

    def path_join(self, *args):
        """Join path components"""
        return os.path.join(*args)

    def join_data_root_path(self, *args):
        """Join paths under DATA_ROOT_PATH"""
        return self.path_join(DATA_ROOT_PATH, *args)

    def join_myst_path(self, *args):
        """Join paths under MyST folder"""
        return self.path_join(DATA_ROOT_PATH, MYST_FOLDER, *args)

    def get_deposit_dir(self, *args):
        """Get Zenodo deposit directory"""
        return self.path_join(get_deposit_dir(self.payload['issue_id']), *args)

    def get_archive_dir(self, *args):
        """Get Zenodo archive directory"""
        return self.path_join(get_archive_dir(self.payload['issue_id']), *args)
