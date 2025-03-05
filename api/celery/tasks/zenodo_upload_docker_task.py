"""
Zenodo Upload Docker Task

Task for uploading a Docker image to Zenodo.
"""

import os
import logging
import requests
import tempfile
import subprocess
from celery import states
from ..app import celery_app, ZENODO_SERVER, ZENODO_TOKEN, ZENODO_SANDBOX_SERVER, ZENODO_SANDBOX_TOKEN, BINDER_REGISTRY
from ..base import BaseNeuroLibreTask
from ..utils import zenodo_upload_file

@celery_app.task(bind=True, soft_time_limit=600, time_limit=1000)
def zenodo_upload_docker_task(self, screening_dict):
    """
    Upload a Docker image to Zenodo.
    
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
        task.start("🏖️ Uploading Docker image to Zenodo SANDBOX.")
    else:
        server = ZENODO_SERVER
        token = ZENODO_TOKEN
        task.start("🏭 Uploading Docker image to Zenodo PRODUCTION.")
    
    try:
        # Get the docker bucket URL from the task's parent
        docker_bucket_url = task.screening.docker_bucket_url
        if not docker_bucket_url:
            task.fail("⛔️ No Docker bucket URL provided.")
            return
        
        # Get the Docker image name
        docker_image_name = f"{BINDER_REGISTRY}/{task.owner_name}/{task.repo_name}:{task.screening.binder_hash}"
        
        # Create a temporary directory for the Docker image
        with tempfile.TemporaryDirectory() as temp_dir:
            # Save the Docker image
            task.start(f"Saving Docker image: {docker_image_name}")
            save_path = os.path.join(temp_dir, f"{task.repo_name}-docker.tar")
            save_cmd = ["docker", "save", "-o", save_path, docker_image_name]
            save_process = subprocess.run(save_cmd, capture_output=True, text=True)
            
            if save_process.returncode != 0:
                task.fail(f"⛔️ Failed to save Docker image: {save_process.stderr}")
                return
            
            # Compress the Docker image
            task.start("Compressing Docker image")
            compress_cmd = ["gzip", save_path]
            compress_process = subprocess.run(compress_cmd, capture_output=True, text=True)
            
            if compress_process.returncode != 0:
                task.fail(f"⛔️ Failed to compress Docker image: {compress_process.stderr}")
                return
            
            # Upload the Docker image to Zenodo
            compressed_path = f"{save_path}.gz"
            task.start(f"Uploading Docker image to Zenodo: {compressed_path}")
            response = zenodo_upload_file(docker_bucket_url, token, compressed_path)
            
            if response.status_code != 201:
                task.fail(f"⛔️ Failed to upload Docker image to Zenodo: {response.text}")
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
            logging.info(f"Uploaded Docker image to Zenodo for {task.owner_name}/{task.repo_name}: {file_id}")
            
            # Succeed the task
            if is_sandbox:
                task.succeed(f"🏖️ Uploaded Docker image to Zenodo SANDBOX for {task.owner_name}/{task.repo_name}.")
            else:
                task.succeed(f"🏭 Uploaded Docker image to Zenodo PRODUCTION for {task.owner_name}/{task.repo_name}.")
            
            return {'file_id': file_id}
        
    except Exception as e:
        logging.exception(f"Error in zenodo_upload_docker_task: {str(e)}")
        task.fail(f"⛔️ Error uploading Docker image to Zenodo: {str(e)}")
        raise 