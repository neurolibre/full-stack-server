from flask import Response, Blueprint, abort, jsonify, request, current_app, make_response
from common import *
from flask_apispec import marshal_with, doc, use_kwargs
from urllib.parse import urlparse
from schema import UnlockSchema, StatusSchema, BookSchema, TaskSchema
from flask_htpasswd import HtPasswdAuth
from neurolibre_celery_tasks import celery_app, sleep_task

common_api = Blueprint('common_api', __name__,
                        template_folder='./')

# To emit log messages from this blueprint to the application context, 
# we are going to use current_app. Only valid if used within route definitions.

# Document registration of the following endpoints must be performed by the app that 
# imports this blueprint.

@common_api.before_app_first_request
def setup_htpasswd_auth():
    # The current_app needs to be called within
    # the application context.
    htpasswd_auth  = HtPasswdAuth(current_app)
    common_api.htpasswd_auth = htpasswd_auth


# Decorate to require HTTP Basic Authentication for
# common-api endpoints
def require_http_auth(view_func):
    def wrapper(*args, **kwargs):
        return common_api.htpasswd_auth.required(view_func)(*args, **kwargs)
    return wrapper

@common_api.route('/api/heartbeat', methods=['GET'])
@marshal_with(None,code=200,description="Success.")
@use_kwargs(StatusSchema())
@doc(description='Sanity check for the successful registration of the API endpoints.', tags=['Tests'])
def api_heartbeat(id=None):
    url = request.url
    parsed_url = urlparse(url)
    if "id" in request.args:
        id = request.args.get("id")
        response =  make_response(f'&#128994; NeuroLibre server is active (running). <br> &#127808; Ready to accept requests from Issue #{id} <br> &#128279; URL: {parsed_url.scheme}://{parsed_url.netloc}',200)
    else:
        response =  make_response(f'&#128994; NeuroLibre server is active (running) at {parsed_url.scheme}://{parsed_url.netloc}',200)
    return response

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

@common_api.route('/api/book', methods=['GET'])
@marshal_with(None,code=400,description="Bad request.")
@marshal_with(None,code=404,description="Not found.")
@marshal_with(None,code=200,description="Returns a JSON (possibly array) that contains information about the reproducible preprint (e.g. book_url).")
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

@common_api.route('/api/book/unlock', methods=['POST'])
@require_http_auth
@marshal_with(None,code=422,description="Cannot validate the payload, missing or invalid entries.")
@marshal_with(None,code=200,description="Build lock has been removed.")
@marshal_with(None,code=404,description="Lock does not exist.")
@doc(description='Remove the build lock that prevents recurrent or simultaneous build requests (rate limit 30 mins).', tags=['Book'])
@use_kwargs(UnlockSchema())
def api_unlock_build(user, repo_url):
    lock_filename = get_lock_filename(repo_url)
    if os.path.isfile(lock_filename):
        os.remove(lock_filename)
        response = make_response(f"Removed the lock for {repo_url}",200)
    else:
        response =  make_response(f"No build lock found for {repo_url}",404)
    
    response.mimetype = "text/plain"
    return response

@common_api.route('/public/data', methods=['GET'])
@doc(description='List the name of folders under /DATA.', tags=['Data'])
def api_preview_list(user):
    """
    This endpoint is to list the contents of the /DATA folder.
    """
    files = os.listdir('/DATA')
    return make_response(jsonify(files),200)