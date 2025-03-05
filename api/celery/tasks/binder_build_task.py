"""
Binder Build Task

Task for building a Binder image.
"""

import logging
import requests
import json
from api.celery.app import celery_app, celery_config
from api.celery.base import BaseNeuroLibreTask
from common import write_log

@celery_app.task(bind=True, soft_time_limit=3600, time_limit=3600)
def binder_build_task(self, screening_dict):
    """
    Build a Binder image.
    
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
    
    # Determine if we're using the original repository or the forked one
    is_prod = task.screening.is_prod
    if is_prod:
        # Use the forked repository
        owner_name = celery_config['GH_ORGANIZATION']
        task.start(f"🏭 Building Binder image for PRODUCTION repository: {owner_name}/{task.repo_name}")
    else:
        # Use the original repository
        owner_name = task.owner_name
        task.start(f"🔎 Building Binder image for PREVIEW repository: {owner_name}/{task.repo_name}")
    
    # Get the commit hash
    commit_hash = task.screening.commit_hash
    if not commit_hash:
        task.fail("⛔️ No commit hash provided.")
        return
    
    # Construct the Binder URL
    binder_url = f"{celery_config['PREVIEW_BINDERHUB']}/build/gh/{owner_name}/{task.repo_name}/{commit_hash}"
    
    task.start(f"Requesting Binder build at: {binder_url}")
    all_logs += f"\n Requesting Binder build at: {binder_url}"
    
    try:
        # Request the Binder build
        response = requests.get(binder_url, stream=True)
        
        # Process the streaming response
        build_logs = []
        phase = None
        for line in response.iter_lines():
            if line:
                line_str = line.decode('utf-8')
                if line_str.startswith('data:'):
                    try:
                        data = json.loads(line_str[5:])
                        if 'message' in data:
                            build_logs.append(data['message'])
                            all_logs += f"\n {data['message']}"
                        if 'phase' in data:
                            phase = data['phase']
                            task.start(f"Binder build phase: {phase}")
                            all_logs += f"\n Binder build phase: {phase}"
                    except json.JSONDecodeError:
                        build_logs.append(line_str[5:])
                        all_logs += f"\n {line_str[5:]}"
        
        # Check if the build was successful
        if phase == "ready":
            # Write the logs
            log_path = write_log(task.owner_name, task.repo_name, "binder", all_logs, all_logs_dict)
            
            # Succeed the task
            if is_prod:
                task.succeed(f"🏭 PRODUCTION 🏭 | 🐳 Binder image built successfully for {owner_name}/{task.repo_name} at commit {commit_hash}. \n\n > [!IMPORTANT] \n > Remember to take a look at the [**build logs**]({celery_config['PREVIEW_BINDERHUB']}/api/logs/{log_path}) to check if the build was successful.")
            else:
                task.succeed(f"🔎 PREVIEW 🔎 | 🐳 Binder image built successfully for {owner_name}/{task.repo_name} at commit {commit_hash}. \n\n > [!IMPORTANT] \n > Remember to take a look at the [**build logs**]({celery_config['PREVIEW_BINDERHUB']}/api/logs/{log_path}) to check if the build was successful.")
                task.email_user(
                    f"""🔎 PREVIEW 🔎 | 🐳 Binder image built successfully for {owner_name}/{task.repo_name} at commit {commit_hash}.<br><br>
                    👋 Remember to take a look at the <a href="{celery_config['PREVIEW_BINDERHUB']}/api/logs/{log_path}">build logs</a> to check if the build was successful.""")
            
            return {'phase': phase, 'logs': build_logs}
        else:
            # Write the logs
            log_path = write_log(task.owner_name, task.repo_name, "binder", all_logs, all_logs_dict)
            
            # Fail the task
            task.fail(f"⛔️ Binder build failed for {owner_name}/{task.repo_name} at commit {commit_hash}. Phase: {phase} \n\n > [!CAUTION] \n > Please take a look at the [**build logs**]({celery_config['PREVIEW_BINDERHUB']}/api/logs/{log_path}) to locate the error.")
            task.email_user(f"⛔️ Binder build failed for {owner_name}/{task.repo_name} at commit {commit_hash}. Phase: {phase} \n\n > [!CAUTION] \n > Please take a look at the <a href='{celery_config['PREVIEW_BINDERHUB']}/api/logs/{log_path}'>build logs</a> to locate the error.")
            
            return {'phase': phase, 'logs': build_logs}
        
    except Exception as e:
        logging.exception(f"Error in binder_build_task: {str(e)}")
        task.fail(f"⛔️ Error building Binder image: {str(e)}")
        return 