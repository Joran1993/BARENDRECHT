import os
import jwt
import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from typing import Optional

SECRET_KEY = os.environ.get("SECRET_KEY", "verander-dit-in-productie-naar-iets-geheims")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 365


def _hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    h = hmac.new(salt.encode(), password.encode(), hashlib.sha256).hexdigest()
    return f"{salt}${h}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, h = hashed.split("$")
        expected = hmac.new(salt.encode(), password.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, h)
    except Exception:
        return False


def hash_password(password: str) -> str:
    return _hash_password(password)


def create_token(user_id: int, username: str, role: str = "user",
                 gemeente: str = "", bedrijf_id: Optional[int] = None) -> str:
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "gemeente": gemeente,
        "bedrijf_id": bedrijf_id,
        "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except Exception:
        return None
