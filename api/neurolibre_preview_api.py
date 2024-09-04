import flask
import os
import json
import time
import requests
import git
import logging
import neurolibre_common_api
from flask import jsonify, make_response
from common import *
from schema import BuildSchema, BuildTestSchema, DownloadSchema, MystBuildSchema
from flask_htpasswd import HtPasswdAuth
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_apispec import FlaskApiSpec, marshal_with, doc, use_kwargs
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from github_client import *
from neurolibre_celery_tasks import celery_app, sleep_task, preview_build_book_task, preview_build_book_test_task, preview_download_data
from celery.events.state import State
from github import Github, UnknownObjectException
from screening_client import ScreeningClient
"""
Configuration START
"""

from neurolibre_api import NeuroLibreAPI

common_endpoints = [
    neurolibre_common_api.api_get_book,
    neurolibre_common_api.api_get_books,
    neurolibre_common_api.api_heartbeat,
    neurolibre_common_api.api_unlock_build,
    neurolibre_common_api.api_preview_list]

preview_api = NeuroLibreAPI(__name__, 
                            ['config/common.yaml', 'config/preview.yaml'], 
                            common_endpoints)
app = preview_api.get_app()
docs = preview_api.get_docs()

# load_dotenv()

# # Initialize Flask app
# app = flask.Flask(__name__)

# # Load and update app configuration from YAML files
# preview_config = load_yaml('config/preview.yaml')
# common_config = load_yaml('config/common.yaml')
# app.config.update(preview_config)
# app.config.update(common_config)

# # Register common API blueprint
# app.register_blueprint(neurolibre_common_api.common_api)
# # Configure app to work behind a proxy
# app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# # Set up logging
# app.logger.handlers.extend(logging.getLogger('gunicorn.error').handlers)
# app.logger.setLevel(logging.DEBUG)

# # Set up authentication
# app.config['FLASK_HTPASSWD_PATH'] = os.getenv('AUTH_KEY')
# htpasswd = HtPasswdAuth(app)

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

app.logger.debug(f'{JOURNAL_NAME} preview API.')
app.logger.info(f"Using {BINDER_NAME}.{BINDER_DOMAIN} as BinderHub.")

# Set server name and about information
SERVER_NAME  = SERVER_SLUG
SERVER_ABOUT = SERVER_ABOUT + SERVER_LOGO

# # Set up API specification for Swagger UI
# spec = APISpec(
#         title="Neurolibre preview & screening API",
#         version='v1',
#         plugins=[MarshmallowPlugin()],
#         openapi_version="3.0.2",
#         info=dict(description=SERVER_ABOUT,contact=SERVER_CONTACT,termsOfService=SERVER_TOS),
#         servers = [{'url': f'https://{SERVER_NAME}.{SERVER_DOMAIN}/','description':'Preview server.', 'variables': {'SERVER_NAME':{'default':SERVER_NAME}}}]
#         )

# # Update app config with API spec
# # SWAGGER UI URLS. Pay attention to /swagger/ vs /swagger.
# app.config.update({'APISPEC_SPEC': spec})

# # Set up security scheme for API
# # Through Python, there's no way to disable within-documentation API calls.
# # Even though "Try it out" is not functional, we cannot get rid of it.
# api_key_scheme = {"type": "http", "scheme": "basic"}
# spec.components.security_scheme("basicAuth", api_key_scheme)

# # Create swagger UI documentation for the endpoints.
# docs = FlaskApiSpec(app=app,document_options=False,)

# # Register common API endpoints to the documentation
# docs.register(neurolibre_common_api.api_get_book,blueprint="common_api")
# docs.register(neurolibre_common_api.api_get_books,blueprint="common_api")
# docs.register(neurolibre_common_api.api_heartbeat,blueprint="common_api")
# docs.register(neurolibre_common_api.api_unlock_build,blueprint="common_api")
# docs.register(neurolibre_common_api.api_preview_list,blueprint="common_api")

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
@htpasswd.required
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
@htpasswd.required
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
@htpasswd.required
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
@htpasswd.required
@doc(description='Check if SSL verified authentication is functional.', tags=['Test'])
def api_preview_test(user):
    response = make_response(jsonify(f"Preview server login successful. <3 {JOURNAL_NAME}"),200)
    response.mimetype = "text/plain"
    return response

docs.register(api_preview_test)

@app.route('/api/celery/test', methods=['GET'],endpoint='api_celery_test')
@htpasswd.required
@doc(description='Starts a background task (sleep 1 min) and returns task ID.', tags=['Tests'])
def api_celery_test(user):
    seconds = 60
    task = sleep_task.apply_async(args=[seconds])
    return f'Celery test started: {task.id}'

docs.register(api_celery_test)

@app.route('/api/celery/test/<task_id>',methods=['GET'], endpoint='get_task_status_test')
@htpasswd.required
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

# @app.route('/api/myst/build', methods=['POST'])
# @htpasswd.required
# @marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
# @marshal_with(None,code=200,description="Accept text/eventstream for BinderHub build logs. Keepalive 30s.")
# @doc(description='Endpoint for building MyST Markdown formatted articles.', tags=['MyST'])
# @use_kwargs(MystBuildSchema())
# def api_myst_build(user, id, repo_url, commit_hash, binder_hash):
#     GH_BOT=os.getenv('GH_BOT')
#     github_client = Github(GH_BOT)
#     issue_id = id

#     task_title = "MyST Build (Preview)"
#     comment_id = gh_template_respond(github_client,"pending",task_title,REVIEW_REPOSITORY,issue_id)

#     celery_payload = dict(repo_url=repo_url,
#                           commit_hash=commit_hash,
#                           binder_hash = binder_hash,
#                           rate_limit=RATE_LIMIT,
#                           binder_name=BINDER_NAME,
#                           domain_name = BINDER_DOMAIN,
#                           comment_id=comment_id,
#                           issue_id=issue_id,
#                           review_repository=REVIEW_REPOSITORY,
#                           task_title=task_title)

#     task_result = preview_build_myst_task.apply_async(args=[celery_payload])

# docs.register(api_myst_build)