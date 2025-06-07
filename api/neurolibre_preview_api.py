import os
from flask import jsonify, make_response, render_template, Response, stream_with_context, request
from urllib.parse import urlparse
import time
import requests
import json
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
REVIEW_REPOSITORY = app.config['REVIEW_REPOSITORY']

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
def api_download_data(user, repository_url, id=None, email=None, is_overwrite=None, external_repo=None):
    """
    This endpoint is to download data from GitHub (technical screening) requests.
    """
    extra_payload = dict(email=email, is_overwrite=is_overwrite, external_repo=external_repo)
    app.logger.info(f'Extra payload at endpoint: {extra_payload}')
    cur_rev_repo = external_repo if external_repo else REVIEW_REPOSITORY
    app.logger.info(f'External repo at endpoint: {cur_rev_repo}')
    screening = ScreeningClient(task_name="DOWNLOAD (CACHE) DATA", 
                                issue_id=id, 
                                target_repo_url=repository_url,
                                review_repository=cur_rev_repo,
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

# @app.route('/api/book/build/test', methods=['POST'])
# @preview_api.auth_required
# @marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
# @marshal_with(None,code=200,description="Accept text/eventstream for BinderHub build logs. Keepalive 30s.")
# @doc(description='Endpoint for building NRP through webpage', tags=['Book'])
# @use_kwargs(BuildTestSchema())
# def api_book_build_test(user, repo_url, commit_hash, email):
#     """
#     This endpoint is used by robo.neurolibre.org.
#     """

#     [owner, repo, provider] = get_owner_repo_provider(repo_url)
#     mail_subject = f"NRP test build for {owner}/{repo}"
#     mail_body = f"We have received your request to build a {JOURNAL_NAME} reproducible preprint from {repo_url} at {commit_hash}. \n Your request has been queued, we will inform you when the process starts."

#     send_email(email, mail_subject, mail_body)

#     celery_payload = dict(repo_url=repo_url,
#                           commit_hash=commit_hash,
#                           rate_limit=RATE_LIMIT,
#                           binder_name=BINDER_NAME,
#                           domain_name = BINDER_DOMAIN,
#                           email = email,
#                           review_repository=REVIEW_REPOSITORY,
#                           mail_subject=mail_subject)

#     task_result = preview_build_book_test_task.apply_async(args=[celery_payload])

#     if task_result.task_id is not None:
#         mail_body = f"We started processing your NRP test request. Work ID: <code>{task_result.task_id}</code>. \n We will send you the results."
#         response = make_response(jsonify("Celery task assigned successfully."),200)
#     else:
#         # If not successfully assigned, fail the status immediately and return 500
#         mail_body = f"We could not start processing your NRP test request due to a technical issue on the server side. Please contact info@neurolibre.org."
#         response = make_response(jsonify("Celery could not start the task."),500)

#     send_email(email, mail_subject, mail_body)
#     return response

# docs.register(api_book_build_test)

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
def api_myst_build(user, id, repository_url, commit_hash=None, binder_hash=None, is_prod=False):
    """
    This endpoint is to download data from GitHub (technical screening) requests.
    """
    app.logger.info(f'Entered MyST build endpoint')
    
    if commit_hash == "production":
        extra_payload = dict(commit_hash="latest", binder_hash="latest", is_prod=True)
    else:
        extra_payload = dict(commit_hash=commit_hash, binder_hash=binder_hash, is_prod=is_prod)

    screening = ScreeningClient(task_name="Build MyST article", 
                                issue_id=id, 
                                target_repo_url=repository_url,
                                **extra_payload)
    response = screening.start_celery_task(preview_build_myst_task)
    return response

@app.route('/api/book/build/test', methods=['POST'],endpoint='api_myst_build_robo')
@preview_api.auth_required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@marshal_with(None,code=200,description="Accept text/eventstream for BinderHub build logs. Keepalive 30s.")
@doc(description='Endpoint for building NRP through webpage', tags=['Myst'])
@use_kwargs(BuildTestSchema())
def api_myst_build_robo(user, repo_url, commit_hash, email):
    """
    This endpoint is used by robo.neurolibre.org.
    repo_url can be a full URL or just the owner/repo.
    commit_hash is actually the binder_hash (TODO: Change this)
    email is the email address of the user who requested the build.
    """
    repo_url = gh_filter(repo_url,return_url=True)
    [owner, repo, _] =  get_owner_repo_provider(repo_url)
    commit_hash_source = format_commit_hash(repo_url,"HEAD")
    extra_payload = dict(is_prod=False, commit_hash=commit_hash_source, binder_hash=commit_hash)
    screening = ScreeningClient(task_name=f"{JOURNAL_NAME} build request for {owner}/{repo} {commit_hash_source[0:6]}", 
                                email_address=email,
                                target_repo_url=repo_url,
                                **extra_payload)
    response = screening.start_celery_task(preview_build_myst_task)
    return response



"""
EXTERNAL, PUBLIC ENDPOINTS for mini apps

Nginx checks for the exact matches for these, and routes the requests
to the corresponding Flask endpoints explicitly (unix.sock/api/this/that)
"""

@app.route('/api/validate',methods=['GET'],endpoint='validate')
@doc(description='Something else.', tags=['Book'])
def validate():
    """
    This enpoint serves a simple UI to validate repository structure
    for Jupyter Book or MyST format.
    It redirects ?repo_url=https://github.com/username/reponame
    to /api/process?repo_url=https://github.com/username/reponame
    see the template in templates/validate.html
    """
    rendered = render_template('validate.html')
    response = make_response(rendered)
    response.headers['Content-Type'] = 'text/html'
    return response

@app.route('/api/process', methods=['GET'], endpoint='process')
@doc(description='Validate repository structure for Jupyter Book or MyST format', tags=['Book'])
def process():
    """
    This endpoint applies the logic to validate the repository structure for
    Jupyter Book or MyST format using the GitHub API.

    Conditions:
    - Has binder folder (error if not)
    - Has data_requirement.json in binder folder (warning if not)
    - Has content folder (error if not)
    - Has _toc.yml in content folder (error if not)
    - Has _config.yml in content folder (error if not, jb_exclusive)
    - Has myst.yml in content folder (myst exclusive)

    Conditions are not really through or strict, but more to guide the user.
    This can be a part of myst_libre, opportunity to make it cleaner and more comprehensive.

    Uses stream events to return the status and messages to the client in real time.
    """
    repo_url = request.args.get('repo_url')
    if not repo_url:
        return jsonify({"error": "No repo_url provided"}), 400

    def generate():
        try:
            yield f"data: {json.dumps({'message': f'Fetching repository contents: {repo_url}', 'status': 'info'})}\n\n"
            
            parsed_url = urlparse(repo_url)
            path_parts = parsed_url.path.strip('/').split('/')
            if len(path_parts) < 2:
                raise ValueError("Invalid GitHub URL")
            owner, repo = path_parts[:2]

            api_url = f"https://api.github.com/repos/{owner}/{repo}/contents"
            
            def fetch_contents(path=''):
                response = requests.get(f"{api_url}/{path}")
                response.raise_for_status()
                return response.json()

            contents = fetch_contents()
            has_binder_folder = False
            has_data_requirement = False
            has_content_folder = False
            has_toc_yml = False
            has_config_yml = False
            has_myst_yml = False

            yield f"data: {json.dumps({'message': '🗂️ Listing repository contents:', 'status': 'info'})}\n\n"
            for item in contents:
                if item['type'] == 'dir' and item['name'] == 'binder':
                    has_binder_folder = True
                    binder_contents = fetch_contents('binder')
                    yield f"data: {json.dumps({'message': '📁 Listing binder folder contents:', 'status': 'info'})}\n\n"
                    for binder_item in binder_contents:
                        yield f"data: {json.dumps({'message': '- 📄 ' + binder_item['name'], 'status': 'info'})}\n\n"
                        if binder_item['name'] == 'data_requirement.json':
                            has_data_requirement = True
                elif item['type'] == 'dir' and item['name'] == 'content':
                    has_content_folder = True
                    content_contents = fetch_contents('content')
                    yield f"data: {json.dumps({'message': '📁 Listing content folder contents:', 'status': 'info'})}\n\n"
                    for content_item in content_contents:
                        if content_item['name'] == '_toc.yml':
                            has_toc_yml = True
                            yield f"data: {json.dumps({'message': '- ⚙️ ' + content_item['name'], 'status': 'positive'})}\n\n"
                        elif content_item['name'] == '_config.yml':
                            has_config_yml = True
                            yield f"data: {json.dumps({'message': '- ⚙️ ' + content_item['name'], 'status': 'positive'})}\n\n"
                        else:
                            yield f"data: {json.dumps({'message': '- 📄 ' + content_item['name'], 'status': 'info'})}\n\n"
                elif item['type'] == 'file' and item['name'] == 'myst.yml':
                    has_myst_yml = True
                    yield f"data: {json.dumps({'message': '- ⚙️ ' + content_item['name'], 'status': 'positive'})}\n\n"
                else:
                    yield f"data: {json.dumps({'message': '- 📄 ' + item['name'], 'status': 'info'})}\n\n"

            # Perform validation checks
            if not has_binder_folder:
                yield f"data: {json.dumps({'message': 'Error: binder folder not found at the root.', 'status': 'error'})}\n\n"
                yield f"data: {json.dumps({'message': 'Repository structure is invalid.', 'status': 'failure'})}\n\n"
                return

            if not has_data_requirement:
                yield f"data: {json.dumps({'message': 'Warning: data_requirement.json not found in binder folder.', 'status': 'warning'})}\n\n"

            if has_myst_yml:
                yield f"data: {json.dumps({'message': 'Repository follows MyST format.', 'status': 'positive'})}\n\n"
                yield f"data: {json.dumps({'message': 'Repository structure is valid.', 'status': 'success'})}\n\n"
            elif has_content_folder and has_toc_yml and has_config_yml:
                yield f"data: {json.dumps({'message': 'Repository follows Jupyter Book format.', 'info': 'positive'})}\n\n"
                yield f"data: {json.dumps({'message': 'Repository structure is valid.', 'status': 'success'})}\n\n"
            else:
                missing_items = []
                if not has_content_folder:
                    missing_items.append("content folder")
                if not has_toc_yml:
                    missing_items.append("_toc.yml")
                if not has_config_yml:
                    missing_items.append("_config.yml")
                
                error_message = f"Error: Repository does not meet Jupyter Book or MyST format requirements. Missing: {', '.join(missing_items)}."
                yield f"data: {json.dumps({'message': error_message, 'status': 'error'})}\n\n"
                yield f"data: {json.dumps({'message': 'Repository structure is invalid.', 'status': 'failure'})}\n\n"

        except requests.RequestException as e:
            yield f"data: {json.dumps({'message': f'Error fetching repository contents: {str(e)}', 'status': 'error'})}\n\n"
            yield f"data: {json.dumps({'message': 'Process failed.', 'status': 'failure'})}\n\n"
        except ValueError as e:
            yield f"data: {json.dumps({'message': f'Error: {str(e)}', 'status': 'error'})}\n\n"
            yield f"data: {json.dumps({'message': 'Process failed.', 'status': 'failure'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'message': f'Unexpected error: {str(e)}', 'status': 'error'})}\n\n"
            yield f"data: {json.dumps({'message': 'Process failed.', 'status': 'failure'})}\n\n"

    return Response(stream_with_context(generate()), content_type='text/event-stream')


docs.register(api_myst_build)

# for rule in app.url_map.iter_rules():
#     if "POST" in rule.methods:
#         app.logger.info(f"{rule.rule} - {rule.endpoint}")
#     if "GET" in rule.methods:
#         app.logger.info(f"{rule.rule} - {rule.endpoint}")