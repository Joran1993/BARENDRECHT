"""
Firestore sync — schrijft naar het gedeelde Firebase-project zodat
de FlutterFlow-app dezelfde data ziet.

Alle functies zijn fire-and-forget: fouten worden gelogd maar blokkeren
nooit het hoofdproces.
"""
import os
import threading
from datetime import datetime, timezone

_db = None
_init_lock = threading.Lock()


def init_firebase():
    """Initialiseer Firebase Admin SDK bij startup. Gooit een exception als het mislukt."""
    import json as _json
    import firebase_admin
    from firebase_admin import credentials

    try:
        firebase_admin.get_app()
        print("[firebase] App al geïnitialiseerd")
        return
    except ValueError:
        pass

    gc = os.getenv("GOOGLE_CREDENTIALS")
    if gc:
        try:
            cred = credentials.Certificate(_json.loads(gc))
        except Exception as e:
            print(f"[firebase] Fout bij parsen GOOGLE_CREDENTIALS: {e}")
            raise
    else:
        cred_path = os.path.join(os.path.dirname(__file__), "serviceaccount.json")
        cred = credentials.Certificate(cred_path)

    firebase_admin.initialize_app(cred, {"storageBucket": "database-e5575.appspot.com"})
    print("[firebase] App geïnitialiseerd")


def _get_db():
    global _db
    if _db is not None:
        return _db
    with _init_lock:
        if _db is not None:
            return _db
        try:
            import firebase_admin
            try:
                firebase_admin.get_app()
            except ValueError:
                init_firebase()
            from firebase_admin import firestore
            _db = firestore.client()
            print("[firestore] Verbonden met Firebase project")
        except Exception as e:
            print(f"[firestore] Init mislukt: {e}")
            _db = None
    return _db


def _run(fn):
    """Voer fn uit in een achtergrond-thread — blokkeert de request nooit."""
    t = threading.Thread(target=fn, daemon=True)
    t.start()


def _now():
    return datetime.now(timezone.utc)


# ── Items / Marketplaceoffers ─────────────────────────────────────────────────

def sync_item(item: dict):
    """Schrijf of overschrijf een item in Marketplaceoffers."""
    def _write():
        try:
            db = _get_db()
            if not db:
                return
            doc_id = str(item["id"])
            db.collection("Marketplaceoffers").document(doc_id).set({
                "cirqo_id":    item["id"],
                "foto_url":    item.get("photo_url") or "",
                "label":       item.get("ai_label") or "",
                "detail":      item.get("ai_detail") or "",
                "gewicht_kg":  item.get("gewicht_kg"),
                "categorie":   item.get("category") or "",
                "opmerking":   item.get("manual_note") or "",
                "gemeente":    item.get("gemeente") or "",
                "status":      item.get("status") or "beschikbaar",
                "geaccepteerd": bool(item.get("geaccepteerd")),
                "aangemaakt":  item.get("created_at") or _now(),
                "bron":        "cirqo_web",
            }, merge=True)
        except Exception as e:
            print(f"[firestore] sync_item fout: {e}")
    _run(_write)


def delete_item(item_id: int):
    """Verwijder een item uit Marketplaceoffers."""
    def _del():
        try:
            db = _get_db()
            if not db:
                return
            db.collection("Marketplaceoffers").document(str(item_id)).delete()
        except Exception as e:
            print(f"[firestore] delete_item fout: {e}")
    _run(_del)


# ── Aanbiedingen / ophaalverzoeken ────────────────────────────────────────────

def sync_aanbieding(aanbieding: dict):
    """Schrijf een aanbieding naar ophaalverzoeken."""
    def _write():
        try:
            db = _get_db()
            if not db:
                return
            doc_id = str(aanbieding["id"])
            db.collection("ophaalverzoeken").document(doc_id).set({
                "cirqo_id":    aanbieding["id"],
                "item_id":     str(aanbieding.get("item_id") or ""),
                "bedrijf_id":  str(aanbieding.get("bedrijf_id") or ""),
                "bedrijf_naam": aanbieding.get("bedrijf_naam") or "",
                "status":      aanbieding.get("status") or "open",
                "aangemaakt":  aanbieding.get("created_at") or _now(),
                "bijgewerkt":  _now(),
                "bron":        "cirqo_web",
            }, merge=True)
        except Exception as e:
            print(f"[firestore] sync_aanbieding fout: {e}")
    _run(_write)


def update_aanbieding_status(aanbieding_id: int, status: str):
    """Update alleen de status van een aanbieding."""
    def _update():
        try:
            db = _get_db()
            if not db:
                return
            db.collection("ophaalverzoeken").document(str(aanbieding_id)).set({
                "status":     status,
                "bijgewerkt": _now(),
            }, merge=True)
        except Exception as e:
            print(f"[firestore] update_aanbieding_status fout: {e}")
    _run(_update)
