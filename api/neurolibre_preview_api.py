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
from schema import BuildSchema, BuildTestSchema, DownloadSchema
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

"""
Configuration START
"""

# THIS IS NEEDED UNLESS FLASK IS CONFIGURED TO AUTO-LOAD!
load_dotenv()

app = flask.Flask(__name__)

# LOAD CONFIGURATION FILE
app.config.from_pyfile('preview_config.py')

app.register_blueprint(neurolibre_common_api.common_api)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

gunicorn_error_logger = logging.getLogger('gunicorn.error')
app.logger.handlers.extend(gunicorn_error_logger.handlers)
app.logger.setLevel(logging.DEBUG)
app.logger.debug('NeuroLibre preview API.')

AUTH_KEY=os.getenv('AUTH_KEY')
app.config['FLASK_HTPASSWD_PATH'] = AUTH_KEY
htpasswd = HtPasswdAuth(app)

reviewRepository = app.config["REVIEW_REPOSITORY"]
binderName = app.config["BINDER_NAME"]
domainName = app.config["BINDER_DOMAIN"]
build_rate_limit = app.config["RATE_LIMIT"]

app.logger.info(f"Using {binderName}.{domainName} as BinderHub.")

serverContact = app.config["SERVER_CONTACT"]
serverName = app.config["SERVER_SLUG"]
serverDescription = app.config["SERVER_DESC"]
serverTOS = app.config["SERVER_TOS"]
serverAbout = app.config["SERVER_ABOUT"] + app.config["SERVER_LOGO"]

# API specifications displayed on the swagger UI
spec = APISpec(
        title="Neurolibre preview & screening API",
        version='v1',
        plugins=[MarshmallowPlugin()],
        openapi_version="3.0.2",
        info=dict(description=serverAbout,contact=serverContact,termsOfService=serverTOS),
        servers = [{'url': f'https://{serverName}.neurolibre.org/','description':'Preview server.', 'variables': {'serverName':{'default':serverName}}}]
        )

# SWAGGER UI URLS. Pay attention to /swagger/ vs /swagger.
app.config.update({'APISPEC_SPEC': spec})

# Through Python, there's no way to disable within-documentation API calls.
# Even though "Try it out" is not functional, we cannot get rid of it.
api_key_scheme = {"type": "http", "scheme": "basic"}
spec.components.security_scheme("basicAuth", api_key_scheme)

# Create swagger UI documentation for the endpoints.
docs = FlaskApiSpec(app=app,document_options=False,)

# Register common endpoints to the documentation
docs.register(neurolibre_common_api.api_get_book,blueprint="common_api")
docs.register(neurolibre_common_api.api_get_books,blueprint="common_api")
docs.register(neurolibre_common_api.api_heartbeat,blueprint="common_api")
docs.register(neurolibre_common_api.api_unlock_build,blueprint="common_api")
docs.register(neurolibre_common_api.api_preview_list,blueprint="common_api")

"""
Configuration END
"""

# Create a build_locks folder to control rate limits
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
def api_download_data(user, id, repo_url, email, is_overwrite):
    """
    This endpoint is to download data from GitHub (technical screening) requests.
    """
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    issue_id = id

    task_title = "Download data for preview."
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id)

    celery_payload = dict(repo_url=repo_url,
                          rate_limit=build_rate_limit,
                          binder_name=binderName,
                          domain_name = domainName,
                          comment_id=comment_id,
                          issue_id=issue_id,
                          review_repository=reviewRepository,
                          task_title=task_title,
                          overwrite=is_overwrite,
                          email=email)

    task_result = preview_download_data.apply_async(args=[celery_payload])

    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "")
        response = make_response(jsonify("Celery task assigned successfully."),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)
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
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id)

    celery_payload = dict(repo_url=repo_url,
                          commit_hash=commit_hash,
                          rate_limit=build_rate_limit,
                          binder_name=binderName,
                          domain_name = domainName,
                          comment_id=comment_id,
                          issue_id=issue_id,
                          review_repository=reviewRepository,
                          task_title=task_title)

    task_result = preview_build_book_task.apply_async(args=[celery_payload])

    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "")
        response = make_response(jsonify("Celery task assigned successfully."),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
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
    mail_body = f"We have received your request to build a NeuroLibre reproducible preprint from {repo_url} at {commit_hash}. \n Your request has been queued, we will inform you when the process starts."

    send_email(email, mail_subject, mail_body)

    celery_payload = dict(repo_url=repo_url,
                          commit_hash=commit_hash,
                          rate_limit=build_rate_limit,
                          binder_name=binderName,
                          domain_name = domainName,
                          email = email,
                          review_repository=reviewRepository,
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
    response = make_response(jsonify("Preview server login successful. <3 NeuroLibre"),200)
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