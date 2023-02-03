from common import *
import flask
import os
import json
import glob
import time
import subprocess
import requests
import shutil
import git
from flask_htpasswd import HtPasswdAuth
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
import logging
import neurolibre_common_api
from flask_apispec import FlaskApiSpec, marshal_with, doc, use_kwargs


from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from marshmallow import Schema, fields
 
# THIS IS NEEDED UNLESS FLASK IS CONFIGURED TO AUTO-LOAD!
load_dotenv()

app = flask.Flask(__name__)

app.register_blueprint(neurolibre_common_api.common_api)

app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
app.config["DEBUG"] = True

gunicorn_error_logger = logging.getLogger('gunicorn.error')
app.logger.handlers.extend(gunicorn_error_logger.handlers)
app.logger.setLevel(logging.DEBUG)
app.logger.debug('NeuroLibre preview API.')

AUTH_KEY=os.getenv('AUTH_KEY')
app.config['FLASK_HTPASSWD_PATH'] = AUTH_KEY
htpasswd = HtPasswdAuth(app)

binderName = "test"
domainName = "conp.cloud"
build_rate_limit = 30 #minutes

logo ="<img style=\"width:200px;\" src=\"https://github.com/neurolibre/brand/blob/main/png/logo_preprint.png?raw=true\"></img>"
serverName = 'preview' 
serverDescription = 'Preview server'
serverContact = dict(name="NeuroLibre",url="https://neurolibre.org",email="conpdev@gmail.com")
serverTOS = "http://docs.neurolibre.org"
serverAbout = f"<h3>Endpoints to handle preview & screening tasks <u>prior to the submission & screening</u>.</h3>{logo}"

spec = APISpec(
        title="Neurolibre preview & screening API",
        version='v1',
        plugins=[MarshmallowPlugin()],
        openapi_version="3.0.2",
        info=dict(description=serverAbout,contact=serverContact,termsOfService=serverTOS),
        servers = [{'url': 'https://{serverName}.neurolibre.org/','description':'Production server.', 'variables': {'serverName':{'default':serverName}}}]
        )

app.config.update({
    'APISPEC_SPEC': spec,
    'APISPEC_SWAGGER_UI_URL': '/documentation'
})

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
    Defines payload types and requirements for creating zenodo records.
    """
    repo_url = fields.Str(required=True,description="Full URL of a NeuroLibre compatible repository to be used for building the book.")
    commit_hash = fields.String(required=True,dump_default="HEAD",description="Commit SHA to be checked out for building the book. Defaults to HEAD.")

@app.route('/api/book/build', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@doc(description='Create zenodo buckets (i.e., records) for a submission.', tags=['Book'])
@use_kwargs(BuildSchema())
def api_book_build(user, repo_url,commit_hash):
    repo = repo_url.split("/")[-1]
    user_repo = repo_url.split("/")[-2]
    provider = repo_url.split("/")[-3]
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

    ## Setting build rate limit
    if os.path.exists(lock_filepath):
        lock_age_in_secs = time.time() - os.path.getmtime(lock_filepath)
        if lock_age_in_secs > build_rate_limit*60:
            os.remove(lock_filepath)
    if os.path.exists(lock_filepath):
        binderhub_exists_link = f"https://{binderName}.{domainName}/v2/{provider}/{user_repo}/{repo}/{commit_hash}"
        flask.abort(429, lock_age_in_secs, binderhub_exists_link)
    else:
        with open(lock_filepath, "w") as f:
            f.write("")
    
    # Request build from the preview binderhub instance
    req = requests.get(binderhub_request)

    def run():
        for line in req.iter_lines():
            if line:
                yield str(line.decode('utf-8')) + "\n"
        results = book_get_by_params(commit_hash=commit_hash)
        print(results)
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