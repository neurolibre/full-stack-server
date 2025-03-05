"""
Fork Configure Repository Task

Task for forking and configuring a repository.
"""
import logging
import time
from celery import states
from github import Github
from api.celery.app import celery_app, celery_config, get_github_bot_token
from api.celery.base import BaseNeuroLibreTask


@celery_app.task(bind=True, soft_time_limit=600, time_limit=1000)
def fork_configure_repository_task(self, screening_dict):
    """
    Fork and configure a repository.
    
    Args:
        screening_dict: Dictionary containing screening information
        
    Returns:
        A message indicating the task is complete
    """
    task = BaseNeuroLibreTask(self, screening_dict)
    
    # Get the repository URL
    repo_url = task.screening.target_repo_url
    if not repo_url:
        task.fail("⛔️ No repository URL provided.")
        return
    
    # Extract owner and repo name from the URL
    # Assuming URL format: https://github.com/owner/repo
    parts = repo_url.split('/')
    source_owner = parts[-2]
    repo_name = parts[-1]
    
    task.start(f"🍴 Forking repository {source_owner}/{repo_name} to {celery_config['GH_ORGANIZATION']}/{repo_name}")
    
    try:
        # Initialize GitHub client
        g = Github(get_github_bot_token())
        
        # Get the source repository
        source_repo = g.get_repo(f"{source_owner}/{repo_name}")
        
        # Check if the repository already exists in the organization
        try:
            org_repo = g.get_repo(f"{celery_config['GH_ORGANIZATION']}/{repo_name}")
            task.start(f"Repository {celery_config['GH_ORGANIZATION']}/{repo_name} already exists, skipping fork.")
        except Exception:
            # Fork the repository to the organization
            org = g.get_organization(celery_config['GH_ORGANIZATION'])
            org_repo = org.create_fork(source_repo)
            task.start(f"Repository {source_owner}/{repo_name} forked to {celery_config['GH_ORGANIZATION']}/{repo_name}")
            
            # Wait for the fork to be ready
            time.sleep(5)
        
        # Configure the repository
        task.start(f"Configuring repository {celery_config['GH_ORGANIZATION']}/{repo_name}")
        
        # Enable GitHub Pages
        try:
            # Check if GitHub Pages is already enabled
            pages = org_repo.get_pages()
            task.start(f"GitHub Pages already enabled for {celery_config['GH_ORGANIZATION']}/{repo_name}")
        except Exception:
            # Enable GitHub Pages
            org_repo.create_pages(source="gh-pages", path="/")
            task.start(f"GitHub Pages enabled for {celery_config['GH_ORGANIZATION']}/{repo_name}")
        
        # Update the task state
        self.update_state(
            state=states.SUCCESS,
            meta={
                'forked_repo_url': org_repo.html_url
            }
        )
        
        # Log the result
        logging.info(f"Forked and configured repository {source_owner}/{repo_name} to {celery_config['GH_ORGANIZATION']}/{repo_name}")
        
        # Succeed the task
        task.succeed(f"🍴 Repository {source_owner}/{repo_name} forked and configured to {celery_config['GH_ORGANIZATION']}/{repo_name}")
        
        return {'forked_repo_url': org_repo.html_url}
        
    except Exception as e:
        logging.exception(f"Error in fork_configure_repository_task: {str(e)}")
        task.fail(f"⛔️ Error forking and configuring repository: {str(e)}")
        raise 