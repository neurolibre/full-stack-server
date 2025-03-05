"""
Rsync MyST Production Task

Task for synchronizing MyST content to the production server.
"""

import os
import subprocess
import requests
from celery import states
from ..app import celery_app, DATA_ROOT_PATH, DOI_PREFIX, DOI_SUFFIX, PREVIEW_SERVER, PREPRINT_SERVER
from ..base import BaseNeuroLibreTask

@celery_app.task(bind=True)
def rsync_myst_prod_task(self, screening_dict):
    """
    DOI-formatted myst html files are synced to the production server.
    
    Args:
        screening_dict: Dictionary containing screening information
        
    Returns:
        A message indicating the task is complete
    """
    task = BaseNeuroLibreTask(self, screening_dict)
    task.start("🔄 Syncing MyST build to production server.")
    
    expected_myst_url = f"{PREVIEW_SERVER}/{DOI_PREFIX}/{DOI_SUFFIX}.{task.screening.issue_id:05d}"
    response = requests.get(expected_myst_url)
    
    if response.status_code == 200:
        remote_path = os.path.join("neurolibre-preview:", DATA_ROOT_PATH[1:], DOI_PREFIX, f"{DOI_SUFFIX}.{task.screening.issue_id:05d}" + "*")
        process = subprocess.Popen(["/usr/bin/rsync", "-avzR", remote_path, "/"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = process.communicate()[0]
        ret = process.wait()
        
        if ret == 0:
            task.succeed(f"🌺 MyST build synced to production server: {PREPRINT_SERVER}/{DOI_PREFIX}/{DOI_SUFFIX}.{task.screening.issue_id:05d}", False)
        else:
            task.fail(f"⛔️ Failed to sync MyST build to production server: {output}")
    else:
        task.fail(f"⛔️ Production MyST build not found on the preview server {expected_myst_url} \n {response.text}") 