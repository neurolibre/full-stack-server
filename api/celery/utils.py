"""
Utility Functions

This module contains utility functions used by multiple Celery tasks.
"""

import os
import time
import json
import subprocess
import shutil
import tempfile
import zipfile
import functools
import logging
import fnmatch
from celery.exceptions import Ignore, SoftTimeLimitExceeded, TimeoutError
from celery import states
from github import Github
from .app import JOURNAL_NAME

def handle_soft_timeout(func):
    """
    Decorator to handle SoftTimeLimitExceeded and TimeoutError exceptions for Celery tasks.
    
    This decorator wraps a Celery task function and catches timeout exceptions,
    updating the task state and raising Ignore to prevent the task from being retried.
    """
    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        try:
            return func(self, *args, **kwargs)
        except (SoftTimeLimitExceeded, TimeoutError) as e:
            error_message = f"Task timed out after reaching the soft time limit: {str(e)}"
            logging.error(error_message)
            self.update_state(
                state=states.FAILURE,
                meta={
                    'exc_type': f"{JOURNAL_NAME} celery exception",
                    'exc_message': "Task timed out",
                    'message': error_message
                }
            )
            raise Ignore()
    return wrapper

def fast_copytree(src, dst):
    """
    Quickly copy a directory tree using zip compression.
    
    Args:
        src: Source directory path
        dst: Destination directory path
    """
    # Create a temporary directory for the zip file
    with tempfile.TemporaryDirectory() as tmpdir:
        zip_path = os.path.join(tmpdir, 'archive.zip')
        
        # Zip the source directory
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(src, os.path.basename(src))
            for root, _, files in os.walk(src):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, src)
                    zipf.write(file_path, arcname)
        
        # Copy the zip file to the destination
        dst_zip = os.path.join(dst, 'archive.zip')
        shutil.copy2(zip_path, dst_zip)
        
        # Extract the zip file at the destination
        with zipfile.ZipFile(dst_zip, 'r') as zipf:
            zipf.extractall(dst)
        
        # Remove the zip file from the destination
        os.remove(dst_zip)

def run_celery_subprocess(cmd, cwd=None, env=None):
    """
    Run a subprocess command and return the return code and output.
    
    Args:
        cmd: Command to run as a list of strings
        cwd: Current working directory
        env: Environment variables
        
    Returns:
        Tuple of (return_code, output)
    """
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        cwd=cwd,
        env=env,
        text=True
    )
    output = process.communicate()[0]
    return_code = process.wait()
    return return_code, output

def gh_template_respond(github_client, state, task_title, repo, issue_id, task_id, comment_id, message):
    """
    Respond to a GitHub issue with a template.
    
    Args:
        github_client: GitHub client
        state: State of the task (started, success, failure)
        task_title: Title of the task
        repo: Repository name
        issue_id: Issue ID
        task_id: Task ID
        comment_id: Comment ID
        message: Message to include in the response
    """
    repo_obj = github_client.get_repo(repo)
    issue = repo_obj.get_issue(number=issue_id)
    
    if state == "started":
        body = f"## {task_title}\n\n**Status**: Started\n\n**Task ID**: {task_id}\n\n{message}"
    elif state == "success":
        body = f"## {task_title}\n\n**Status**: Success ✅\n\n**Task ID**: {task_id}\n\n{message}"
    elif state == "failure":
        body = f"## {task_title}\n\n**Status**: Failed ❌\n\n**Task ID**: {task_id}\n\n{message}"
    else:
        body = f"## {task_title}\n\n**Status**: {state}\n\n**Task ID**: {task_id}\n\n{message}"
    
    issue.create_comment(body)

def send_email_celery(to_email, subject, body):
    """
    Send an email using the Celery task.
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        body: Email body
    """
    # Import here to avoid circular imports
    from common import send_email
    send_email(to_email, subject, body)

def send_email_with_html_attachment_celery(to_email, subject, body, attachment_path):
    """
    Send an email with an HTML attachment using the Celery task.
    
    Args:
        to_email: Recipient email address
        subject: Email subject
        body: Email body
        attachment_path: Path to the HTML attachment
    """
    # Import here to avoid circular imports
    from common import send_email_with_html_attachment
    send_email_with_html_attachment(to_email, subject, body, attachment_path)

def write_html_log(commit_sha, logs):
    """
    Write logs to an HTML file.
    
    Args:
        commit_sha: Commit SHA
        logs: Logs to write
        
    Returns:
        Path to the HTML file
    """
    log_dir = os.path.join(os.getcwd(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, f"{commit_sha}.html")
    with open(log_file, "w") as f:
        f.write(f"<pre>{logs}</pre>")
    
    return log_file

def truncate_for_github_comment(message, items_to_add=None, max_length=60000):
    """
    Truncate a message to fit within GitHub comment length limits.
    
    Args:
        message: Message to truncate
        items_to_add: Additional items to add to the message
        max_length: Maximum length of the message
        
    Returns:
        Truncated message
    """
    if items_to_add is None:
        items_to_add = []
    
    # Calculate the length of the items to add
    items_length = sum(len(item) for item in items_to_add)
    
    # Calculate the maximum length for the message
    max_message_length = max_length - items_length
    
    # Truncate the message if necessary
    if len(message) > max_message_length:
        truncated_message = message[:max_message_length - 100]
        truncated_message += "\n\n... [message truncated due to GitHub comment length limits] ...\n\n"
        return truncated_message + "".join(items_to_add)
    
    return message + "".join(items_to_add)

def clean_garbage_files(source_path, unwanted_dirs=None, unwanted_files=None):
    """
    Clean unwanted files and directories from a path.
    
    Args:
        source_path: Path to clean
        unwanted_dirs: List of unwanted directory patterns
        unwanted_files: List of unwanted file patterns
    """
    if unwanted_dirs is None:
        unwanted_dirs = ['.git', '__pycache__', '.ipynb_checkpoints']
    
    if unwanted_files is None:
        unwanted_files = ['.DS_Store', '.gitignore', '*.pyc']
    
    # Walk through the directory tree
    for root, dirs, files in os.walk(source_path, topdown=True):
        # Remove unwanted directories
        dirs[:] = [d for d in dirs if not any(fnmatch.fnmatch(d, pattern) for pattern in unwanted_dirs)]
        
        # Remove unwanted files
        for file in files:
            if any(fnmatch.fnmatch(file, pattern) for pattern in unwanted_files):
                file_path = os.path.join(root, file)
                try:
                    os.remove(file_path)
                except Exception as e:
                    logging.warning(f"Failed to remove file {file_path}: {e}")

def local_to_nfs(source_path, dest_path):
    """
    Copy files from local path to NFS path.
    
    Args:
        source_path: Source path
        dest_path: Destination path
    """
    # Create destination directory if it doesn't exist
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
    
    # Copy the file or directory
    if os.path.isfile(source_path):
        shutil.copy2(source_path, dest_path)
    else:
        if os.path.exists(dest_path):
            shutil.rmtree(dest_path)
        shutil.copytree(source_path, dest_path) 

def cleanup_hub(hub):
    """Helper function to clean up JupyterHub resources"""
    if hub:
        logging.info(f"Stopping container {hub.container.short_id}")
        hub.stop_container()
        logging.info("Removing stopped containers.")
        hub.delete_stopped_containers() 
        logging.info("Cleanup successful...")