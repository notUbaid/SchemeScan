from functools import wraps
from flask import request, jsonify, g
from utils.firebase_client import verify_firebase_token, get_document
from config import Config

def require_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Missing or invalid authorization header'}), 401
        token = auth_header.split(' ')[1]
        try:
            decoded = verify_firebase_token(token)
            g.user_uid = decoded['uid']
            g.user_email = decoded.get('email', '')
        except Exception as e:
            return jsonify({'error': 'Invalid token', 'detail': str(e)}), 401
        return f(*args, **kwargs)
    return decorated

def require_admin(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization', '')
        if not auth_header.startswith('Bearer '):
            return jsonify({'error': 'Unauthorized'}), 401
        token = auth_header.split(' ')[1]
        try:
            decoded = verify_firebase_token(token)
            email = decoded.get('email', '')
            uid = decoded['uid']
            
            admin_doc = get_document('admin_users', uid)
            if not admin_doc and email not in Config.ADMIN_EMAILS:
                return jsonify({'error': 'Admin access required'}), 403
            
            g.user_uid = uid
            g.user_email = email
            g.is_superadmin = admin_doc.get('access_level') == 'superadmin' \
                              if admin_doc else False
        except Exception as e:
            return jsonify({'error': str(e)}), 401
        return f(*args, **kwargs)
    return decorated
