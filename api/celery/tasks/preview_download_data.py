"""
Preview Download Data Task

Task for downloading data for a preview.
"""

import os
import logging
import requests
from api.celery.app import celery_app, celery_config
from api.celery.base import BaseNeuroLibreTask
from common import write_log

@celery_app.task(bind=True, soft_time_limit=600, time_limit=1000)
def preview_download_data(self, screening_dict):
    """
    Download data for a preview.
    
    Args:
        screening_dict: Dictionary containing screening information
        
    Returns:
        A message indicating the task is complete
    """
    all_logs = ""
    all_logs_dict = {}
    
    task = BaseNeuroLibreTask(self, screening_dict)
    
    all_logs_dict["task_id"] = task.task_id
    all_logs_dict["github_issue_id"] = task.screening.issue_id
    all_logs_dict["owner_name"] = task.owner_name
    all_logs_dict["repo_name"] = task.repo_name
    
    task.start("🔎 Initiating data download.")
    task.email_user(f"""PREVIEW data download for {task.owner_name}/{task.repo_name} has been started. <br>
                        Task ID: {task.task_id} <br>
                        Commit hash: {task.screening.commit_hash} <br>
                        Binder hash: {task.screening.binder_hash}""")
    
    try:
        # Get the data URL from the screening dictionary
        data_url = task.screening.data_url
        if not data_url:
            task.fail("⛔️ No data URL provided.")
            task.email_user("⛔️ No data URL provided.")
            return
        
        task.start(f"Downloading data from {data_url}")
        all_logs += f"\n Downloading data from {data_url}"
        
        # Create the data directory if it doesn't exist
        data_dir = os.path.join(celery_config['DATA_ROOT_PATH'], "data", task.owner_name, task.repo_name)
        os.makedirs(data_dir, exist_ok=True)
        
        # Download the data
        response = requests.get(data_url, stream=True)
        if response.status_code != 200:
            task.fail(f"⛔️ Failed to download data from {data_url}. Status code: {response.status_code}")
            task.email_user(f"⛔️ Failed to download data from {data_url}. Status code: {response.status_code}")
            return
        
        # Save the data to a file
        file_name = data_url.split("/")[-1]
        file_path = os.path.join(data_dir, file_name)
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        
        task.start(f"Data downloaded to {file_path}")
        all_logs += f"\n Data downloaded to {file_path}"
        
        # Write the log
        log_path = write_log(task.owner_name, task.repo_name, "data_download", all_logs, all_logs_dict)
        
        # Succeed the task
        task.succeed(f"🧐 PREVIEW 🧐 | 📦 Data download has been completed! \n\n * 📂 Data saved to {file_path} \n\n > [!IMPORTANT] \n > Remember to take a look at the [**download logs**]({celery_config['PREVIEW_SERVER']}/api/logs/{log_path}) to check if the data was downloaded successfully.")
        task.email_user(
            f"""🧐 PREVIEW 🧐 | 📦 Data download has been completed! 📦<br><br>
            📂 Data saved to {file_path}<br><br>
            👋 Remember to take a look at the <a href="{celery_config['PREVIEW_SERVER']}/api/logs/{log_path}">download logs</a> to check if the data was downloaded successfully.""")
        
    except Exception as e:
        logging.exception(f"Error in preview_download_data: {str(e)}")
        task.fail(f"⛔️ Error downloading data: {str(e)}")
        task.email_user(f"⛔️ Error downloading data: {str(e)}")
        return 