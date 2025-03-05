"""
Zenodo Publish Task

Task for publishing Zenodo depositions.
"""

import os
import logging
import requests
from celery import states
from ..app import celery_app, ZENODO_SERVER, ZENODO_TOKEN, ZENODO_SANDBOX_SERVER, ZENODO_SANDBOX_TOKEN
from ..base import BaseNeuroLibreTask
from ..utils import zenodo_publish_deposition

@celery_app.task(bind=True, soft_time_limit=600, time_limit=1000)
def zenodo_publish_task(self, screening_dict):
    """
    Publish Zenodo depositions.
    
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
        task.start("🏖️ Publishing Zenodo SANDBOX depositions.")
    else:
        server = ZENODO_SERVER
        token = ZENODO_TOKEN
        task.start("🏭 Publishing Zenodo PRODUCTION depositions.")
    
    try:
        # Get the deposition IDs from the task's parent
        book_deposition_id = task.screening.book_deposition_id
        data_deposition_id = task.screening.data_deposition_id
        repo_deposition_id = task.screening.repo_deposition_id
        docker_deposition_id = task.screening.docker_deposition_id
        
        if not all([book_deposition_id, data_deposition_id, repo_deposition_id, docker_deposition_id]):
            task.fail("⛔️ Missing deposition IDs.")
            return
        
        # Publish the book deposition
        task.start(f"Publishing book deposition: {book_deposition_id}")
        book_response = zenodo_publish_deposition(server, token, book_deposition_id)
        
        if book_response.status_code != 202:
            task.fail(f"⛔️ Failed to publish book deposition: {book_response.text}")
            return
        
        # Publish the data deposition
        task.start(f"Publishing data deposition: {data_deposition_id}")
        data_response = zenodo_publish_deposition(server, token, data_deposition_id)
        
        if data_response.status_code != 202:
            task.fail(f"⛔️ Failed to publish data deposition: {data_response.text}")
            return
        
        # Publish the repository deposition
        task.start(f"Publishing repository deposition: {repo_deposition_id}")
        repo_response = zenodo_publish_deposition(server, token, repo_deposition_id)
        
        if repo_response.status_code != 202:
            task.fail(f"⛔️ Failed to publish repository deposition: {repo_response.text}")
            return
        
        # Publish the docker deposition
        task.start(f"Publishing docker deposition: {docker_deposition_id}")
        docker_response = zenodo_publish_deposition(server, token, docker_deposition_id)
        
        if docker_response.status_code != 202:
            task.fail(f"⛔️ Failed to publish docker deposition: {docker_response.text}")
            return
        
        # Get the DOIs from the responses
        book_doi = book_response.json().get("doi")
        data_doi = data_response.json().get("doi")
        repo_doi = repo_response.json().get("doi")
        docker_doi = docker_response.json().get("doi")
        
        # Update the task state with the DOIs
        self.update_state(
            state=states.SUCCESS,
            meta={
                'book_doi': book_doi,
                'data_doi': data_doi,
                'repo_doi': repo_doi,
                'docker_doi': docker_doi
            }
        )
        
        # Log the result
        logging.info(f"Published Zenodo depositions for {task.owner_name}/{task.repo_name}: {book_doi}, {data_doi}, {repo_doi}, {docker_doi}")
        
        # Succeed the task
        if is_sandbox:
            task.succeed(f"🏖️ Published Zenodo SANDBOX depositions for {task.owner_name}/{task.repo_name}.")
        else:
            task.succeed(f"🏭 Published Zenodo PRODUCTION depositions for {task.owner_name}/{task.repo_name}.")
        
        return {
            'book_doi': book_doi,
            'data_doi': data_doi,
            'repo_doi': repo_doi,
            'docker_doi': docker_doi
        }
        
    except Exception as e:
        logging.exception(f"Error in zenodo_publish_task: {str(e)}")
        task.fail(f"⛔️ Error publishing Zenodo depositions: {str(e)}")
        raise 