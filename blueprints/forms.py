from flask import Blueprint, jsonify

forms_bp = Blueprint('forms', __name__)

@forms_bp.route('/test', methods=['GET'])
def test_forms():
    return jsonify({"status": "forms blueprint registered"}), 200
