"""Firestore persistence for app accounts. Passwords are stored only as Werkzeug scrypt hashes."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from email_validator import EmailNotValidError, validate_email
from werkzeug.security import check_password_hash, generate_password_hash

from utils.firebase_client import db, update_document

USERS_COLLECTION = "scheme_users"

MAX_PASSWORD_LEN = 256


def validate_and_normalize_email(email: str) -> str:
    if not email or not isinstance(email, str):
        raise ValueError("INVALID_EMAIL")
    email = email.strip()
    if len(email) > 254:
        raise ValueError("INVALID_EMAIL")
    try:
        info = validate_email(email, check_deliverability=False)
    except EmailNotValidError:
        raise ValueError("INVALID_EMAIL") from None
    return info.normalized.lower()


def validate_password(password: str) -> None:
    if not password or not isinstance(password, str):
        raise ValueError("PASSWORD_REQUIRED")
    if len(password) < 10:
        raise ValueError("PASSWORD_TOO_SHORT")
    if len(password) > MAX_PASSWORD_LEN:
        raise ValueError("PASSWORD_TOO_LONG")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        raise ValueError("PASSWORD_TOO_WEAK")


def find_user_by_email(email_lower: str) -> dict | None:
    q = db.collection(USERS_COLLECTION).where("email_lower", "==", email_lower).limit(1)
    docs = list(q.stream())
    if not docs:
        return None
    d = docs[0]
    return {"id": d.id, **d.to_dict()}


def get_user_public(uid: str) -> dict | None:
    doc = db.collection(USERS_COLLECTION).document(uid).get()
    if not doc.exists:
        return None
    data = doc.to_dict()
    created = data.get("created_at")
    if hasattr(created, "isoformat"):
        created = created.isoformat()
    return {
        "id": doc.id,
        "email": data.get("email"),
        "created_at": created,
    }


def create_user(email: str, password: str) -> dict:
    email_lower = validate_and_normalize_email(email)
    validate_password(password)
    if find_user_by_email(email_lower):
        raise ValueError("EMAIL_TAKEN")

    uid = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    doc = {
        "email": email.strip(),
        "email_lower": email_lower,
        "password_hash": generate_password_hash(password),
        "created_at": now,
        "updated_at": now,
    }
    db.collection(USERS_COLLECTION).document(uid).set(doc)
    return {"id": uid, "email": doc["email"]}


def verify_credentials(email: str, password: str) -> dict | None:
    try:
        email_lower = validate_and_normalize_email(email)
    except ValueError:
        return None
    user = find_user_by_email(email_lower)
    if not user:
        return None
    pwd_hash = user.get("password_hash")
    if not pwd_hash or not check_password_hash(pwd_hash, password):
        return None
    update_document(USERS_COLLECTION, user["id"], {"last_login_at": datetime.now(timezone.utc)})
    return {"id": user["id"], "email": user.get("email")}
