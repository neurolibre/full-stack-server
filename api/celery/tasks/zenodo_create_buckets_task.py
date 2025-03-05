"""
Zenodo Create Buckets Task

Task for creating Zenodo buckets for a repository.
"""

import os
import logging
import json
from celery import states
from ..app import celery_app, ZENODO_SERVER, ZENODO_TOKEN, ZENODO_SANDBOX_SERVER, ZENODO_SANDBOX_TOKEN
from ..base import BaseNeuroLibreTask
from ..utils import zenodo_create_deposition, zenodo_create_bucket_url, zenodo_get_deposition_id, zenodo_get_bucket_id

@celery_app.task(bind=True, soft_time_limit=600, time_limit=1000)
def zenodo_create_buckets_task(self, screening_dict):
    """
    Create Zenodo buckets for a repository.
    
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
        task.start("🏖️ Creating Zenodo SANDBOX buckets.")
    else:
        server = ZENODO_SERVER
        token = ZENODO_TOKEN
        task.start("🏭 Creating Zenodo PRODUCTION buckets.")
    
    try:
        # Create the book deposition
        book_deposition = zenodo_create_deposition(server, token, f"{task.owner_name}/{task.repo_name} - Book")
        book_deposition_id = zenodo_get_deposition_id(book_deposition)
        book_bucket_id = zenodo_get_bucket_id(book_deposition)
        book_bucket_url = zenodo_create_bucket_url(server, book_bucket_id)
        
        # Create the data deposition
        data_deposition = zenodo_create_deposition(server, token, f"{task.owner_name}/{task.repo_name} - Data")
        data_deposition_id = zenodo_get_deposition_id(data_deposition)
        data_bucket_id = zenodo_get_bucket_id(data_deposition)
        data_bucket_url = zenodo_create_bucket_url(server, data_bucket_id)
        
        # Create the repository deposition
        repo_deposition = zenodo_create_deposition(server, token, f"{task.owner_name}/{task.repo_name} - Repository")
        repo_deposition_id = zenodo_get_deposition_id(repo_deposition)
        repo_bucket_id = zenodo_get_bucket_id(repo_deposition)
        repo_bucket_url = zenodo_create_bucket_url(server, repo_bucket_id)
        
        # Create the docker deposition
        docker_deposition = zenodo_create_deposition(server, token, f"{task.owner_name}/{task.repo_name} - Docker")
        docker_deposition_id = zenodo_get_deposition_id(docker_deposition)
        docker_bucket_id = zenodo_get_bucket_id(docker_deposition)
        docker_bucket_url = zenodo_create_bucket_url(server, docker_bucket_id)
        
        # Store the deposition IDs and bucket URLs in the task's result
        result = {
            "book_deposition_id": book_deposition_id,
            "book_bucket_url": book_bucket_url,
            "data_deposition_id": data_deposition_id,
            "data_bucket_url": data_bucket_url,
            "repo_deposition_id": repo_deposition_id,
            "repo_bucket_url": repo_bucket_url,
            "docker_deposition_id": docker_deposition_id,
            "docker_bucket_url": docker_bucket_url
        }
        
        # Update the task state with the result
        self.update_state(
            state=states.SUCCESS,
            meta={
                'book_deposition_id': book_deposition_id,
                'book_bucket_url': book_bucket_url,
                'data_deposition_id': data_deposition_id,
                'data_bucket_url': data_bucket_url,
                'repo_deposition_id': repo_deposition_id,
                'repo_bucket_url': repo_bucket_url,
                'docker_deposition_id': docker_deposition_id,
                'docker_bucket_url': docker_bucket_url
            }
        )
        
        # Log the result
        logging.info(f"Created Zenodo buckets for {task.owner_name}/{task.repo_name}: {json.dumps(result, indent=2)}")
        
        # Succeed the task
        if is_sandbox:
            task.succeed(f"🏖️ Created Zenodo SANDBOX buckets for {task.owner_name}/{task.repo_name}.")
        else:
            task.succeed(f"🏭 Created Zenodo PRODUCTION buckets for {task.owner_name}/{task.repo_name}.")
        
        return result
        
    except Exception as e:
        logging.exception(f"Error in zenodo_create_buckets_task: {str(e)}")
        task.fail(f"⛔️ Error creating Zenodo buckets: {str(e)}")
        raise 