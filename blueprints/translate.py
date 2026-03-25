from flask import Blueprint, jsonify

translate_bp = Blueprint('translate', __name__)

@translate_bp.route('/test', methods=['GET'])
def test_translate():
    return jsonify({"status": "translate blueprint registered"}), 200
