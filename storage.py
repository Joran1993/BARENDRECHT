"""
Supabase Storage — foto upload
"""
import os
import uuid
import httpx

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
BUCKET = "photos"

# Herbruikbare HTTP client (geen nieuwe verbinding per upload)
_client: httpx.Client | None = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(timeout=30)
    return _client


def upload_photo(content: bytes) -> str:
    """Upload JPEG bytes naar Supabase Storage. Geeft publieke URL terug."""
    key = f"{uuid.uuid4()}.jpg"
    r = _get_client().put(
        f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{key}",
        content=content,
        headers={
            "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
            "Content-Type": "image/jpeg",
        },
    )
    r.raise_for_status()
    return f"{SUPABASE_URL}/storage/v1/object/public/{BUCKET}/{key}"
