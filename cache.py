"""
Redis cache — voorkomt onnodige DB calls
"""
import os
import json
import redis

REDIS_URL = os.environ.get("REDIS_URL", "")
_client = None


def _get() -> redis.Redis | None:
    global _client
    if not REDIS_URL:
        return None
    if _client is None:
        _client = redis.from_url(REDIS_URL, decode_responses=True)
    return _client


def get(key: str):
    r = _get()
    if not r:
        return None
    try:
        val = r.get(key)
        return json.loads(val) if val else None
    except Exception:
        return None


def set(key: str, value, ttl: int = 30):
    r = _get()
    if not r:
        return
    try:
        r.setex(key, ttl, json.dumps(value, default=str))
    except Exception:
        pass


def delete(*keys: str):
    r = _get()
    if not r:
        return
    try:
        r.delete(*keys)
    except Exception:
        pass
