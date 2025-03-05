"""
Rsync Data Task

Task for synchronizing data between servers using rsync.
"""

import os
import logging
from github import Github
from celery import states
from api.celery.app import celery_app, celery_config, get_github_bot_token
from api.celery.utils import run_celery_subprocess, gh_template_respond, get_time
from common import get_time

@celery_app.task(bind=True)
def rsync_data_task(self, comment_id, issue_id, project_name, reviewRepository):
    """
    Upload data to the production server from the test server.
    
    Args:
        comment_id: GitHub comment ID
        issue_id: GitHub issue ID
        project_name: Name of the project
        reviewRepository: GitHub repository for review
        
    Returns:
        A message indicating the task is complete
    """
    task_title = "DATA TRANSFER (Preview --> Preprint)"
    GH_BOT = os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_id = self.request.id
    remote_path = os.path.join("neurolibre-preview:", "DATA", project_name)
    
    # Log the remote path
    f = open(f"{DATA_ROOT_PATH}/data_synclog.txt", "a")
    f.write(remote_path)
    f.close()
    
    now = get_time()
    self.update_state(state=states.STARTED, meta={'message': f"Transfer started {now}"})
    gh_template_respond(github_client, "started", task_title, reviewRepository, issue_id, task_id, comment_id, "")
    
    return_code, output = run_celery_subprocess(["/usr/bin/rsync", "-avR", remote_path, "/"])
    
    if return_code != 0:
        gh_template_respond(github_client, "failure", task_title, reviewRepository, issue_id, task_id, comment_id, f"{output}")
        self.update_state(state=states.FAILURE, meta={'exc_type': f"{JOURNAL_NAME} celery exception", 'exc_message': "Custom", 'message': output})
    
    # Performing a final check
    if os.path.exists(os.path.join(DATA_ROOT_PATH, project_name)):
        if len(os.listdir(os.path.join(DATA_ROOT_PATH, project_name))) == 0:
            # Directory exists but empty
            self.update_state(state=states.FAILURE, meta={'exc_type': f"{JOURNAL_NAME} celery exception", 'exc_message': "Custom", 'message': f"Directory exists but empty {project_name}"})
            gh_template_respond(github_client, "failure", task_title, reviewRepository, issue_id, task_id, comment_id, f"Directory exists but empty: {project_name}")
        else:
            # Directory exists and not empty
            gh_template_respond(github_client, "success", task_title, reviewRepository, issue_id, task_id, comment_id, "Success.")
            self.update_state(state=states.SUCCESS, meta={'message': f"Data sync has been completed for {project_name}"})
    else:
        # Directory does not exist
        self.update_state(state=states.FAILURE, meta={'exc_type': f"{JOURNAL_NAME} celery exception", 'exc_message': "Custom", 'message': f"Directory does not exist {project_name}"})
        gh_template_respond(github_client, "failure", task_title, reviewRepository, issue_id, task_id, comment_id, f"Directory does not exist: {project_name}") 