from flask import Blueprint, g, jsonify, request

import jwt
from utils.auth_decorators import require_auth
from utils.auth_tokens import create_access_token, decode_access_token
from utils.users_store import create_user, get_user_public, verify_credentials

auth_bp = Blueprint("auth", __name__)


def _json_error(message: str, code: int):
    return jsonify({"error": message}), code


def _auth_config_error(err: RuntimeError):
    return jsonify({"error": str(err)}), 503


@auth_bp.route("/test", methods=["GET"])
def test_auth():
    return jsonify({"status": "auth blueprint registered"}), 200


@auth_bp.route("/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email or not password:
        return _json_error("Email and password are required", 400)
    try:
        user = create_user(email, password)
        token = create_access_token(user["id"], user["email"])
    except ValueError as e:
        code = str(e)
        if code == "EMAIL_TAKEN":
            return _json_error("An account with this email already exists", 409)
        if code == "INVALID_EMAIL":
            return _json_error("Invalid email address", 400)
        if code == "PASSWORD_TOO_SHORT":
            return _json_error("Password must be at least 10 characters", 400)
        if code == "PASSWORD_TOO_LONG":
            return _json_error("Password is too long", 400)
        if code in ("PASSWORD_TOO_WEAK", "PASSWORD_REQUIRED"):
            return _json_error("Password must include at least one letter and one number", 400)
        return _json_error("Registration failed", 400)
    except RuntimeError as e:
        return _auth_config_error(e)
    return (
        jsonify(
            {
                "access_token": token,
                "token_type": "Bearer",
                "user": {"id": user["id"], "email": user["email"]},
            }
        ),
        201,
    )


@auth_bp.route("/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    if not email or not password:
        return _json_error("Email and password are required", 400)
    try:
        user = verify_credentials(email, password)
        if not user:
            return _json_error("Invalid email or password", 401)
        token = create_access_token(user["id"], user["email"])
    except RuntimeError as e:
        return _auth_config_error(e)
    return jsonify(
        {
            "access_token": token,
            "token_type": "Bearer",
            "user": {"id": user["id"], "email": user["email"]},
        }
    )


@auth_bp.route("/me", methods=["GET"])
@require_auth
def me():
    user = get_user_public(g.current_user_id)
    if not user:
        return _json_error("User not found", 404)
    return jsonify(user)


@auth_bp.route("/verify", methods=["POST"])
def verify_token():
    """Optional: check token without loading full profile."""
    data = request.get_json(silent=True) or {}
    token = (data.get("access_token") or "").strip()
    if not token:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
    if not token:
        return _json_error("No token provided", 400)
    try:
        payload = decode_access_token(token)
    except jwt.ExpiredSignatureError:
        return _json_error("Token expired", 401)
    except jwt.InvalidTokenError:
        return _json_error("Invalid token", 401)
    except RuntimeError as e:
        return _auth_config_error(e)
    return jsonify({"valid": True, "sub": payload.get("sub"), "email": payload.get("email")})
