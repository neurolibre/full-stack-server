"""
Rsync Book Task

Task for synchronizing book content between servers using rsync.
"""

import os
import subprocess
import logging
from github import Github
from celery import states
from api.celery.app import celery_app, celery_config, get_github_bot_token
from api.celery.utils import gh_template_respond
from common import get_owner_repo_provider, format_commit_hash, get_time,book_get_by_params, get_book_target_tail
from preprint import enforce_lab_interface

@celery_app.task(bind=True)
def rsync_book_task(self, repo_url, commit_hash, comment_id, issue_id, reviewRepository, server):
    """
    Move the book from the test to the production server.
    
    This book is expected to be built from a GH_ORGANIZATION repository.
    Once the book is available on the production server, content is symlinked 
    to a DOI formatted directory (Nginx configured) to enable DOI formatted links.
    
    Args:
        repo_url: URL of the repository
        commit_hash: Commit hash to use
        comment_id: GitHub comment ID
        issue_id: GitHub issue ID
        reviewRepository: GitHub repository for review
        server: Server URL
        
    Returns:
        A message indicating the task is complete
    """
    task_title = "REPRODUCIBLE PREPRINT TRANSFER (Preview --> Preprint)"
    github_client = Github(get_github_bot_token())
    task_id = self.request.id
    
    [owner, repo, provider] = get_owner_repo_provider(repo_url, provider_full_name=True)
    
    if owner != celery_config['GH_ORGANIZATION']:
        gh_template_respond(github_client, "failure", task_title, reviewRepository, issue_id, task_id, comment_id, f"Repository is not under {celery_config['GH_ORGANIZATION']} organization!")
        self.update_state(state=states.FAILURE, meta={'exc_type': f"{celery_config['JOURNAL_NAME']} celery exception", 'exc_message': "Custom", 'message': f"FAILURE: Repository {owner}/{repo} has no {celery_config['GH_ORGANIZATION']} fork."})
        return
    
    commit_hash = format_commit_hash(repo_url, commit_hash)
    logging.info(f"{owner}{provider}{repo}{commit_hash}")
    
    remote_path = os.path.join("neurolibre-preview:", celery_config['DATA_ROOT_PATH'][1:], celery_config['JB_ROOT_FOLDER'], owner, provider, repo, commit_hash + "*")
    
    try:
        # Log the remote path
        f = open(f"{celery_config['DATA_ROOT_PATH']}/synclog.txt", "a")
        f.write(remote_path)
        f.close()
        
        now = get_time()
        self.update_state(state=states.STARTED, meta={'message': f"Transfer started {now}"})
        gh_template_respond(github_client, "started", task_title, reviewRepository, issue_id, task_id, comment_id, "")
        
        process = subprocess.Popen(["/usr/bin/rsync", "-avR", remote_path, "/"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output = process.communicate()[0]
        ret = process.wait()
        logging.info(output)
    
    except subprocess.CalledProcessError as e:
        gh_template_respond(github_client, "failure", task_title, reviewRepository, issue_id, task_id, comment_id, f"{e.output}")
        self.update_state(state=states.FAILURE, meta={'exc_type': f"{celery_config['JOURNAL_NAME']} celery exception", 'exc_message': "Custom", 'message': e.output})
    
    results = book_get_by_params(commit_hash=commit_hash)
    if not results:
        gh_template_respond(github_client, "failure", task_title, reviewRepository, issue_id, task_id, comment_id, f"Cannot retrieve book at {commit_hash}")
        self.update_state(state=states.FAILURE, meta={'exc_type': f"{celery_config['JOURNAL_NAME']} celery exception", 'exc_message': "Custom", 'message': f"Cannot retrieve book at {commit_hash}"})
    else:
        # Symlink production book to attain a proper URL
        book_target_tail = get_book_target_tail(results[0]['book_url'], commit_hash)
        # After the commit hash, the pattern informs whether it is single or multi page.
        # If multi-page _build/html, if single page, should be _build/_page/index/singlehtml
        book_path = os.path.join(celery_config['DATA_ROOT_PATH'], celery_config['JB_ROOT_FOLDER'], owner, provider, repo, commit_hash, book_target_tail)
        # Here, make sure that all the binderhub links use the lab interface
        enforce_lab_interface(book_path)

        iid = "{:05d}".format(issue_id)
        doi_path = os.path.join(celery_config['DATA_ROOT_PATH'], celery_config['DOI_PREFIX'], f"{celery_config['DOI_SUFFIX']}.{iid}")
        process_mkd = subprocess.Popen(["mkdir", doi_path], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        output_mkd = process_mkd.communicate()[0]
        ret_mkd = process_mkd.wait()

        for item in os.listdir(book_path):
            source_path = os.path.join(book_path, item)
            target_path = os.path.join(doi_path, item)
            if os.path.isdir(source_path):
                os.symlink(source_path, target_path, target_is_directory=True)
            else:
                os.symlink(source_path, target_path)
        
        # Check if symlink successful
        if os.path.exists(os.path.join(doi_path)):
            message = f"<a href=\"{server}/{celery_config['DOI_PREFIX']}/{celery_config['DOI_SUFFIX']}.{iid}\">Reproducible Preprint URL (DOI formatted)</a><p><a href=\"{server}/{book_path}\">Reproducible Preprint (bare URL)</a></p>"
            gh_template_respond(github_client, "success", task_title, reviewRepository, issue_id, task_id, comment_id, message)
            self.update_state(state=states.SUCCESS, meta={'message': message})
        else:
            gh_template_respond(github_client, "failure", task_title, reviewRepository, issue_id, task_id, comment_id, output)
            self.update_state(state=states.FAILURE, meta={'exc_type': f"{celery_config['JOURNAL_NAME']} celery exception", 'exc_message': "Custom", 'message': f"Cannot sync book at {commit_hash}"}) 