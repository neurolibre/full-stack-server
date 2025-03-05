"""
Preview Build Book Test Task

Task for testing the book preview build process.
"""

import os
import time
import json
import logging
import requests
from flask import Response
from celery import states
from api.celery.app import celery_app
from api.celery.utils import get_time, send_email_celery, send_email_with_html_attachment_celery, write_html_log
from common import book_get_by_params, book_execution_errored, book_log_collector, get_owner_repo_provider, get_lock_filename, run_binder_build_preflight_checks

@celery_app.task(bind=True)
def preview_build_book_test_task(self, payload):
    """
    Test task that simulates building a book preview.
    
    Args:
        payload: Dictionary containing task payload
        
    Returns:
        A message indicating the task is complete
    """
    task_id = self.request.id
    owner, repo, provider = get_owner_repo_provider(payload['repo_url'], provider_full_name=True)
    
    binderhub_request = run_binder_build_preflight_checks(
        payload['repo_url'],
        payload['commit_hash'],
        payload['rate_limit'],
        payload['binder_name'],
        payload['domain_name']
    )
    
    lock_filename = get_lock_filename(payload['repo_url'])
    response = requests.get(binderhub_request, stream=True)
    
    mail_body = f"Runtime environment build has been started <code>{task_id}</code> If successful, it will be followed by the Jupyter Book build."
    send_email_celery(payload['email'], payload['mail_subject'], mail_body)
    
    now = get_time()
    self.update_state(state=states.STARTED, meta={'message': f"IN PROGRESS: Build for {owner}/{repo} at {payload['commit_hash']} has been running since {now}"})
    
    if response.ok:
        # Create binder_stream generator object
        def generate():
            for line in response.iter_lines():
                if line:
                    event_string = line.decode("utf-8")
                    try:
                        event = json.loads(event_string.split(': ', 1)[1])
                        # https://binderhub.readthedocs.io/en/latest/api.html
                        if event.get('phase') == 'failed':
                            message = event.get('message')
                            yield message
                            response.close()
                            if os.path.exists(lock_filename):
                                os.remove(lock_filename)
                            return
                        message = event.get('message')
                        if message:
                            yield message
                    except GeneratorExit:
                        pass
                    except:
                        pass
        
        # Use the generator object as the source of flask eventstream response
        binder_response = Response(generate(), mimetype='text/event-stream')
        # Fetch all the yielded messages
        binder_logs = binder_response.get_data(as_text=True)
        binder_logs = "".join(binder_logs)
        
        # After the upstream closes, check the server if there's
        # a book built successfully.
        book_status = book_get_by_params(commit_hash=payload['commit_hash'])
        exec_error = book_execution_errored(owner, repo, provider, payload['commit_hash'])
        
        # For now, remove the block either way.
        # The main purpose is to avoid triggering
        # a build for the same request. Later on
        # you may choose to add dead time after a successful build.
        if os.path.exists(lock_filename):
            os.remove(lock_filename)
            
        if not book_status or exec_error:
            # These flags will determine how the response will be
            # interpreted and returned outside the generator
            issue_comment = []
            msg = f"<p>&#129344; We ran into a problem building your book. Please see the log files below.</p><details><summary> <b>BinderHub build log</b> </summary><pre><code>{binder_logs}</code></pre></details><p>If the BinderHub build looks OK, please see the Jupyter Book build log(s) below.</p>"
            issue_comment.append(msg)
            
            owner, repo, provider = get_owner_repo_provider(payload['repo_url'], provider_full_name=True)
            # Retrieve book build and execution report logs.
            book_logs = book_log_collector(owner, repo, provider, payload['commit_hash'])
            issue_comment.append(book_logs)
            
            msg = "<p>&#128030; After inspecting the logs above, you can interactively debug your notebooks on our <a href=\"https://test.conp.cloud\">BinderHub server</a>.</p> <p>For guidelines, please see <a href=\"https://docs.neurolibre.org/en/latest/TEST_SUBMISSION.html#debugging-for-long-neurolibre-submission\">the relevant documentation.</a></p>"
            issue_comment.append(msg)
            
            issue_comment = "\n".join(issue_comment)
            tmp_log = write_html_log(payload['commit_hash'], issue_comment)
            
            body = "<p>&#129344; We ran into a problem building your book. Please download the log file attached and open in your web browser.</p>"
            send_email_with_html_attachment_celery(payload['email'], payload['mail_subject'], body, tmp_log)
            
            self.update_state(state=states.FAILURE, meta={'exc_type': f"NeuroLibre celery exception", 'exc_message': "Custom", 'message': f"FAILURE: Build for {owner}/{repo} at {payload['commit_hash']} has failed"})
        else:
            mail_body = f"Book build successful: {book_status[0]['book_url']}"
            send_email_celery(payload['email'], payload['mail_subject'], mail_body)
            
            self.update_state(state=states.SUCCESS, meta={'message': f"SUCCESS: Build for {owner}/{repo} at {payload['commit_hash']} has succeeded."}) 