from flask import Response
from flask import Blueprint
from flask import jsonify
from common import load_all

common_api = Blueprint('common_api', __name__,
                        template_folder='./')

@common_api.route('/api/books', methods=['GET'])
def list_all_books():
    books = load_all()
    if books:
        return Response(jsonify(books), status=200, mimetype='application/json')
    else:
        return Response(jsonify("oops"), status=404, mimetype='application/json')