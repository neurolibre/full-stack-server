from flask import Response, Blueprint, abort, jsonify
from common import *
from flask_apispec import FlaskApiSpec, marshal_with, doc, use_kwargs
from marshmallow import Schema, fields

common_api = Blueprint('common_api', __name__,
                        template_folder='./')

#docs = FlaskApiSpec(common_api,document_options=False)

@common_api.route('/api/heartbeat', methods=['GET'])
def api_test():
    return f"<3<3<3<3 Alive "

@common_api.route('/api/books', methods=['GET'])
def list_all_books():
    books = load_all()
    if books:
        return Response(jsonify(books), status=200, mimetype='application/json')
    else:
        return Response(jsonify("oops"), status=404, mimetype='application/json')

class BookSchema(Schema):
    user_name = fields.String(required=False,description="Full URL of the repository submitted by the author.")
    commit_hash = fields.String(required=False,description="Commit hash.")
    repo_name = fields.String(required=False,description="Commit hash.")

@common_api.route('/api/book', methods=['GET'])
#@htpasswd.required
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@use_kwargs(BookSchema())
@doc(description='Request a an individual book url via commit, repo name or user name.', tags=['Book'])
def api_books_get(user_name=None,commit_hash=None,repo_name=None):
    
    if  not any([user_name, commit_hash, repo_name]):
        abort(400)

    # Create an empty list for our results
    results = book_get_by_params(user_name, commit_hash, repo_name)
    if not results:
        abort(404)
    
    # Use the jsonify function from Flask to convert our list of
    # Python dictionaries to the JSON format.
    return jsonify(results)

# Register endpoint to the documentation
