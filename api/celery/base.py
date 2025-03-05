"""
Base Task Class

This module defines the BaseNeuroLibreTask class that provides common functionality
for all Celery tasks in the NeuroLibre application.
"""

import os
from celery import states
from celery.exceptions import Ignore
from screening_client import ScreeningClient
from common import get_owner_repo_provider, format_commit_hash, get_deposit_dir, get_archive_dir
from .app import (
    DATA_ROOT_PATH,
    MYST_FOLDER,
    JOURNAL_NAME
)

class BaseNeuroLibreTask:
    """
    Base class for all NeuroLibre Celery tasks.
    
    This class provides common functionality for all tasks, such as:
    - Task initialization
    - State management
    - GitHub interaction
    - Path handling
    """
    
    def __init__(self, celery_task, screening=None, payload=None):
        """
        Initialize a BaseNeuroLibreTask.
        
        Args:
            celery_task: The Celery task instance
            screening: A dictionary containing screening information
            payload: A dictionary containing task payload
        """
        self.celery_task = celery_task
        self.payload = payload
        self.task_id = celery_task.request.id
        if screening:
            screening['notify_target'] = True
            self.screening = ScreeningClient.from_dict(screening)
            self.screening.task_id = self.task_id
            self.owner_name, self.repo_name, self.provider_name = get_owner_repo_provider(self.screening.target_repo_url, provider_full_name=True)
        elif payload:
            # This will be probably deprecated soon. For now, reserve for backward compatibility.
            self.screening = ScreeningClient(
                payload['task_name'],
                payload['issue_id'],
                payload['repo_url'],
                self.task_id,
                payload['comment_id'])
            self.owner_name, self.repo_name, self.provider_name = get_owner_repo_provider(payload['repo_url'], provider_full_name=True)
        else:
            raise ValueError("Either screening or payload must be provided.")

    def start(self, message=""):
        """Mark the task as started and update GitHub issue."""
        if self.screening.issue_id is not None:
            self.screening.respond.STARTED(message)
            self.update_state(states.STARTED, {'message': message})

    def fail(self, message, attachment_path=None):
        """Mark the task as failed and update GitHub issue."""
        if self.screening.issue_id is not None:
            if attachment_path and os.path.exists(attachment_path):
                # Create comment with file attachment
                self.screening.STATE_WITH_ATTACHMENT(message, attachment_path, failure=True)
            else:
                # Original failure response
                self.screening.respond.FAILURE(message, collapsable=False)
                
            self.update_state(state=states.FAILURE, meta={
                'exc_type': f"{JOURNAL_NAME} celery exception",
                'exc_message': "Custom", 
                'message': message
            })
            raise Ignore()

    def email_user(self, message):
        """Send an email to the user."""
        if self.screening.email_address is not None:
            self.screening.send_user_email(message)

    def succeed(self, message, collapsable=True, attachment_path=None):
        """Mark the task as successful and update GitHub issue."""
        if self.screening.issue_id is not None:
            if attachment_path:
                self.screening.STATE_WITH_ATTACHMENT(message, attachment_path, failure=False)
            else:
                self.screening.respond.SUCCESS(message, collapsable=collapsable)

    def update_state(self, state, meta):
        """Update the state of the Celery task."""
        self.celery_task.update_state(state=state, meta=meta)

    def get_commit_hash(self):
        """Get the commit hash for the repository."""
        return format_commit_hash(self.payload['repo_url'], self.payload.get('commit_hash', 'HEAD'))
    
    def get_dotenv_path(self):
        """Get the path to the .env file."""
        return self.path_join(os.environ.get('HOME'),'full-stack-server','api')

    def path_join(self, *args):
        """Join path components."""
        return os.path.join(*args)

    def join_data_root_path(self, *args):
        """Join path components with the data root path."""
        return self.path_join(DATA_ROOT_PATH, *args)

    def join_myst_path(self, *args):
        """Join path components with the myst folder path."""
        return self.path_join(DATA_ROOT_PATH, MYST_FOLDER, *args)

    def get_deposit_dir(self, *args):
        """Get the deposit directory path."""
        return self.path_join(get_deposit_dir(self.payload['issue_id']), *args)

    def get_archive_dir(self, *args):
        """Get the archive directory path."""
        return self.path_join(get_archive_dir(self.payload['issue_id']), *args) 