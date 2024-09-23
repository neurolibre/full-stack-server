import os
from flask import jsonify, make_response, render_template, Response, stream_with_context
from common import *
from schema import BuildSchema, BuildTestSchema, DownloadSchema, MystBuildSchema
from flask_htpasswd import HtPasswdAuth
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_apispec import FlaskApiSpec, marshal_with, doc, use_kwargs
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from github_client import *
from neurolibre_celery_tasks import celery_app, sleep_task, preview_build_book_task, preview_build_book_test_task, preview_download_data,preview_build_myst_task
from celery.events.state import State
from github import Github, UnknownObjectException
from screening_client import ScreeningClient
"""
Configuration START
"""

from neurolibre_api import NeuroLibreAPI

preview_api = NeuroLibreAPI(__name__,
                            ['config/common.yaml', 'config/preview.yaml'])
app = preview_api.get_app()
docs = preview_api.docs

# Extract configuration variables
JOURNAL_NAME = app.config['JOURNAL_NAME']
REVIEW_REPOSITORY = app.config['REVIEW_REPOSITORY']
BINDER_NAME = app.config['BINDER_NAME']
BINDER_DOMAIN = app.config['BINDER_DOMAIN']
RATE_LIMIT = app.config['RATE_LIMIT']
SERVER_CONTACT = app.config['SERVER_CONTACT']
SERVER_SLUG = app.config['SERVER_SLUG']
SERVER_DOMAIN = app.config['SERVER_DOMAIN']
SERVER_TOS = app.config['SERVER_TOS']
SERVER_ABOUT = app.config['SERVER_ABOUT']
SERVER_LOGO = app.config['SERVER_LOGO']
DATA_ROOT_PATH = app.config['DATA_ROOT_PATH']
MYST_FOLDER = app.config['MYST_FOLDER']

# Set server name and about information
SERVER_NAME  = SERVER_SLUG
SERVER_ABOUT = SERVER_ABOUT + SERVER_LOGO

app.logger.info(f'{JOURNAL_NAME} preview API.')
app.logger.info(f"Using {BINDER_NAME}.{BINDER_DOMAIN} as BinderHub.")

"""
Configuration END
"""

# Create a directory for build locks (used for rate limiting)
if not os.path.exists(os.path.join(os.getcwd(),'build_locks')):
    os.makedirs(os.path.join(os.getcwd(),'build_locks'))

"""
API Endpoints START
"""

@app.route('/api/data/cache', methods=['POST'])
@preview_api.auth_required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@use_kwargs(DownloadSchema())
@doc(description='Endpoint for downloading data through repo2data.', tags=['Data'])
def api_download_data(user, id, repository_url, email=None, is_overwrite=None):
    """
    This endpoint is to download data from GitHub (technical screening) requests.
    """
    extra_payload = dict(email=email, is_overwrite=is_overwrite)
    screening = ScreeningClient(task_name="DOWNLOAD DATA", 
                                issue_id=id, 
                                target_repo_url=repository_url,
                                **extra_payload)
    response = screening.start_celery_task(preview_download_data)
    return response

docs.register(api_download_data)

@app.route('/api/book/build', methods=['POST'])
@preview_api.auth_required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@marshal_with(None,code=200,description="Accept text/eventstream for BinderHub build logs. Keepalive 30s.")
@doc(description='Endpoint for building reproducibility assets on the preview BinderHub instance: Repo2Data, (Binder) Repo2Docker, Jupyter Book.', tags=['Book'])
@use_kwargs(BuildSchema())
def api_book_build(user, id, repo_url, commit_hash):
    """
    This endpoint is to build books via GitHub (technical screening) requests.
    """
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    issue_id = id

    task_title = "Book Build (Preview)"
    comment_id = gh_template_respond(github_client,"pending",task_title,REVIEW_REPOSITORY,issue_id)

    celery_payload = dict(repo_url=repo_url,
                          commit_hash=commit_hash,
                          rate_limit=RATE_LIMIT,
                          binder_name=BINDER_NAME,
                          domain_name = BINDER_DOMAIN,
                          comment_id=comment_id,
                          issue_id=issue_id,
                          review_repository=REVIEW_REPOSITORY,
                          task_title=task_title)

    task_result = preview_build_book_task.apply_async(args=[celery_payload])

    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,REVIEW_REPOSITORY,issue_id,task_result.task_id,comment_id, "")
        response = make_response(jsonify("Celery task assigned successfully."),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,REVIEW_REPOSITORY,issue_id,task_result.task_id,comment_id, f"Internal server error: {JOURNAL_NAME} background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)
    return response

# Register endpoint to the documentation
docs.register(api_book_build)

@app.route('/api/book/build/test', methods=['POST'])
@preview_api.auth_required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@marshal_with(None,code=200,description="Accept text/eventstream for BinderHub build logs. Keepalive 30s.")
@doc(description='Endpoint for building NRP through webpage', tags=['Book'])
@use_kwargs(BuildTestSchema())
def api_book_build_test(user, repo_url, commit_hash, email):
    """
    This endpoint is used by robo.neurolibre.org.
    """

    [owner, repo, provider] = get_owner_repo_provider(repo_url)
    mail_subject = f"NRP test build for {owner}/{repo}"
    mail_body = f"We have received your request to build a {JOURNAL_NAME} reproducible preprint from {repo_url} at {commit_hash}. \n Your request has been queued, we will inform you when the process starts."

    send_email(email, mail_subject, mail_body)

    celery_payload = dict(repo_url=repo_url,
                          commit_hash=commit_hash,
                          rate_limit=RATE_LIMIT,
                          binder_name=BINDER_NAME,
                          domain_name = BINDER_DOMAIN,
                          email = email,
                          review_repository=REVIEW_REPOSITORY,
                          mail_subject=mail_subject)

    task_result = preview_build_book_test_task.apply_async(args=[celery_payload])

    if task_result.task_id is not None:
        mail_body = f"We started processing your NRP test request. Work ID: <code>{task_result.task_id}</code>. \n We will send you the results."
        response = make_response(jsonify("Celery task assigned successfully."),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        mail_body = f"We could not start processing your NRP test request due to a technical issue on the server side. Please contact info@neurolibre.org."
        response = make_response(jsonify("Celery could not start the task."),500)

    send_email(email, mail_subject, mail_body)
    return response

docs.register(api_book_build_test)

@app.route('/api/test', methods=['GET'])
@preview_api.auth_required
@doc(description='Check if SSL verified authentication is functional.', tags=['Test'])
def api_preview_test(user):
    response = make_response(jsonify(f"Preview server login successful. <3 {JOURNAL_NAME}"),200)
    response.mimetype = "text/plain"
    return response

docs.register(api_preview_test)

@app.route('/api/celery/test', methods=['GET'],endpoint='api_celery_test')
@preview_api.auth_required
@doc(description='Starts a background task (sleep 1 min) and returns task ID.', tags=['Tests'])
def api_celery_test(user):
    seconds = 60
    task = sleep_task.apply_async(args=[seconds])
    return f'Celery test started: {task.id}'

docs.register(api_celery_test)

@app.route('/api/celery/test/<task_id>',methods=['GET'], endpoint='get_task_status_test')
@preview_api.auth_required
@doc(description='Get the status of the test task.', tags=['Tasks'])
def get_task_status_test(user,task_id):
    task = celery_app.AsyncResult(task_id)
    if task.state == 'PENDING':
        response = {
            'status': 'Waiting to start.'
        }
    elif task.state == 'STARTED':
        response = {
            'status': 'In progress',
            'message': f"{task.info}"
        }
    elif task.state == 'SUCCESS':
        response = {
            'status': 'done sleeping for 60 seconds',
            'message': f"{task.info}"
        }
    else:
        response = {
            'status': 'failed to sleep'
        }
    return jsonify(response)

docs.register(get_task_status_test)

@app.route('/api/myst/build', methods=['POST'],endpoint='api_myst_build')
@preview_api.auth_required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@use_kwargs(MystBuildSchema())
@doc(description='Endpoint for building myst formatted articles.', tags=['Myst'])
def api_myst_build(user, id, repository_url, commit_hash=None, binder_hash=None):
    """
    This endpoint is to download data from GitHub (technical screening) requests.
    """
    app.logger.info(f'Entered MyST build endpoint')
    extra_payload = dict(commit_hash=commit_hash, binder_hash=binder_hash)
    screening = ScreeningClient(task_name="Build MyST article", 
                                issue_id=id, 
                                target_repo_url=repository_url,
                                **extra_payload)
    response = screening.start_celery_task(preview_build_myst_task)
    return response

@app.route('/api/validate',methods=['GET'],endpoint='validate')
@doc(description='Something else.', tags=['Book'])
def validate():
    rendered = render_template('validate.html')
    response = make_response(rendered)
    response.headers['Content-Type'] = 'text/html'
    return response

@app.route('/api/process',methods=['GET'],endpoint='process')
@doc(description='Something', tags=['Book'])
def process():
    def generate():
        steps = [
            "Initializing process...",
            "Step 1: Validating input...",
            "Step 2: Processing data...",
            "Step 3: Analyzing results...",
            "Step 4: Generating report...",
            "Process completed successfully!"
        ]
        
        for step in steps:
            # Simulate some work
            time.sleep(1)
            app.logger.info(step)
            yield f"data: {json.dumps({'message': step})}\n\n"
        
        # Send a final message to indicate completion
        yield f"data: {json.dumps({'message': 'All steps completed.', 'status': 'complete'})}\n\n"

    return Response(stream_with_context(generate()), content_type='text/event-stream')

docs.register(api_myst_build)

for rule in app.url_map.iter_rules():
    if "POST" in rule.methods:
        app.logger.info(f"{rule.rule} - {rule.endpoint}")
    if "GET" in rule.methods:
        app.logger.info(f"{rule.rule} - {rule.endpoint}")