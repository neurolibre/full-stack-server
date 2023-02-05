from flask import Response, Blueprint, abort, jsonify, request, current_app
from common import *
from flask_apispec import FlaskApiSpec, marshal_with, doc, use_kwargs
from marshmallow import Schema, fields
import logging

common_api = Blueprint('common_api', __name__,
                        template_folder='./')

#docs = FlaskApiSpec(common_api,document_options=False)

gunicorn_error_logger = logging.getLogger('gunicorn.error')
current_app.logger.handlers.extend(gunicorn_error_logger.handlers)
current_app.logger.setLevel(logging.DEBUG)
current_app.logger.debug('NeuroLibre common API.')

@common_api.route('/api/heartbeat', methods=['GET'])
@doc(description='Sanity check for the successful registration of the API endpoints.', tags=['Heartbeat'])
def api_heartbeat():
    return f"<3<3<3<3 Alive <3<3<3<3"

@common_api.route('/api/books', methods=['GET'])
@doc(description='Get the list of all the built books that exist on the server.', tags=['Book'])
def api_get_books():
    books = load_all()
    if books:
        return Response(jsonify(books), status=200, mimetype='application/json')
    else:
        return Response(jsonify("There are no books on this server yet."), status=404, mimetype='application/json')

class BookSchema(Schema):
    user_name = fields.String(required=False,description="Full URL of the repository submitted by the author.")
    commit_hash = fields.String(required=False,description="Commit hash.")
    repo_name = fields.String(required=False,description="Commit hash.")

@common_api.route('/api/book', methods=['GET'])
#@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@use_kwargs(BookSchema())
@doc(description='Request an individual book url via commit, repo name or user name.', tags=['Book'])
def api_get_book(user_name=None,commit_hash=None,repo_name=None):
    
    if  not any([user_name, commit_hash, repo_name]):
        current_app.logger.debug('No payload, parsing request arguments.')
    
    if "user_name" in request.args:
        user_name = str(request.args['user_name'])
    elif "commit_hash" in request.args:
        commit_hash = str(request.args['commit_hash'])
    elif "repo_name" in request.args:
        repo_name = str(request.args['repo_name'])
    else:
        abort(400)

    # Create an empty list for our results
    results = book_get_by_params(user_name, commit_hash, repo_name)
    if not results:
        abort(404)
    
    # Use the jsonify function from Flask to convert our list of
    # Python dictionaries to the JSON format.
    return jsonify(results)

# Register endpoint to the documentation
