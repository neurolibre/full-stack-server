from flask import Response, Blueprint, abort, jsonify, request, current_app, make_response
from common import *
from flask_apispec import marshal_with, doc, use_kwargs
from marshmallow import Schema, fields
from urllib.parse import urlparse

common_api = Blueprint('common_api', __name__,
                        template_folder='./')

# To emit log messages from this blueprint to the application context, 
# we are going to use current_app. Only valid if used within route definitions.

# Document registration of the following endpoints must be performed by the app that 
# imports this blueprint.

class StatusSchema(Schema):
    id = fields.Integer(required=False,description="Review issue ID if request is forwarded through robo.neurolibre.org")

@common_api.route('/api/heartbeat', methods=['GET'])
@marshal_with(None,code=200,description="Success.")
@use_kwargs(StatusSchema())
@doc(description='Sanity check for the successful registration of the API endpoints.', tags=['Heartbeat'])
def api_heartbeat(id=None):
    url = request.url
    parsed_url = urlparse(url)
    if "id" in request.args:
        id = request.args.get("id")
        return make_response(jsonify(f'&#128994; NeuroLibre server is active (running). <br> &#127808; Ready to accept requests from Issue #{id} <br> &#128279; URL: {parsed_url.scheme}://{parsed_url.netloc}'),200)
    else:
        return make_response(jsonify(f'&#128994; NeuroLibre server is active (running) at {parsed_url.scheme}://{parsed_url.netloc}'),200)

@common_api.route('/api/books', methods=['GET'])
@marshal_with(None,code=404,description="Not found.")
@marshal_with(None,code=200,description="Success.")
@doc(description='Get the list of all the built books that exist on the server.', tags=['Book'])
def api_get_books():
    books = load_all()
    if books:
        return make_response(jsonify(books), 200)
    else:
        return make_response(jsonify("There are no books on this server yet."), 404)

class BookSchema(Schema):
    user_name = fields.String(required=False,description="Full URL of the repository submitted by the author.")
    commit_hash = fields.String(required=False,description="Commit hash.")
    repo_name = fields.String(required=False,description="Commit hash.")

@common_api.route('/api/book', methods=['GET'])
@marshal_with(None,code=400,description="Bad request.")
@marshal_with(None,code=404,description="Not found.")
@marshal_with(None,code=200,description="Success.")
@use_kwargs(BookSchema())
@doc(description='Request an individual book url via commit, repo name or user name. Accepts arguments passed in the request URL.', tags=['Book'])
def api_get_book(user_name=None,commit_hash=None,repo_name=None):
    
    if  not any([user_name, commit_hash, repo_name]):
        # Example debug message from within the blueprint route
        current_app.logger.debug('No payload, parsing request arguments.')

    if "user_name" in request.args:
        user_name = request.args.get("user_name")
    elif "commit_hash" in request.args:
        commit_hash = request.args.get("commit_hash")
    elif "repo_name" in request.args:
        repo_name = request.args.get("repo_name")
    else:
        response = make_response(jsonify('Bad request, no arguments passed to locate a book.'),400)

    # Create an empty list for our results
    results = book_get_by_params(user_name, commit_hash, repo_name)
    
    if not results:
        response = make_response(jsonify('Requested book does not exist.'),404)
    else:
        response = make_response(jsonify(results),200)
    
    # Use the jsonify function from Flask to convert our list of
    # Python dictionaries to the JSON format.
    return response