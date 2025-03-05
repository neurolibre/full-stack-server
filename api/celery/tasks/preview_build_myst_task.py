"""
Preview Build MyST Task

Task for building a MyST preview.
"""

import os
import logging
import tarfile
import shutil
from api.celery.app import celery_app, celery_config
from api.celery.base import BaseNeuroLibreTask
from api.celery.utils import handle_soft_timeout, cleanup_hub
from common import write_log, get_active_ports, close_port, format_commit_hash
from github_client import gh_forkify_it, gh_read_from_issue_body
from myst_libre.tools import JupyterHubLocalSpawner
from myst_libre.rees import REES
from myst_libre.builders import MystBuilder

@celery_app.task(bind=True, soft_time_limit=600, time_limit=1000)
@handle_soft_timeout
def preview_build_myst_task(self, screening_dict):
    """
    Build a MyST preview.
    
    Args:
        screening_dict: Dictionary containing screening information
        
    Returns:
        A message indicating the task is complete
    """
    all_logs = ""
    all_logs_dict = {}

    task = BaseNeuroLibreTask(self, screening_dict)
    is_prod = task.screening.is_prod
    noexec = False

    all_logs_dict["task_id"] = task.task_id
    all_logs_dict["github_issue_id"] = task.screening.issue_id
    all_logs_dict["owner_name"] = task.owner_name
    all_logs_dict["repo_name"] = task.repo_name
    all_logs_dict["is_prod"] = is_prod

    # No docker archive signals no user-defined runtime.
    if task.screening.issue_id is not None:
        docker_archive_value = gh_read_from_issue_body(task.screening.github_client, celery_config['REVIEW_REPOSITORY'], task.screening.issue_id, "docker-archive")
        if docker_archive_value == "N/A":
            noexec = True

    noexec = True if task.screening.binder_hash in ["noexec"] else False
    
    original_owner = task.owner_name
    if is_prod:
        task.start("⚡️ Initiating PRODUCTION MyST build.")
        # Transform the target repo URL to point to the forked version.
        task.screening.target_repo_url = gh_forkify_it(task.screening.target_repo_url)
        task.owner_name = celery_config['GH_ORGANIZATION']
        # Enforce the latest commit
        task.screening.commit_hash = format_commit_hash(task.screening.target_repo_url, "HEAD")
        # Enforce latest binder image
        task.screening.binder_hash = None
        base_url = os.path.join("/", celery_config['DOI_PREFIX'], f"{celery_config['DOI_SUFFIX']}.{task.screening.issue_id:05d}")
        prod_path = os.path.join(celery_config['DATA_ROOT_PATH'], celery_config['DOI_PREFIX'], f"{celery_config['DOI_SUFFIX']}.{task.screening.issue_id:05d}")
        os.makedirs(prod_path, exist_ok=True)
    else:
        task.start("🔎 Initiating PREVIEW MyST build.")
        task.email_user(f"""PREVIEW MyST build for {task.owner_name}/{task.repo_name} has been started. <br>
                            Task ID: {task.task_id} <br>
                            Commit hash: {task.screening.commit_hash} <br>
                            Binder hash: {task.screening.binder_hash}""")
        task.screening.commit_hash = format_commit_hash(task.screening.target_repo_url, "HEAD") if task.screening.commit_hash in [None, "latest"] else task.screening.commit_hash
        base_url = os.path.join("/", celery_config['MYST_FOLDER'], task.owner_name, task.repo_name, task.screening.commit_hash, "_build", "html")
    hub = None

    if noexec:
        # Base runtime.
        task.screening.binder_hash = celery_config['NOEXEC_CONTAINER_COMMIT_HASH']
    else:
        # User defined runtime.
        task.screening.binder_hash = format_commit_hash(task.screening.target_repo_url, "HEAD") if task.screening.binder_hash in [None, "latest"] else task.screening.binder_hash

    if noexec:
        # Overrides build image to the base
        binder_image_name = celery_config['NOEXEC_CONTAINER_REPOSITORY']
    else:
        # Falls back to the repo name to look for the image. 
        binder_image_name = None

    all_logs_dict["commit_hash"] = task.screening.commit_hash
    all_logs_dict["binder_hash"] = task.screening.binder_hash
    all_logs_dict["binder_image_name"] = binder_image_name

    try:
        rees_resources = REES(dict(
            registry_url=celery_config['BINDER_REGISTRY'],
            gh_user_repo_name = f"{task.owner_name}/{task.repo_name}",
            gh_repo_commit_hash = task.screening.commit_hash,
            binder_image_tag = task.screening.binder_hash,
            binder_image_name = binder_image_name,
            dotenv = task.get_dotenv_path()))      

        if rees_resources.search_img_by_repo_name():
            logging.info(f"🐳 FOUND IMAGE... ⬇️ PULLING {rees_resources.found_image_name}")
            all_logs += f"\n 🐳 FOUND IMAGE... ⬇️ PULLING {rees_resources.found_image_name}"
            rees_resources.pull_image()
        else:
            if (not noexec) and is_prod:
                task.fail(f"🚨 Ensure a successful binderhub build before production MyST build for {task.owner_name}/{task.repo_name}.")
                task.email_user(f"🚨 Ensure a successful binderhub build before production MyST build for {task.owner_name}/{task.repo_name}. See more at {PREVIEW_BINDERHUB}")
                logging.error(f"⛔️ NOT FOUND - A docker image was not found for {task.owner_name}/{task.repo_name} at {task.screening.commit_hash}")
        
        hub = JupyterHubLocalSpawner(rees_resources,
                                host_build_source_parent_dir = task.join_myst_path(),
                                container_build_source_mount_dir = celery_config['CONTAINER_MYST_SOURCE_PATH'], #default
                                host_data_parent_dir = celery_config['DATA_ROOT_PATH'], #optional
                                container_data_mount_dir = celery_config['CONTAINER_MYST_DATA_PATH'])

        task.start("Cloning repository, pulling binder image, spawning JupyterHub...")
        hub_logs = hub.spawn_jupyter_hub()
        all_logs += ''.join(hub_logs)

        expected_source_path = task.join_myst_path(task.owner_name, task.repo_name, task.screening.commit_hash)
        if os.path.exists(expected_source_path) and os.listdir(expected_source_path):
            task.start("🎉 Successfully cloned the repository.")
        else:
            task.fail(f"⛔️ Source repository {task.owner_name}/{task.repo_name} at {task.screening.commit_hash} not found.")
            task.email_user(f"⛔️ Source repository {task.owner_name}/{task.repo_name} at {task.screening.commit_hash} not found.")
        
        # Initialize the builder
        task.start("Warming up the myst builder...")   
        builder = MystBuilder(hub=hub)

        # This will use exec cache both for preview and production.
        base_user_dir = os.path.join(celery_config['DATA_ROOT_PATH'], celery_config['MYST_FOLDER'], original_owner, task.repo_name)
        latest_file_user = os.path.join(base_user_dir, "latest.txt")

        latest_file_prod = None
        base_prod_dir = None
        if is_prod:
            base_prod_dir = os.path.join(celery_config['DATA_ROOT_PATH'], celery_config['MYST_FOLDER'], task.owner_name, task.repo_name)
            latest_file_prod = os.path.join(base_prod_dir, "latest.txt")

        if is_prod and os.path.exists(latest_file_prod):
            latest_file = latest_file_prod
        else:
            latest_file = latest_file_user

        previous_commit = None
        if os.path.exists(latest_file):
            logging.info(f"✔️ Found latest.txt at {base_user_dir}")
            all_logs += f"\n ✔️ Found latest.txt at {base_user_dir}"
            with open(latest_file, 'r') as f:
                previous_commit = f.read().strip()
            all_logs += f"\n ✔️ Found previous build at commit {previous_commit}"

        logging.info(f"💾 Cache will be loaded from commit: {previous_commit}")
        all_logs += f"\n 💾 Cache will be loaded from commit: {previous_commit}"
        logging.info(f" -- Current commit: {task.screening.commit_hash}")
        all_logs += f"\n -- Current commit: {task.screening.commit_hash}"
        
        # Copy previous build folder to the new build folder to take advantage of caching.
        if previous_commit and (previous_commit != task.screening.commit_hash):
            previous_execute_dir = task.join_myst_path(base_user_dir, previous_commit, "_build")
            if is_prod:
                current_build_dir = task.join_myst_path(base_prod_dir, task.screening.commit_hash, "_build")
            else:
                current_build_dir = task.join_myst_path(base_user_dir, task.screening.commit_hash, "_build")

            if os.path.isdir(previous_execute_dir):
                task.start(f"♻️ Copying _build folder from previous build {previous_commit}")
                all_logs += f"\n ♻️ Copying _build folder from previous build {previous_commit}"
                try:
                    shutil.copytree(previous_execute_dir, current_build_dir)
                    task.start("✔️ Successfully copied previous build folder")
                    all_logs += f"\n ✔️ Successfully copied previous build folder"  
                except Exception as e:
                    task.start(f"⚠️ Warning: Failed to copy previous build folder: {str(e)}")
                    all_logs += f"\n ⚠️ Warning: Failed to copy previous build folder: {str(e)}"

        builder.setenv('BASE_URL', base_url)
        # builder.setenv('CONTENT_CDN_PORT', "3102")

        active_ports_before = get_active_ports()

        task.start(f"Issuing MyST build command, execution environment: {rees_resources.found_image_name}")

        myst_logs = builder.build('--execute', '--html', user="ubuntu", group="ubuntu")
        all_logs += f"\n {myst_logs}"

        active_ports_after = get_active_ports()

        new_active_ports = set(active_ports_after) - set(active_ports_before)
        logging.info(f"New active ports: {new_active_ports}")

        for port in new_active_ports:
            close_port(port)

        expected_webpage_path = task.join_myst_path(task.owner_name, task.repo_name, task.screening.commit_hash, "_build", "html", "index.html")
        if os.path.exists(expected_webpage_path):
            source_dir = task.join_myst_path(task.owner_name, task.repo_name, task.screening.commit_hash)
            archive_path = f"{source_dir}.tar.gz"
    
            try:
                source_dir = task.join_myst_path(task.owner_name, task.repo_name, task.screening.commit_hash)
                archive_path = f"{source_dir}.tar.gz"
                with tarfile.open(archive_path, "w:gz") as tar:
                    tar.add(source_dir, arcname=os.path.basename(source_dir))
                task.start(f"Created archive at {archive_path}")
                all_logs += f"\n ✔️ Created archive at {archive_path}"

                if is_prod:
                    latest_file_write = os.path.join(base_prod_dir, "latest.txt")
                else:
                    latest_file_write = os.path.join(base_user_dir, "latest.txt")

                with open(latest_file_write, 'w') as f:
                    f.write(task.screening.commit_hash)
                task.start(f"Updated latest.txt to {task.screening.commit_hash}")
                all_logs += f"\n ✔️ Updated latest.txt to {task.screening.commit_hash}"

                if is_prod:
                    html_source = task.join_myst_path(task.owner_name, task.repo_name, task.screening.commit_hash, "_build", "html")
                    temp_archive = os.path.join(prod_path, "temp.tar.gz")
                    try:
                        # Create tar archive
                        with tarfile.open(temp_archive, "w:gz") as tar:
                            tar.add(html_source, arcname=".")

                        # Extract archive
                        with tarfile.open(temp_archive, "r:gz") as tar:
                            tar.extractall(prod_path)

                        task.start(f"Copied HTML contents to production path at {prod_path}")
                        all_logs += f"\n ✔️ Copied HTML contents to production path at {prod_path}"
                    finally:
                        # Clean up temp archive
                        if os.path.exists(temp_archive):
                            os.remove(temp_archive)                    
                
            except Exception as e:
                task.start(f"Warning: Failed to create archive/update latest: {str(e)}")
                all_logs += f"\n ⚠️ Warning: Failed to create archive/update latest: {str(e)}"
            
            log_path = write_log(task.owner_name, task.repo_name, "myst", all_logs, all_logs_dict)
            if is_prod:
                task.succeed(f"🚀 PRODUCTION 🚀 | 🌺 MyST build has been completed! \n\n * 🔗 [Built webpage]({celery_config['PREVIEW_SERVER']}/{celery_config['DOI_PREFIX']}/{celery_config['DOI_SUFFIX']}.{task.screening.issue_id:05d}) \n\n > [!IMPORTANT] \n > Remember to take a look at the [**build logs**]({celery_config['PREVIEW_SERVER']}/api/logs/{log_path}) to check if all the notebooks have been executed successfully, as well as other warnings and errors from the MyST build.", collapsable=False)
            else:
                task.succeed(f"🧐 PREVIEW 🧐 | 🌺 MyST build has been completed! \n\n * 🔗 [Built webpage]({celery_config['PREVIEW_SERVER']}/myst/{task.owner_name}/{task.repo_name}/{task.screening.commit_hash}/_build/html/index.html) \n\n > [!IMPORTANT] \n > Remember to take a look at the [**build logs**]({celery_config['PREVIEW_SERVER']}/api/logs/{log_path}) to check if all the notebooks have been executed successfully, as well as other warnings and errors from the MyST build.", collapsable=False)
                task.email_user(
                    f"""🧐 PREVIEW 🧐 | 🌺 MyST build has been completed! 🌺<br><br>
                    🌱 Click <a href="{celery_config['PREVIEW_SERVER']}/myst/{task.owner_name}/{task.repo_name}/{task.screening.commit_hash}/_build/html/index.html">here</a> to view the latest version of your living preprint.<br><br>
                    👋 Remember to take a look at the <a href="{celery_config['PREVIEW_SERVER']}/api/logs/{log_path}">build logs</a> to check if all the notebooks have been executed successfully, as well as other warnings and errors from the MyST build.""")
        else:
            log_path = write_log(task.owner_name, task.repo_name, "myst", all_logs, all_logs_dict)
            task.fail(f"⛔️ MyST build did not produce the expected webpage \n\n > [!CAUTION] \n > Please take a look at the [**build logs**]({celery_config['PREVIEW_SERVER']}/api/logs/{log_path}) to locate the error.")
            task.email_user(f"⛔️ MyST build did not produce the expected webpage \n\n > [!CAUTION] \n > Please take a look at the <a href='{celery_config['PREVIEW_SERVER']}/api/logs/{log_path}'>build logs</a> to locate the error.")
    finally:
        cleanup_hub(hub) 