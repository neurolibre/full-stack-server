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
from schema import BuildSchema
from flask_htpasswd import HtPasswdAuth
from dotenv import load_dotenv
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_apispec import FlaskApiSpec, marshal_with, doc, use_kwargs
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from neurolibre_celery_tasks import rsync, celery_app,sleep_task
from celery.events.state import State

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

# Create a build_locks folder to control rate limits
if not os.path.exists(os.path.join(os.getcwd(),'build_locks')):
    os.makedirs(os.path.join(os.getcwd(),'build_locks'))

@app.route('/api/book/build', methods=['POST'])
@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@marshal_with(None,code=200,description="Accept text/eventstream for BinderHub build logs. Keepalive 30s.")
@doc(description='Endpoint for building reproducibility assets on the preview BinderHub instance: Repo2Data, (Binder) Repo2Docker, Jupyter Book.', tags=['Book'])
@use_kwargs(BuildSchema())
def api_book_build(user, repo_url,commit_hash):
    """
    Connect to binderhub build eventstream and forward it to 
    the client.
    TODO: Celery.
    """

    binderhub_request = run_binder_build_preflight_checks(repo_url,commit_hash,build_rate_limit, binderName, domainName)

    app.logger.info(f"Starting BinderHub request at {binderhub_request } ...")

    lock_filename = get_lock_filename(repo_url)

    # START EVENTSTREAM | BINDER --> THIS ENDPOINT --> CLIENT |
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

            # After the upstream closes, check the server if there's 
            # a book built successfully.
            book_status = book_get_by_params(commit_hash=commit_hash)

            # For now, remove the block either way.
            # The main purpose is to avoid triggering
            # a build for the same request. Later on
            # you may choose to add dead time after a successful build.
            os.remove(lock_filename)

            # Append book-related response downstream
            if not book_status:
                # These flags will determine how the response will be 
                # interpreted and returned outside the generator
                error = {"status":"404", "message":"Jupyter book built was not successful!", "commit_hash":commit_hash, "binderhub_url":binderhub_request}
                yield "<-- Book Build Failed -->\n"
                yield f"{json.dumps(error)}"
            else:
                yield "<-- Book Build Successful -->\n"
                yield f"{json.dumps(book_status[0])}"
        # As our API is behind Cloudflare, long responses trigger a timeout 
        # if we parse the response here and send it as proper json. 
        # That's why we stream from here, and deal with parsing at the 
        # receiver's end (roboneuro ruby)
        return flask.Response(generate(), mimetype='text/event-stream')

# Register endpoint to the documentation
docs.register(api_book_build)

@app.route('/api/test', methods=['GET'])
@htpasswd.required
@doc(description='Check if SSL verified authentication is functional.', tags=['Test'])
def api_preview_test(user):
    response = make_response(jsonify("Preview server login successful. <3 NeuroLibre"),200)
    response.mimetype = "text/plain"
    return response

@app.route('/api/celery/test', methods=['GET'])
@htpasswd.required
@doc(description='Check if SSL verified authentication is functional.', tags=['Test'])
def api_celery_test(user):
    seconds = 60
    task = sleep_task.apply_async(args=[seconds])
    return f'Celery test started: {task.id}'

docs.register(api_preview_test)

@app.route('/api/task/<task_id>')
def get_task_status(task_id):
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

    