"""
Zenodo Upload Book Task

Task for uploading a book to Zenodo.
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
def zenodo_upload_book_task(self, screening_dict):
    """
    Upload a book to Zenodo.
    
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
        task.start("🏖️ Uploading book to Zenodo SANDBOX.")
    else:
        server = ZENODO_SERVER
        token = ZENODO_TOKEN
        task.start("🏭 Uploading book to Zenodo PRODUCTION.")
    
    try:
        # Get the book bucket URL from the task's parent
        book_bucket_url = task.screening.book_bucket_url
        if not book_bucket_url:
            task.fail("⛔️ No book bucket URL provided.")
            return
        
        # Get the book path
        book_path = os.path.join(DATA_ROOT_PATH, "books", task.owner_name, task.repo_name, task.screening.commit_hash)
        if not os.path.exists(book_path):
            task.fail(f"⛔️ Book path does not exist: {book_path}")
            return
        
        # Create a temporary directory for the book archive
        with tempfile.TemporaryDirectory() as temp_dir:
            # Create the book archive
            archive_path = os.path.join(temp_dir, f"{task.repo_name}-book.tar.gz")
            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(book_path, arcname=os.path.basename(book_path))
            
            # Upload the book archive to Zenodo
            task.start(f"Uploading book archive to Zenodo: {archive_path}")
            response = zenodo_upload_file(book_bucket_url, token, archive_path)
            
            if response.status_code != 201:
                task.fail(f"⛔️ Failed to upload book to Zenodo: {response.text}")
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
            logging.info(f"Uploaded book to Zenodo for {task.owner_name}/{task.repo_name}: {file_id}")
            
            # Succeed the task
            if is_sandbox:
                task.succeed(f"🏖️ Uploaded book to Zenodo SANDBOX for {task.owner_name}/{task.repo_name}.")
            else:
                task.succeed(f"🏭 Uploaded book to Zenodo PRODUCTION for {task.owner_name}/{task.repo_name}.")
            
            return {'file_id': file_id}
        
    except Exception as e:
        logging.exception(f"Error in zenodo_upload_book_task: {str(e)}")
        task.fail(f"⛔️ Error uploading book to Zenodo: {str(e)}")
        raise 