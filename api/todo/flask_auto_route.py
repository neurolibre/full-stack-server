from flask import Flask
from flask_apispec import use_kwargs, doc
from flask_htpasswd import HtPasswdAuth
from common import load_yaml
from endpoint_handler import handle_endpoint
import importlib

def generate_routes(app: Flask, htpasswd: HtPasswdAuth):
    
    endpoints = load_yaml('api/endpoints.yaml')

    for endpoint_name, config in endpoints.items():
        route = config['route']
        methods = config['methods']
        schema_name = config['schema']
        description = config['description']
        tags = config['tags']
        parameters = config['parameters']

        # Dynamically import the schema
        schema_module = importlib.import_module('schemas')  # Adjust this import as needed
        schema = getattr(schema_module, schema_name)

        # Create the function signature dynamically
        param_str = ", ".join(p['name'] + "=" + str(p.get('default', 'None')) for p in parameters)
        func_str = f"def {endpoint_name}({param_str}):\n"
        func_str += f"    return handle_endpoint('{endpoint_name}', {', '.join(p['name'] for p in parameters)})"

        # Create the function object
        func_locals = {}
        exec(func_str, globals(), func_locals)
        view_func = func_locals[endpoint_name]

        # Apply decorators
        view_func = app.route(route, methods=methods)(view_func)
        view_func = htpasswd.required(view_func)
        view_func = marshal_with(None, code=422, description="Cannot validate the payload, missing or invalid entries.")(view_func)
        view_func = use_kwargs(schema())(view_func)
        view_func = doc(description=description, tags=tags)(view_func)

        # Add the view function to the app
        app.view_functions[endpoint_name] = view_func


"""
This will be then followed by
"""

# from flask import Flask
# from flask_htpasswd import HtPasswdAuth
# from route_generator import generate_routes

# app = Flask(__name__)
# htpasswd = HtPasswdAuth(app)

# generate_routes(app, htpasswd)

# if __name__ == '__main__':
#     app.run()