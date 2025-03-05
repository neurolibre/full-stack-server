"""
Zenodo Flush Task

Task for flushing (deleting) Zenodo depositions.
"""

import os
import logging
import requests
from celery import states
from ..app import celery_app, ZENODO_SERVER, ZENODO_TOKEN, ZENODO_SANDBOX_SERVER, ZENODO_SANDBOX_TOKEN
from ..base import BaseNeuroLibreTask
from ..utils import zenodo_delete_deposition

@celery_app.task(bind=True, soft_time_limit=600, time_limit=1000)
def zenodo_flush_task(self, screening_dict):
    """
    Flush (delete) Zenodo depositions.
    
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
        task.start("🏖️ Flushing Zenodo SANDBOX depositions.")
    else:
        server = ZENODO_SERVER
        token = ZENODO_TOKEN
        task.start("🏭 Flushing Zenodo PRODUCTION depositions.")
    
    try:
        # Get the deposition IDs from the task's parent
        book_deposition_id = task.screening.book_deposition_id
        data_deposition_id = task.screening.data_deposition_id
        repo_deposition_id = task.screening.repo_deposition_id
        docker_deposition_id = task.screening.docker_deposition_id
        
        # Initialize results
        results = {
            'book_deleted': False,
            'data_deleted': False,
            'repo_deleted': False,
            'docker_deleted': False
        }
        
        # Delete the book deposition if it exists
        if book_deposition_id:
            task.start(f"Deleting book deposition: {book_deposition_id}")
            book_response = zenodo_delete_deposition(server, token, book_deposition_id)
            results['book_deleted'] = book_response.status_code == 204
            if not results['book_deleted']:
                task.start(f"⚠️ Failed to delete book deposition: {book_response.text}")
        
        # Delete the data deposition if it exists
        if data_deposition_id:
            task.start(f"Deleting data deposition: {data_deposition_id}")
            data_response = zenodo_delete_deposition(server, token, data_deposition_id)
            results['data_deleted'] = data_response.status_code == 204
            if not results['data_deleted']:
                task.start(f"⚠️ Failed to delete data deposition: {data_response.text}")
        
        # Delete the repository deposition if it exists
        if repo_deposition_id:
            task.start(f"Deleting repository deposition: {repo_deposition_id}")
            repo_response = zenodo_delete_deposition(server, token, repo_deposition_id)
            results['repo_deleted'] = repo_response.status_code == 204
            if not results['repo_deleted']:
                task.start(f"⚠️ Failed to delete repository deposition: {repo_response.text}")
        
        # Delete the docker deposition if it exists
        if docker_deposition_id:
            task.start(f"Deleting docker deposition: {docker_deposition_id}")
            docker_response = zenodo_delete_deposition(server, token, docker_deposition_id)
            results['docker_deleted'] = docker_response.status_code == 204
            if not results['docker_deleted']:
                task.start(f"⚠️ Failed to delete docker deposition: {docker_response.text}")
        
        # Update the task state with the results
        self.update_state(
            state=states.SUCCESS,
            meta=results
        )
        
        # Log the result
        logging.info(f"Flushed Zenodo depositions for {task.owner_name}/{task.repo_name}: {results}")
        
        # Succeed the task
        if is_sandbox:
            task.succeed(f"🏖️ Flushed Zenodo SANDBOX depositions for {task.owner_name}/{task.repo_name}.")
        else:
            task.succeed(f"🏭 Flushed Zenodo PRODUCTION depositions for {task.owner_name}/{task.repo_name}.")
        
        return results
        
    except Exception as e:
        logging.exception(f"Error in zenodo_flush_task: {str(e)}")
        task.fail(f"⛔️ Error flushing Zenodo depositions: {str(e)}")
        raise 