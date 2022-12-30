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
# THIS IS NEEDED UNLESS FLASK IS CONFIGURED TO AUTO-LOAD!
load_dotenv()

app = flask.Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

gunicorn_error_logger = logging.getLogger('gunicorn.error')
app.logger.handlers.extend(gunicorn_error_logger.handlers)
app.logger.setLevel(logging.DEBUG)
app.logger.debug('----POSTING LOGS FROM FLASK TO GUNICORN---')

app.config["DEBUG"] = True
app.config['FLASK_HTPASSWD_PATH'] = '/home/ubuntu/.htpasswd'
htpasswd = HtPasswdAuth(app)


@app.errorhandler(500)
def internal_error(e):
    return "<h1>500</h1><p>Internal server error</p>{}".format(str(e)), 500

@app.errorhandler(400)
def bad_request(e):
    return "<h1>400</h1><p>Bad request, valid requests are:</p>{}".format(doc()), 400

@app.errorhandler(404)
def page_not_found(e):
    return "<h1>404</h1><p>The resource could not be found.</p>", 404

@app.errorhandler(406)
def malformed_specs(e):
    return "<h1>406</h1><p>Given specifications does not conform any content.</p><p>{}</p>".format(str(e)), 406

@app.errorhandler(409)
def same_request(e):
    error = {"reason":"A similar request has been already sent!", "binderhub_url":str(e)}
    return json.dumps(error), 409

@app.errorhandler(424)
def previous_request_failed(e):
    return "<h1>424</h1><p>The request failed due to a previous request.</p><p>{}</p>".format(str(e)), 424

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=29876)
