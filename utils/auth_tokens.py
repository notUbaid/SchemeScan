"""JWT access tokens signed with FLASK_SECRET_KEY."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt

from config import Config


def _require_secret() -> str:
    key = Config.SECRET_KEY
    if not key:
        raise RuntimeError(
            "FLASK_SECRET_KEY is not set. Add a long random string to your .env file "
            "(e.g. python -c \"import secrets; print(secrets.token_hex(32))\")."
        )
    return key


def create_access_token(user_id: str, email: str) -> str:
    secret = _require_secret()
    now = datetime.now(timezone.utc)
    exp_days = getattr(Config, "JWT_EXPIRATION_DAYS", 7)
    payload = {
        "sub": user_id,
        "email": email,
        "iat": now,
        "exp": now + timedelta(days=exp_days),
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    secret = _require_secret()
    return jwt.decode(token, secret, algorithms=["HS256"])
