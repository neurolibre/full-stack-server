import flask
import os
import json
import glob
import time
import subprocess
import requests
import shutil
import git
import logging
import neurolibre_common_api
from common import *
from preprint import *
from github_client import *
from schema import BinderSchema, BucketsSchema, UploadSchema, ListSchema, DeleteSchema, PublishSchema, DatasyncSchema, BooksyncSchema, ProdStartSchema, IDSchema
from flask import jsonify, make_response, Config
from flask_apispec import FlaskApiSpec, marshal_with, doc, use_kwargs
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from flask_htpasswd import HtPasswdAuth
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from neurolibre_celery_tasks import celery_app, rsync_data_task, sleep_task, rsync_book_task, fork_configure_repository_task, zenodo_create_buckets_task, zenodo_upload_book_task, zenodo_upload_repository_task, zenodo_upload_docker_task, zenodo_publish_task, preprint_build_pdf_draft
from github import Github

"""
Configuration START
"""

# THIS IS NEEDED UNLESS FLASK IS CONFIGURED TO AUTO-LOAD!
load_dotenv()

app = flask.Flask(__name__)

# LOAD CONFIGURATION FILE
app.config.from_pyfile('preprint_config.py')

app.register_blueprint(neurolibre_common_api.common_api)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

gunicorn_error_logger = logging.getLogger('gunicorn.error')
app.logger.handlers.extend(gunicorn_error_logger.handlers)
app.logger.setLevel(logging.DEBUG)
app.logger.debug('NeuroLibre preprint production API.')

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
serverDomain = app.config["SERVER_DOMAIN"]
serverDescription = app.config["SERVER_DESC"]
serverTOS = app.config["SERVER_TOS"]
serverAbout = app.config["SERVER_ABOUT"] + app.config["SERVER_LOGO"]

app.logger.info(f"Server running https://{serverName}.{serverDomain}.")

spec = APISpec(
        title="Reproducible preprint API",
        version='v1',
        plugins=[MarshmallowPlugin()],
        openapi_version="3.0.2",
        info=dict(description=serverAbout,contact=serverContact,termsOfService=serverTOS),
        servers = [{'url': f'https://{serverName}.{domainName}/','description':'Production server.', 'variables': {'serverName':{'default':serverName},'domainName':{'default':domainName}}}]
        )

# SWAGGER UI URLS. Interestingly, the preview deployment 
# required `/swagger/` instead. This one works as is.
app.config.update({'APISPEC_SPEC': spec})

# Through Python, there's no way to disable within-documentation API calls.
# Even though "Try it out" is not functional, we cannot get rid of it.
api_key_scheme = {"type": "http", "scheme": "basic"}
spec.components.security_scheme("basicAuth", api_key_scheme)

# Create swagger UI documentation for the endpoints.
docs = FlaskApiSpec(app=app,document_options=False)

# Register common endpoints to the documentation
docs.register(neurolibre_common_api.api_get_book,blueprint="common_api")
docs.register(neurolibre_common_api.api_get_books,blueprint="common_api")
docs.register(neurolibre_common_api.api_heartbeat,blueprint="common_api")
docs.register(neurolibre_common_api.api_unlock_build,blueprint="common_api")

"""
Configuration END
"""

# Create a build_locks folder to control rate limits
if not os.path.exists(os.path.join(os.getcwd(),'build_locks')):
    os.makedirs(os.path.join(os.getcwd(),'build_locks'))


"""
API Endpoints START
"""
@app.route('/api/pdf/sync', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Copy summary PDF from neurolibre/preprints to NeuroLibre server.', tags=['Production'])
@use_kwargs(IDSchema())
def summary_pdf_sync_post(user,id):
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    issue_id = id
    url_branch = f"https://raw.githubusercontent.com/neurolibre/preprints/neurolibre.{issue_id:05d}/neurolibre.{issue_id:05d}/10.55458.neurolibre.{issue_id:05d}.pdf"
    url_master = f"https://raw.githubusercontent.com/neurolibre/preprints/master/neurolibre.{issue_id:05d}/10.55458.neurolibre.{issue_id:05d}.pdf"

    if requests.head(url_branch).status_code == 200:
        download_url = url_branch
    elif requests.head(url_master).status_code == 200:
        # In case where both exist, this should be the one
        download_url = url_master
    else:
        result = make_response(f"A PDF could not be found for review ID {issue_id}. Note that it is only available after `recommend-accept`.",404)
        result.mimetype = "text/plain"
        return result 
    
    # PDF pool
    file_path = os.path.join("/DATA","10.55458",f"neurolibre.{issue_id:05d}.pdf")
    response = requests.get(download_url)

    if response.status_code == 200:
        # Delete the old one if exists.
        if os.path.exists(file_path):
            os.remove(file_path)
        with open(file_path, "wb") as file:
            file.write(response.content)
        result = make_response(f":seedling::recycle::page_facing_up: Synced the summary PDF from the [source]({download_url}), should be now available at https://preprint.neurolibre.org/10.55458/neurolibre.{issue_id:05d}.pdf?no-cache",200)
    else:
        result = make_response(f"Summary PDF was available at the [source]({download_url}), but could not download it to our servers.",500)
    
    result.mimetype = "text/plain"
    return result

docs.register(summary_pdf_sync_post)

@app.route('/api/zenodo/upload/repository', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Upload the repository to the respective zenodo deposit.', tags=['Zenodo'])
@use_kwargs(DatasyncSchema())
def zenodo_upload_repository_post(user,id,repository_url):
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    issue_id = id

    fname = f"zenodo_deposit_NeuroLibre_{issue_id:05d}.json"
    local_file = os.path.join(get_deposit_dir(issue_id), fname)
    with open(local_file, 'r') as f:
        zenodo_record = json.load(f)
    # Fetch bucket url of the requested type of item
    bucket_url = zenodo_record['repository']['links']['bucket']
    
    task_title = "Reproducibility Assets - Archive GitHub Repository"
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id)

    celery_payload = dict(issue_id = id,
                        bucket_url = bucket_url,
                        comment_id = comment_id,
                        review_repository = reviewRepository,
                        repository_url = repository_url,
                        task_title=task_title)

    task_result = zenodo_upload_repository_task.apply_async(args=[celery_payload])
    
    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Started uploading the repository.")
        response = make_response(jsonify(f"Celery task assigned successfully {task_result.task_id}"),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)
    return response

docs.register(zenodo_upload_repository_post)

@app.route('/api/zenodo/upload/book', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Upload the built book to the respective zenodo deposit.', tags=['Zenodo'])
@use_kwargs(DatasyncSchema())
def zenodo_upload_book_post(user,id,repository_url):
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    issue_id = id

    fname = f"zenodo_deposit_NeuroLibre_{issue_id:05d}.json"
    local_file = os.path.join(get_deposit_dir(issue_id), fname)
    with open(local_file, 'r') as f:
        zenodo_record = json.load(f)
    # Fetch bucket url of the requested type of item
    bucket_url = zenodo_record['book']['links']['bucket']
    
    task_title = "Reproducibility Assets - Archive Jupyter Book"
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id)

    celery_payload = dict(issue_id = id,
                          bucket_url = bucket_url,
                          comment_id = comment_id,
                          review_repository = reviewRepository,
                          repository_url = repository_url,
                          task_title=task_title)

    task_result = zenodo_upload_book_task.apply_async(args=[celery_payload])

    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Started uploading the book.")
        response = make_response(jsonify(f"Celery task assigned successfully {task_result.task_id}"),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)
    return response

docs.register(zenodo_upload_book_post)

@app.route('/api/zenodo/upload/docker', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Upload the docker image to the respective zenodo deposit.', tags=['Zenodo'])
@use_kwargs(DatasyncSchema())
def zenodo_upload_docker_post(user,id,repository_url):
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    issue_id = id

    fname = f"zenodo_deposit_NeuroLibre_{issue_id:05d}.json"
    local_file = os.path.join(get_deposit_dir(issue_id), fname)
    with open(local_file, 'r') as f:
        zenodo_record = json.load(f)
    # Fetch bucket url of the requested type of item
    bucket_url = zenodo_record['docker']['links']['bucket']
    
    task_title = "Reproducibility Assets - Archive Docker Image"
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id)

    celery_payload = dict(issue_id = id,
                          bucket_url = bucket_url,
                          comment_id = comment_id,
                          review_repository = reviewRepository,
                          repository_url = repository_url,
                          task_title=task_title)

    task_result = zenodo_upload_docker_task.apply_async(args=[celery_payload])

    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Started docker upload sequence.")
        response = make_response(jsonify(f"Celery task assigned successfully {task_result.task_id}"),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)
    return response

docs.register(zenodo_upload_docker_post)

@app.route('/api/zenodo/status', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Get zenodo status for a submission.', tags=['Zenodo'])
@use_kwargs(IDSchema())
def api_zenodo_status(user,id):
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    status_msg = zenodo_get_status(id)
    response = gh_create_comment(github_client,reviewRepository,id,status_msg)
    if response:
        response = make_response(jsonify(f"Posted on the issue."),200)
    else:
        response = make_response(jsonify(f"Server problem."),500)
    return response

@app.route('/api/zenodo/publish', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Publish uploaded zenodo records for archival for a given submission ID.', tags=['Zenodo'])
@use_kwargs(DatasyncSchema())
def api_zenodo_publish(user,id,repository_url):

    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    issue_id = id

    task_title = "Publish Reproducibility Assets"
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id)

    celery_payload = dict(task_title = task_title,
                          issue_id= issue_id,
                          review_repository = reviewRepository,
                          comment_id = comment_id,
                          repository_url = repository_url)

    task_result = zenodo_publish_task.apply_async(args=[celery_payload])

    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Started uploading the repository.")
        response = make_response(jsonify(f"Celery task assigned successfully {task_result.task_id}"),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)

    return response
# Register endpoint to the documentation
docs.register(api_zenodo_publish)

docs.register(api_zenodo_status)

@app.route('/api/zenodo/buckets', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Create zenodo buckets (i.e., records) for a submission.', tags=['Zenodo'])
@use_kwargs(BucketsSchema())
def api_zenodo_post(user,id,repository_url):

    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    issue_id = id

    data_archive_exists = gh_read_from_issue_body(github_client,reviewRepository,issue_id,"data-archive")

    if data_archive_exists:
        archive_assets = ["book","repository","docker"]
    else:
        archive_assets = ["book","repository","data","docker"]

    # We need the list of authors and their ORCID, this will 
    # be fetched from the paper.md in the tarhet repository
    paper_string = gh_get_paper_markdown(github_client,repository_url)
    paper_data = parse_front_matter(paper_string)

    if not paper_data:
       comment = f"&#128308; Cannot extract metadata from the front-matter of the `paper.md` for {repository_url}."
       gh_create_comment(github_client,reviewRepository,issue_id,comment)
       return make_response(jsonify(f"Problem with parsing paper.md for {repository_url}"),404)

    task_title = "Reproducibility Assets - Create Zenodo buckets"
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id,paper_data['authors'])

    celery_payload = dict(task_title = task_title,
                          issue_id= issue_id,
                          review_repository = reviewRepository,
                          comment_id = comment_id,
                          archive_assets = archive_assets,
                          paper_data = paper_data,
                          repository_url = repository_url)

    task_result = zenodo_create_buckets_task.apply_async(args=[celery_payload])

    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "")
        response = make_response(jsonify(f"Celery task assigned successfully {task_result.task_id}"),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)
    return response

# Register endpoint to the documentation
docs.register(api_zenodo_post)

@app.route('/api/zenodo/upload', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Upload an item to the respective zenodo bucket (book, repository, data or docker image).', tags=['Zenodo'])
@use_kwargs(UploadSchema())
def api_upload_post(user,issue_id,repository_address,item,item_arg,fork_url,commit_fork):
    """
    Uploads one item at a time (book, repository, data or docker image) to zenodo 
    for the buckets that have been created.
    """
    repofork = fork_url.split("/")[-1]
    fork_repo = fork_url.split("/")[-2]
    fork_provider = fork_url.split("/")[-3]
    if not ((fork_provider == "github.com") | (fork_provider == "gitlab.com")):
        flask.abort(400)
    def run():
        ZENODO_TOKEN = os.getenv('ZENODO_API')
        params = {'access_token': ZENODO_TOKEN}
        # Read json record of the deposit
        fname = f"zenodo_deposit_NeuroLibre_{'%05d'%issue_id}.json"
        local_file = os.path.join(get_deposit_dir(issue_id), fname)
        with open(local_file, 'r') as f:
            zenodo_record = json.load(f)
        # Fetch bucket url of the requested type of item
        bucket_url = zenodo_record[item]['links']['bucket']
        if item == "book":
           # We will archive the book created through the forked repository.
           local_path = os.path.join("/DATA", "book-artifacts", fork_repo, fork_provider, repofork, commit_fork, "_build", "html")
           # Descriptive file name
           zenodo_file = os.path.join(get_archive_dir(issue_id),f"JupyterBook_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}")
           # Zip it!
           shutil.make_archive(zenodo_file, 'zip', local_path)
           zpath = zenodo_file + ".zip"
        
           with open(zpath, "rb") as fp:
            r = requests.put(f"{bucket_url}/JupyterBook_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                    params=params,
                                    data=fp)
           if not r:
            error = {"reason":f"404: Cannot upload {zpath} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
            yield "\n" + json.dumps(error)
            yield ""
           else:
            tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
            log_file = os.path.join(get_deposit_dir(issue_id), tmp)
            with open(log_file, 'w') as outfile:
                    json.dump(r.json(), outfile)
            
            yield "\n" + json.dumps(r.json())
            yield ""

        elif item == "docker":

            # If already exists, do not pull again, but let them know.
            expect = os.path.join(get_archive_dir(issue_id),f"DockerImage_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.tar.gz")
            check_docker = os.path.exists(expect)

            if check_docker:
                yield f"\n already exists {expect}"
                yield f"\n uploading to zenodo"
                with open(expect, "rb") as fp:
                        r = requests.put(f"{bucket_url}/DockerImage_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                        params=params,
                                        data=fp)
                # TO_DO: Write a function to handle this, too many repetitions rn.
                if not r:
                    error = {"reason":f"404: Cannot upload {in_r[1]} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                    yield "\n" + json.dumps(error)
                    yield ""
                else:
                    tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
                    log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                    with open(log_file, 'w') as outfile:
                            json.dump(r.json(), outfile)

                    yield "\n" + json.dumps(r.json())
                    yield ""
            else:
                docker_login()
                # Docker image address should be here
                docker_pull(item_arg)
                in_r = docker_save(item_arg,issue_id,commit_fork)
                # in_r[0] os.system status, in_r[1] saved docker image absolute path

                docker_logout()
                if in_r[0] == 0:
                    # Means that saved successfully, upload to zenodo.
                    with open(in_r[1], "rb") as fp:
                        r = requests.put(f"{bucket_url}/DockerImage_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                        params=params,
                                        data=fp)
                    # TO_DO: Write a function to handle this, too many repetitions rn.
                    if not r:
                        error = {"reason":f"404: Cannot upload {in_r[1]} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                        yield "\n" + json.dumps(error)
                        yield ""
                    else:
                        tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
                        log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                        with open(log_file, 'w') as outfile:
                                json.dump(r.json(), outfile)

                        yield "\n" + json.dumps(r.json())
                        yield ""
                else:
                # Cannot save docker image successfully
                    error = {"reason":f"404: Cannot save requested docker image as tar.gz: {item_arg}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                    yield "\n" + json.dumps(error)
                    yield ""

        elif item == "repository":
            
            download_url_main = f"{fork_url}/archive/refs/heads/main.zip"
            download_url_master = f"{fork_url}/archive/refs/heads/master.zip"

            zenodo_file = os.path.join(get_archive_dir(issue_id),f"GitHubRepo_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip")
            
            # REFACTOR HERE AND MANAGE CONDITIONS CLEANER.
            # Try main first
            resp = os.system(f"wget -O {zenodo_file} {download_url_main}")
            if resp != 0:
                # Try master 
                resp2 = os.system(f"wget -O {zenodo_file} {download_url_master}")
                if resp2 != 0:
                    error = {"reason":f"404: Cannot download repository at {download_url_main} or from master branch.", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                    yield "\n" + json.dumps(error)
                    yield ""
                    # TRY FLASK.ABORT(code,custom) here for refactoring.
                else:
                    # Upload to Zenodo
                    with open(zenodo_file, "rb") as fp:
                        r = requests.put(f"{bucket_url}/GitHubRepo_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                        params=params,
                                        data=fp)
                        if not r:
                            error = {"reason":f"404: Cannot upload {zenodo_file} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                            yield "\n" + json.dumps(error)
                            yield ""
                        else:
                            tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
                            log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                            with open(log_file, 'w') as outfile:
                                    json.dump(r.json(), outfile)
                        # Return answer to flask
                        yield "\n" + json.dumps(r.json())
                        yield ""
            else: 
                # main worked
                # Upload to Zenodo
                with open(zenodo_file, "rb") as fp:
                    r = requests.put(f"{bucket_url}/GitHubRepo_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                    params=params,
                                    data=fp)
                    if not r:
                            error = {"reason":f"404: Cannot upload {zenodo_file} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                            yield "\n" + json.dumps(error)
                            yield ""
                    else:
                        tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
                        log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                        with open(log_file, 'w') as outfile:
                                json.dump(r.json(), outfile)
                        # Return answer to flask
                        yield "\n" + json.dumps(r.json())
                        yield ""

        elif item == "data":

           expect = os.path.join(get_archive_dir(issue_id),f"Dataset_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip")
           check_data = os.path.exists(expect)

           if check_data:
            yield f"\n Compressed data already exists Dataset_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip"
            zpath = expect
           else:
            # We will archive the data synced from the test server. (item_arg is the project_name, indicating that the 
            # data is stored at the /DATA/project_name folder)
            local_path = os.path.join("/DATA", item_arg)
            # Descriptive file name
            zenodo_file = os.path.join(get_archive_dir(issue_id),f"Dataset_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}")
            # Zip it!
            shutil.make_archive(zenodo_file, 'zip', local_path)
            zpath = zenodo_file + ".zip"

           # UPLOAD data to zenodo
           yield f"\n Attempting zenodo upload."
           with open(zpath, "rb") as fp:
            r = requests.put(f"{bucket_url}/Dataset_10.55458_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.zip",
                                    params=params,
                                    data=fp)

            if not r:
                error = {"reason":f"404: Cannot upload {zenodo_file} to {bucket_url}", "commit_hash":commit_fork, "repo_url":fork_repo,"issue_id":issue_id}
                yield "\n" + json.dumps(error)
                yield ""
            else:
                tmp = f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_{commit_fork[0:6]}.json"
                log_file = os.path.join(get_deposit_dir(issue_id), tmp)
                with open(log_file, 'w') as outfile:
                        json.dump(r.json(), outfile)
                # Return answer to flask
                yield "\n" + json.dumps(r.json())
                yield ""

    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_upload_post)

@app.route('/api/zenodo/list', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Get the list of Zenodo records that are available for a given submission.', tags=['Zenodo'])
@use_kwargs(ListSchema())
def api_zenodo_list_post(user,issue_id):
    """
    List zenodo records for a given technical screening ID.
    """
    def run():
        path = f"/DATA/zenodo_records/{'%05d'%issue_id}"
        if not os.path.exists(path):
            yield "<br> :neutral_face: I could not find any Zenodo-related records on NeuroLibre servers. Maybe start with `roboneuro zenodo deposit`?"
        else:
            files = os.listdir(path)
            yield "<br> These are the Zenodo records I have on NeuroLibre servers:"
            yield "<ul>"
            for file in files:
                yield f"<li>{file}</li>"
            yield "</ul>"
    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_zenodo_list_post)

@app.route('/api/zenodo/flush', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Flush records and remove respective uploads from Zenodo, if available for a submission ID.', tags=['Zenodo'])
@use_kwargs(DeleteSchema())
def api_zenodo_flush_post(user,issue_id,items):
    """
    Delete buckets and uploaded files from zenodo if exist for a requested item type.
    """
    def run():
    # Set env
        ZENODO_TOKEN = os.getenv('ZENODO_API')
        headers = {"Content-Type": "application/json","Authorization": "Bearer {}".format(ZENODO_TOKEN)}
        # Read json record of the deposit
        fname = f"zenodo_deposit_NeuroLibre_{'%05d'%issue_id}.json"
        local_file = os.path.join(get_deposit_dir(issue_id), fname)
        dat2recmap = {"data":"Dataset","repository":"GitHubRepo","docker":"DockerImage","book":"JupyterBook"}
        
        with open(local_file, 'r') as f:
            zenodo_record = json.load(f)

        for item in items: 
            self_url = zenodo_record[item]['links']['self']
            # Delete the deposit
            r3 = requests.delete(self_url,headers=headers)
            if r3.status_code == 204:
                yield f"\n Deleted {item} deposit successfully at {self_url}."
                yield ""
                # We need to delete these from the Zenodo records file
                if item in zenodo_record: del zenodo_record[item]
                # Flush ALL the upload records (json) associated with the item
                tmp_record = glob.glob(os.path.join(get_deposit_dir(issue_id),f"zenodo_uploaded_{item}_NeuroLibre_{'%05d'%issue_id}_*.json"))
                if tmp_record:
                    for f in tmp_record:
                        os.remove(f)
                        yield f"\n Deleted {f} record from the server."
                # Flush ALL the uploaded files associated with the item
                tmp_file = glob.glob(os.path.join(get_archive_dir(issue_id),f"{dat2recmap[item]}_10.55458_NeuroLibre_{'%05d'%issue_id}_*.zip"))
                if tmp_file:
                    for f in tmp_file:
                        os.remove(f)
                        yield f"\n Deleted {f} record from the server."
            elif r3.status_code == 403: 
                yield f"\n The {item} archive has already been published, cannot be deleted."
                yield ""
            elif r3.status_code == 410:
                yield f"\n The {item} deposit does not exist."
                yield ""
        # Write zenodo record json file or rm existing one if empty at this point
        # Delete the old one
        os.remove(local_file)
        yield f"\n Deleted old {local_file} record from the server."
        # Write the new one
        if zenodo_record:
            with open(local_file, 'w') as outfile:
                json.dump(zenodo_record, outfile)
            yield f"\n Created new {local_file}."
        else:
            yield f"\n All the deposit records have been deleted."

    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_zenodo_flush_post)

@app.route('/api/data/sync', methods=['POST'])
@htpasswd.required
@doc(description='Transfer data from the preview to the production server based on the project name.', tags=['Data'])
@use_kwargs(DatasyncSchema())
def api_data_sync_post(user,id,repository_url):
    # Create a comment in the review issue. 
    # The worker will update that depending on the  state of the task.
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    issue_id = id
    #app.logger.debug(f'{issue_id} {repository_url}')
    project_name = gh_get_project_name(github_client,repository_url)
    #app.logger.debug(f'{project_name}')
    task_title = "DATA TRANSFER (Preview --> Preprint)"
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id)
    #app.logger.debug(f'{comment_id}')
    # Start the BG task.
    task_result = rsync_data_task.apply_async(args=[comment_id, issue_id, project_name, reviewRepository])
    # If successfully queued the task, update the comment
    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "")
        response = make_response(jsonify("Celery task assigned successfully."),200)
    else:
    # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)
    #response.mimetype = "text/plain"
    return response

# Register endpoint to the documentation
docs.register(api_data_sync_post)

@app.route('/api/book/sync', methods=['POST'])
@htpasswd.required
@doc(description='Transfer a built book from the preview to the production server based on the project name.', tags=['Book'])
@use_kwargs(BooksyncSchema())
def api_books_sync_post(user,id,repository_url,commit_hash="HEAD"):
    # Kwargs should match received json request payload fields 
    # assigning this into issue_id for clarity.
    issue_id = id
    [owner, repo, provider] = get_owner_repo_provider(repository_url,provider_full_name=True)
    # Create fork URL 
    repo_url = f"https://{provider}/roboneurolibre/{repo}"
    commit_hash = format_commit_hash(repo_url,commit_hash)
    server = f"https://{serverName}.{serverDomain}"
    # TODO: Implement this into a class not to 
    # repeat this, make sure that async call friendly
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    # Task name
    task_title = "REPRODUCIBLE PREPRINT TRANSFER (Preview --> Preprint)"
    # Make comment under the issue
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id)
    # Start Celery task
    app.logger.debug(f"{repo_url}|{commit_hash}|{comment_id}|{issue_id}|{reviewRepository}|{server}")
    task_result = rsync_book_task.apply_async(args=[repo_url, commit_hash, comment_id, issue_id, reviewRepository, server])
    # Update the comment depending on task_id existence.
    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "")
        response = make_response(jsonify("Celery task assigned successfully."),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)

    return response

# Register endpoint to the documentation
docs.register(api_books_sync_post)

@app.route('/api/production/start', methods=['POST'])
@htpasswd.required
@doc(description='Fork user repository into roboneurolibre and update _config and _toc.', tags=['Production'])
@use_kwargs(ProdStartSchema())
def api_production_start_post(user,id,repository_url,commit_hash="HEAD"):
    issue_id = id
    repo_url = repository_url
    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    task_title = "INITIATE PRODUCTION (Fork and Configure)"
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id)
    # Start BG process
    task_result = fork_configure_repository_task.apply_async(args=[repo_url, comment_id, issue_id, reviewRepository])
    # Update the comment depending on task_id existence.
    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "")
        response = make_response(jsonify("Celery task assigned successfully."),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)
    return response

docs.register(api_production_start_post)

# This is named as a binder/build instead of /book/build due to its context 
# Production server BinderHub deployment does not build a book.
@app.route('/api/binder/build', methods=['POST'])
@htpasswd.required
@doc(description='Request a binderhub build on the production server for a given repo and hash. Repository must belong to the roboneurolibre organization.', tags=['Binder'])
@use_kwargs(BinderSchema())
def api_binder_build(user,repo_url, commit_hash):

    binderhub_request = run_binder_build_preflight_checks(repo_url,commit_hash,build_rate_limit, binderName, domainName)

    # Request build from the preview binderhub instance
    app.logger.info(f"Starting BinderHub request at {binderhub_request } ...")

    lock_filename = get_lock_filename(repo_url)

    response = requests.get(binderhub_request, stream=True)
    if response.ok:
        # Forward the response as an event stream
        def generate():
            for line in response.iter_lines():
                if line:
                    # Fetch streamed block
                    event_string = line.decode("utf-8")
                    try:
                        # Try getting an event object if the emit message
                        # is json (e.g., may be keepalive otherwise)
                        event = json.loads(event_string.split(': ', 1)[1])

                        # https://binderhub.readthedocs.io/en/latest/api.html
                        # MUST close response when phase is failed
                        if event.get('phase') == 'failed':
                            response.close()
                            # Remove the lock as binder build failed.
                            app.logger.info(f"[FAILED] BinderHub build {binderhub_request}.")
                            os.remove(lock_filename)
                            return

                        message = event.get('message')
                        if message:
                            # Only print when phase emits a message to
                            # keep the logs neat.
                            yield message
                    # An exception to handle 
                    # for Gunicorn asynchronous worker (gevent)
                    except GeneratorExit:
                        pass
                    except:
                        # Pass other events
                        pass

    os.remove(lock_filename)
    return flask.Response(generate(), mimetype='text/event-stream')

@app.route('/api/pdf/draft', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Build extended PDF for a submission.', tags=['Extended PDF'])
@use_kwargs(BucketsSchema())
def api_pdf_draft(user,id,repository_url):

    GH_BOT=os.getenv('GH_BOT')
    github_client = Github(GH_BOT)
    issue_id = id

    task_title = "Extended PDF - Build draft"
    comment_id = gh_template_respond(github_client,"pending",task_title,reviewRepository,issue_id)

    celery_payload = dict(issue_id = id,
                        comment_id = comment_id,
                        review_repository = reviewRepository,
                        repository_url = repository_url,
                        task_title=task_title)

    task_result = preprint_build_pdf_draft.apply_async(args=[celery_payload])
    if task_result.task_id is not None:
        gh_template_respond(github_client,"received",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "")
        response = make_response(jsonify("Celery task assigned successfully."),200)
    else:
        # If not successfully assigned, fail the status immediately and return 500
        gh_template_respond(github_client,"failure",task_title,reviewRepository,issue_id,task_result.task_id,comment_id, "Internal server error: NeuroLibre background task manager could not receive the request.")
        response = make_response(jsonify("Celery could not start the task."),500)
    return response
# Register endpoint to the documentation
docs.register(api_pdf_draft)

@app.route('/api/test', methods=['GET'])
@htpasswd.required
@doc(description='Check if SSL verified authentication is functional.', tags=['Tests'])
def api_preprint_test(user):
     response = make_response("Preprint server login successful. <3 NeuroLibre",200)
     response.mimetype = "text/plain"
     return response

docs.register(api_preprint_test)

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
    elif task.state == 'PROGRESS':
        remaining = task.info.get('remaining', 0) if task.info else 0
        response = {
            'status': 'sleeping',
            'remaining': remaining
        }
    elif task.state == 'SUCCESS':
        response = {
            'status': 'done sleeping for 60 seconds'
        }
    else:
        response = {
            'status': 'failed to sleep'
        }
    return jsonify(response)

docs.register(get_task_status_test)