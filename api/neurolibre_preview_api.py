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

# GLOBAL VARIABLES
BOOK_PATHS = "/DATA/book-artifacts/*/*/*/*.tar.gz"
BOOK_URL = "http://neurolibre-data-prod.conp.cloud/book-artifacts"
DOCKER_REGISTRY = "https://binder-registry.conp.cloud"

app = flask.Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

gunicorn_error_logger = logging.getLogger('gunicorn.error')
app.logger.handlers.extend(gunicorn_error_logger.handlers)
app.logger.setLevel(logging.DEBUG)
app.logger.debug('NeuroLibre preview API.')

app.config["DEBUG"] = True
app.config['FLASK_HTPASSWD_PATH'] = '/home/ubuntu/.htpasswd'
htpasswd = HtPasswdAuth(app)


@app.route('/api/v1/about', methods=['GET'])
def home():
    return """
           <h1>NeuroLibre preview API</h1>
           <p> Explanation goes here</p>
           {}
           """.format(doc())