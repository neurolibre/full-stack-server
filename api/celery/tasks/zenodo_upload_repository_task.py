"""
Zenodo Upload Repository Task

Task for uploading a repository to Zenodo.
"""

import os
import logging
import requests
import tempfile
import subprocess
from celery import states
from ..app import celery_app, ZENODO_SERVER, ZENODO_TOKEN, ZENODO_SANDBOX_SERVER, ZENODO_SANDBOX_TOKEN
from ..base import BaseNeuroLibreTask
from ..utils import zenodo_upload_file

@celery_app.task(bind=True, soft_time_limit=600, time_limit=1000)
def zenodo_upload_repository_task(self, screening_dict):
    """
    Upload a repository to Zenodo.
    
    Args:
        screening_dict: Dictionary containing screening information
        
    Returns:
        A message indicating the task is complete
    """
    task = BaseNeuroLibreTask(self, screening_dict)
    
    # Determine if we're using the sandbox or production Zenodo
    is_sandbox = task.screening.is_sandbox
    
    # Set the appropriate server and token
    if is_sandbox:
        server = ZENODO_SANDBOX_SERVER
        token = ZENODO_SANDBOX_TOKEN
        task.start("🏖️ Uploading repository to Zenodo SANDBOX.")
    else:
        server = ZENODO_SERVER
        token = ZENODO_TOKEN
        task.start("🏭 Uploading repository to Zenodo PRODUCTION.")
    
    try:
        # Get the repository bucket URL from the task's parent
        repo_bucket_url = task.screening.repo_bucket_url
        if not repo_bucket_url:
            task.fail("⛔️ No repository bucket URL provided.")
            return
        
        # Get the repository URL
        repo_url = task.screening.target_repo_url
        if not repo_url:
            task.fail("⛔️ No repository URL provided.")
            return
        
        # Create a temporary directory for the repository
        with tempfile.TemporaryDirectory() as temp_dir:
            # Clone the repository
            task.start(f"Cloning repository: {repo_url}")
            clone_cmd = ["git", "clone", "--recursive", repo_url, temp_dir]
            clone_process = subprocess.run(clone_cmd, capture_output=True, text=True)
            
            if clone_process.returncode != 0:
                task.fail(f"⛔️ Failed to clone repository: {clone_process.stderr}")
                return
            
            # Create a zip archive of the repository
            task.start("Creating repository archive")
            archive_path = os.path.join(temp_dir, f"{task.repo_name}.zip")
            archive_cmd = ["git", "archive", "--format=zip", "--output", archive_path, "HEAD"]
            archive_process = subprocess.run(archive_cmd, cwd=temp_dir, capture_output=True, text=True)
            
            if archive_process.returncode != 0:
                task.fail(f"⛔️ Failed to create repository archive: {archive_process.stderr}")
                return
            
            # Upload the repository archive to Zenodo
            task.start(f"Uploading repository archive to Zenodo: {archive_path}")
            response = zenodo_upload_file(repo_bucket_url, token, archive_path)
            
            if response.status_code != 201:
                task.fail(f"⛔️ Failed to upload repository to Zenodo: {response.text}")
                return
            
            # Get the file ID from the response
            file_id = response.json()["id"]
            
            # Update the task state with the file ID
            self.update_state(
                state=states.SUCCESS,
                meta={
                    'file_id': file_id
                }
            )
            
            # Log the result
            logging.info(f"Uploaded repository to Zenodo for {task.owner_name}/{task.repo_name}: {file_id}")
            
            # Succeed the task
            if is_sandbox:
                task.succeed(f"🏖️ Uploaded repository to Zenodo SANDBOX for {task.owner_name}/{task.repo_name}.")
            else:
                task.succeed(f"🏭 Uploaded repository to Zenodo PRODUCTION for {task.owner_name}/{task.repo_name}.")
            
            return {'file_id': file_id}
        
    except Exception as e:
        logging.exception(f"Error in zenodo_upload_repository_task: {str(e)}")
        task.fail(f"⛔️ Error uploading repository to Zenodo: {str(e)}")
        raise 