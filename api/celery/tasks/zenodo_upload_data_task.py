"""
Zenodo Upload Data Task

Task for uploading data to Zenodo.
"""

import os
import logging
import requests
import tarfile
import tempfile
import shutil
from celery import states
from ..app import celery_app, ZENODO_SERVER, ZENODO_TOKEN, ZENODO_SANDBOX_SERVER, ZENODO_SANDBOX_TOKEN, DATA_ROOT_PATH
from ..base import BaseNeuroLibreTask
from ..utils import zenodo_upload_file

@celery_app.task(bind=True, soft_time_limit=600, time_limit=1000)
def zenodo_upload_data_task(self, screening_dict):
    """
    Upload data to Zenodo.
    
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
        task.start("🏖️ Uploading data to Zenodo SANDBOX.")
    else:
        server = ZENODO_SERVER
        token = ZENODO_TOKEN
        task.start("🏭 Uploading data to Zenodo PRODUCTION.")
    
    try:
        # Get the data bucket URL from the task's parent
        data_bucket_url = task.screening.data_bucket_url
        if not data_bucket_url:
            task.fail("⛔️ No data bucket URL provided.")
            return
        
        # Get the data path
        data_path = os.path.join(DATA_ROOT_PATH, "data", task.owner_name, task.repo_name)
        if not os.path.exists(data_path) or not os.listdir(data_path):
            task.fail(f"⛔️ Data path does not exist or is empty: {data_path}")
            return
        
        # Create a temporary directory for the data archive
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create the data archive
            archive_path = os.path.join(temp_dir, f"{task.repo_name}-data.tar.gz")
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(data_path, arcname=os.path.basename(data_path))
            
            # Upload the data archive to Zenodo
            task.start(f"Uploading data archive to Zenodo: {archive_path}")
            response = zenodo_upload_file(data_bucket_url, token, archive_path)
            
            if response.status_code != 201:
                task.fail(f"⛔️ Failed to upload data to Zenodo: {response.text}")
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
            logging.info(f"Uploaded data to Zenodo for {task.owner_name}/{task.repo_name}: {file_id}")
            
            # Succeed the task
            if is_sandbox:
                task.succeed(f"🏖️ Uploaded data to Zenodo SANDBOX for {task.owner_name}/{task.repo_name}.")
            else:
                task.succeed(f"🏭 Uploaded data to Zenodo PRODUCTION for {task.owner_name}/{task.repo_name}.")
            
            return {'file_id': file_id}
        
    except Exception as e:
        logging.exception(f"Error in zenodo_upload_data_task: {str(e)}")
        task.fail(f"⛔️ Error uploading data to Zenodo: {str(e)}")
        raise 