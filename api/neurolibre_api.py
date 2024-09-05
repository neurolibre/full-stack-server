from flask import Flask
from flask_htpasswd import HtPasswdAuth
from werkzeug.middleware.proxy_fix import ProxyFix
from flask_apispec import FlaskApiSpec
from apispec import APISpec
from apispec.ext.marshmallow import MarshmallowPlugin
from dotenv import load_dotenv
import logging
import os
import yaml
import neurolibre_common_api
from functools import wraps

class NeuroLibreAPI:
    def __init__(self, name, config_files):
        self.app = Flask(name)
        self.load_config(config_files)
        self.setup_logging()
        self.setup_auth()
        self.setup_proxy()
        self.setup_docs()
        self.register_blueprint(neurolibre_common_api.common_api)
        common_endpoints = [neurolibre_common_api.api_get_book,
                            neurolibre_common_api.api_get_books,
                            neurolibre_common_api.api_heartbeat,
                            neurolibre_common_api.api_unlock_build,
                            neurolibre_common_api.api_preview_list]
        self.register_docs_common_endpoints(common_endpoints)

    def get_app(self):
        return self.app
    
    def load_config(self, config_files):
        load_dotenv()
        for file in config_files:
            config = self.load_yaml(file)
            self.app.config.update(config)

    def load_yaml(self, file):
        with open(file, 'r') as f:
            return yaml.safe_load(f)

    def setup_logging(self):
        gunicorn_logger = logging.getLogger('gunicorn.error')
        self.app.logger.handlers.extend(gunicorn_logger.handlers)
        self.app.logger.setLevel(logging.DEBUG)

    def setup_auth(self):
        self.app.config['FLASK_HTPASSWD_PATH'] = os.getenv('AUTH_KEY')
        self.htpasswd = HtPasswdAuth(self.app)

    def setup_proxy(self):
        self.app.wsgi_app = ProxyFix(self.app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

    def setup_docs(self):
        spec = APISpec(
            title=f"Neurolibre {self.app.config['SERVER_SLUG']} API",
            version='v1',
            plugins=[MarshmallowPlugin()],
            openapi_version="3.0.2",
            info=dict(
                description=self.app.config['SERVER_ABOUT'] + self.app.config['SERVER_LOGO'],
                contact=self.app.config['SERVER_CONTACT'],
                termsOfService=self.app.config['SERVER_TOS']
            ),
            servers=[{
                'url': f"https://{self.app.config['SERVER_SLUG']}.{self.app.config['SERVER_DOMAIN']}/",
                'description': f"{self.app.config['SERVER_SLUG'].capitalize()} server.",
                'variables': {'SERVER_NAME': {'default': self.app.config['SERVER_SLUG']}}
            }]
        )
        self.app.config.update({'APISPEC_SPEC': spec})
        api_key_scheme = {"type": "http", "scheme": "basic"}
        spec.components.security_scheme("basicAuth", api_key_scheme)
        self.docs = FlaskApiSpec(app=self.app, document_options=False)

    def register_blueprint(self, blueprint):
        self.app.register_blueprint(blueprint)

    def register_docs_common_endpoints(self, endpoints):
        for endpoint in endpoints:
            self.docs.register(endpoint, blueprint="common_api")
    
    def auth_required(self, f):
        @wraps(f)
        def decorated(*args, **kwargs):
            return self.htpasswd.required(f)(*args, **kwargs)
        return decorated

    def run(self):
        self.app.run()