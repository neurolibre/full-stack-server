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
from flask_htpasswd import HtPasswdAuth
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_apispec import FlaskApiSpec, marshal_with, doc, use_kwargs
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from marshmallow import Schema, fields

# THIS IS NEEDED UNLESS FLASK IS CONFIGURED TO AUTO-LOAD!
load_dotenv()

app = flask.Flask(__name__)

app.register_blueprint(neurolibre_common_api.common_api)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
# If DEBUG is true prettyprint False will be overridden.
app.config["DEBUG"] = False
app.config["JSONIFY_PRETTYPRINT_REGULAR"] = False

gunicorn_error_logger = logging.getLogger('gunicorn.error')
app.logger.handlers.extend(gunicorn_error_logger.handlers)
app.logger.setLevel(logging.DEBUG)
app.logger.debug('NeuroLibre preview API.')

AUTH_KEY=os.getenv('AUTH_KEY')
app.config['FLASK_HTPASSWD_PATH'] = AUTH_KEY
htpasswd = HtPasswdAuth(app)

# KEEP BINDERHUB URL AND DOMAIN UP TO DATE
binderName = "test"
domainName = "conp.cloud"
build_rate_limit = 30 #minutes

logo ="<img style=\"width:200px;\" src=\"https://github.com/neurolibre/brand/blob/main/png/logo_preprint.png?raw=true\"></img>"
serverName = 'preview' 
serverDescription = 'Preview server'
serverContact = dict(name="NeuroLibre",url="https://neurolibre.org",email="conpdev@gmail.com")
serverTOS = "http://docs.neurolibre.org"
serverAbout = f"<h3>Endpoints to handle preview & screening tasks <u>prior to the submission & screening</u>.</h3>{logo}"

# API specifications displayed on the swagger UI 
spec = APISpec(
        title="Neurolibre preview & screening API",
        version='v1',
        plugins=[MarshmallowPlugin()],
        openapi_version="3.0.2",
        info=dict(description=serverAbout,contact=serverContact,termsOfService=serverTOS),
        servers = [{'url': 'https://{serverName}.neurolibre.org/','description':'Production server.', 'variables': {'serverName':{'default':serverName}}}]
        )

# SWAGGER UI URLS. Pay attention to /swagger/ vs /swagger.
app.config.update({
    'APISPEC_SPEC': spec,
    'APISPEC_SWAGGER_URL': '/swagger/',
    'APISPEC_SWAGGER_UI_URL': '/documentation'
})

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

class BuildSchema(Schema):
    """
    Defines payload types and requirements for book build request.
    """
    repo_url = fields.Str(required=True,description="Full URL of a NeuroLibre compatible repository to be used for building the book.")
    commit_hash = fields.String(required=True,dump_default="HEAD",description="Commit SHA to be checked out for building the book. Defaults to HEAD.")

@app.route('/api/forward', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Send a book (+binder) build request to the preview server BinderHub.', tags=['Book'])
@use_kwargs(BuildSchema())
def forward_eventstream(user, repo_url,commit_hash):
    app.logger.debug(f"Received request: {repo_url} and {commit_hash}")
    repo = repo_url.split("/")[-1]
    user_repo = repo_url.split("/")[-2]
    provider = repo_url.split("/")[-3]
    app.logger.debug(f"Parsed request: {repo} and {user_repo} and {provider}")

    if provider == "github.com":
        provider = "gh"
    elif provider == "gitlab.com":
        provider = "gl"

    if commit_hash == "HEAD":
        refs = git.cmd.Git().ls_remote(repo_url).split("\n")
        for ref in refs:
            if ref.split('\t')[1] == "HEAD":
                commit_hash = ref.split('\t')[0]
    
    binderhub_request = f"https://{binderName}.{domainName}/build/{provider}/{user_repo}/{repo}.git/{commit_hash}"
    lock_filepath = f"./{provider}_{user_repo}_{repo}.lock"

    app.logger.debug(f"{binderhub_request}")

    ## Setting build rate limit
    if os.path.exists(lock_filepath):
        lock_age_in_secs = time.time() - os.path.getmtime(lock_filepath)
        if lock_age_in_secs > build_rate_limit*60:
            app.logger.debug(f"Removing lock")
            os.remove(lock_filepath)
    
    app.logger.debug(f"Another {lock_filepath}")

    if os.path.exists(lock_filepath):
        binderhub_exists_link = f"https://{binderName}.{domainName}/v2/{provider}/{user_repo}/{repo}/{commit_hash}"
        app.logger.debug(f"Trying to return 409")
        flask.abort(409, binderhub_exists_link)
    else:
        with open(lock_filepath, "w") as f:
            f.write("")
        app.logger.debug(f"Written new lock")

    # Request build from the preview binderhub instance
    app.logger.debug(f"Requesting build stream: {binderhub_request}")
    ######
    response = requests.get(binderhub_request, stream=True)
    if response.ok:
        # Forward the response as an event stream
        def generate():
            for line in response.iter_lines():
                if line:
                    #app.logger.debug(line.decode("utf-8"))
                    event_string = line.decode("utf-8")
                    try:
                        event = json.loads(event_string.split(': ', 1)[1])
                        phase = event.get('phase')
                        # Close the eventstream if phase is "failed"
                        if phase and phase == 'failed':
                            response.close()
                            break
                        elif phase and phase == 'built':
                            yield f'Already built!'
                            yield f'data: {line.decode("utf-8")}\n\n'
                            # return flask.Response(f'data: {line.decode("utf-8")}', status=200)
                        else:
                            yield f'data: {line.decode("utf-8")}\n\n'
                    except:
                        app.logger.debug(f"IndexError bypassed")
                        yield f'data: {line.decode("utf-8")}\n\n'

        os.remove(lock_filepath)
        return flask.Response(generate(), mimetype='text/event-stream')


@app.route('/api/book/build', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Send a book (+binder) build request to the preview server BinderHub.', tags=['Book'])
@use_kwargs(BuildSchema())
def api_book_build(user, repo_url,commit_hash):
    app.logger.debug(f"Received request: {repo_url} and {commit_hash}")
    repo = repo_url.split("/")[-1]
    user_repo = repo_url.split("/")[-2]
    provider = repo_url.split("/")[-3]
    app.logger.debug(f"Parsed request: {repo} and {user_repo} and {provider}")

    if provider == "github.com":
        provider = "gh"
    elif provider == "gitlab.com":
        provider = "gl"

    if commit_hash == "HEAD":
        refs = git.cmd.Git().ls_remote(repo_url).split("\n")
        for ref in refs:
            if ref.split('\t')[1] == "HEAD":
                commit_hash = ref.split('\t')[0]
    
    binderhub_request = f"https://{binderName}.{domainName}/build/{provider}/{user_repo}/{repo}.git/{commit_hash}"
    lock_filepath = f"./{provider}_{user_repo}_{repo}.lock"

    app.logger.debug(f"{binderhub_request}")

    ## Setting build rate limit
    if os.path.exists(lock_filepath):
        lock_age_in_secs = time.time() - os.path.getmtime(lock_filepath)
        if lock_age_in_secs > build_rate_limit*60:
            app.logger.debug(f"Removing lock")
            os.remove(lock_filepath)
    
    app.logger.debug(f"Another {lock_filepath}")

    if os.path.exists(lock_filepath):
        binderhub_exists_link = f"https://{binderName}.{domainName}/v2/{provider}/{user_repo}/{repo}/{commit_hash}"
        app.logger.debug(f"Trying to return 409")
        flask.abort(409, binderhub_exists_link)
    else:
        with open(lock_filepath, "w") as f:
            f.write("")
        app.logger.debug(f"Written new lock")

    # Request build from the preview binderhub instance
    app.logger.debug(f"Requesting build at: {binderhub_request}")
    
    req = requests.get(binderhub_request)
    app.logger.debug(f"Made the request")
    def run():
        for line in req.iter_lines():
            if line:
                yield str(line.decode('utf-8')) + "\n"
        results = book_get_by_params(commit_hash=commit_hash)
        #app.logger.debug(results)
        os.remove(lock_filepath)

        # TODO: Improve this convention.
        if not results:
            error = {"reason":"424: Jupyter book built was not successful!", "commit_hash":commit_hash, "binderhub_url":binderhub_request}
            yield "\n" + json.dumps(error)
            yield ""
        else:
            yield "\n" + json.dumps(results[0])
            yield ""

    return flask.Response(run(), mimetype='text/plain')

# Register endpoint to the documentation
docs.register(api_book_build)

@app.route('/api/test', methods=['POST'])
@htpasswd.required
@doc(description='Check if SSL verified authentication is functional.', tags=['Test'])
def api_preview_test(user):
    return make_response(jsonify("Preview server login successful. <3 NeuroLibre"),200)

docs.register(api_preview_test)