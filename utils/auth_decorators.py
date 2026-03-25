"""Bearer JWT guard for Flask routes."""
from __future__ import annotations

from functools import wraps

import jwt
from flask import g, jsonify, request

from utils.auth_tokens import decode_access_token


def require_auth(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"error": "Authentication required"}), 401
        raw = auth_header[7:].strip()
        if not raw:
            return jsonify({"error": "Authentication required"}), 401
        try:
            data = decode_access_token(raw)
        except jwt.ExpiredSignatureError:
            return jsonify({"error": "Token expired"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"error": "Invalid token"}), 401
        except RuntimeError as e:
            return jsonify({"error": str(e)}), 503
        g.current_user_id = data["sub"]
        g.current_user_email = data.get("email")
        return fn(*args, **kwargs)

    return wrapped
