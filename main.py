"""
De Bouwkringloop — cloud backend
Rollen: superadmin (platform), admin (gemeente), user (scanner)
"""
import asyncio
import base64
import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

import database as db
import ai as ai_module
import auth as auth_module
import storage as storage_module
import cache as cache_module
import push as push_module
import firestore as fs

security = HTTPBearer(auto_error=False)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials:
        raise HTTPException(status_code=401, detail="Niet ingelogd")
    payload = auth_module.decode_token(credentials.credentials)
    if not payload:
        raise HTTPException(status_code=401, detail="Sessie verlopen")
    # Role en gemeente direct uit token — geen DB-query nodig
    return {
        "id": int(payload["sub"]),
        "username": payload.get("username", ""),
        "role": payload.get("role", "user"),
        "gemeente": payload.get("gemeente", "") or "",
        "bedrijf_id": payload.get("bedrijf_id"),
    }


def require_superadmin(user=Depends(get_current_user)):
    if user["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="Alleen platform-beheerders hebben toegang")
    return user


def require_admin(user=Depends(get_current_user)):
    """superadmin én gemeente-admin mogen door."""
    if user["role"] not in ("superadmin", "admin"):
        raise HTTPException(status_code=403, detail="Geen beheerdersrechten")
    return user


BAR_GEMEENTEN = ["Barendrecht", "Ridderkerk", "Albrandswaard"]
RWM_GEMEENTEN = [
    "Roermond", "Leudal", "Maasgouw", "Echt-Susteren",
    "Roerdalen", "Weert", "Beesel", "Bergen",
]
RWM_MILIEUSTRATEN = {
    "Roermond":      ["Milieustraat Roermond"],
    "Leudal":        ["Milieustraat Heythuysen"],
    "Maasgouw":      ["Milieustraat Maasbracht"],
    "Echt-Susteren": ["Milieustraat Echt"],
    "Roerdalen":     ["Milieustraat St. Odiliënberg"],
    "Weert":         ["Milieustraat Weert"],
    "Beesel":        ["Milieustraat Reuver"],
    "Bergen":        ["Milieustraat Well"],
}
HVC_GEMEENTEN = [
    "Alblasserdam", "Alkmaar", "Almere", "Bergen", "Beverwijk", "Castricum",
    "Delft", "Den Helder", "Dordrecht", "Drechterland", "Dronten", "Dijk en Waard",
    "Edam-Volendam", "Enkhuizen", "Gorinchem", "Haarlem", "Hardinxveld-Giessendam",
    "Heemskerk", "Heiloo", "Hendrik-Ido-Ambacht", "Hollands Kroon", "Hoorn",
    "Koggenland", "Leidschendam-Voorburg", "Lelystad", "Maassluis", "Medemblik",
    "Midden-Delfland", "Molenlanden", "Nieuwegein", "Noordoostpolder", "Opmeer",
    "Papendrecht", "Pijnacker-Nootdorp", "Purmerend", "Rijswijk", "Schagen",
    "Sliedrecht", "Smallingerland", "Stede Broec", "Texel", "Uitgeest", "Urk",
    "Utrecht", "Velsen", "Vijfheerenlanden", "Wassenaar", "Waterland",
    "Westland", "Wormerland", "Zaanstad", "Zandvoort", "Zeewolde", "Zwijndrecht",
]
WAARDLANDEN_GEMEENTEN = [
    "Alblasserdam", "Gorinchem", "Hardinxveld-Giessendam",
    "Hendrik-Ido-Ambacht", "Molenlanden", "Papendrecht",
    "Sliedrecht", "Vijfheerenlanden", "Zwijndrecht",
]
BRAND_GEMEENTEN = {"bar": BAR_GEMEENTEN, "hvc": HVC_GEMEENTEN, "rwm": RWM_GEMEENTEN}
ORGANISATIE_GEMEENTEN = {
    "waardlanden": WAARDLANDEN_GEMEENTEN,
    "bar":         BAR_GEMEENTEN,
    "rwm":         RWM_GEMEENTEN,
    "hvc":         HVC_GEMEENTEN,
}


def _gemeente_filter(user: dict, gemeente: Optional[str] = None) -> Optional[str]:
    """
    superadmin:  mag alles zien (None = geen filter)
    admin:       valt terug op eigen gemeente als geen param opgegeven
    user:        altijd eigen gemeente
    """
    if user["role"] == "superadmin":
        return gemeente or None
    if user["role"] == "admin":
        return gemeente or user.get("gemeente") or None
    return user.get("gemeente") or None


def _gemeenten_expand(gemeente: Optional[str]) -> Optional[list]:
    if gemeente and gemeente in ORGANISATIE_GEMEENTEN:
        return ORGANISATIE_GEMEENTEN[gemeente]
    gemeenten = BRAND_GEMEENTEN.get(DEFAULT_BRAND)
    if not gemeenten:
        return None
    if gemeente is None or gemeente in gemeenten:
        return gemeenten
    return None


# ── Startup ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    _migrate_admin_to_superadmin()
    try:
        fs.init_firebase()
    except Exception as e:
        print(f"[main] Firebase init mislukt (niet fataal): {e}")
    print(f"[main] gestart — DEFAULT_BRAND={DEFAULT_BRAND!r}, BRAND_ENV={os.getenv('BRAND')!r}")
    yield


def _migrate_admin_to_superadmin():
    """Zet oude 'admin' accounts die geen gemeente hebben om naar superadmin."""
    try:
        with db.get_cursor() as cur:
            cur.execute("""
                UPDATE users SET role = 'superadmin'
                WHERE role = 'admin' AND (gemeente IS NULL OR gemeente = '')
            """)
    except Exception as e:
        print(f"[migrate] {e}")


app = FastAPI(title="De Bouwkringloop", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/health")
async def health():
    """Health check — wordt gepingd door UptimeRobot om de container wakker te houden."""
    try:
        with db.get_cursor() as cur:
            cur.execute("SELECT 1")
        ai_ok = bool(os.environ.get("ANTHROPIC_API_KEY"))
        return {"status": "ok", "db": "ok", "ai": "ok" if ai_ok else "geen API key"}
    except Exception as e:
        from fastapi import Response
        return Response(content=f"db fout: {e}", status_code=503)


# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/api/auth/bedrijf-token/{token}")
async def login_met_token(token: str):
    """Automatisch inloggen voor bedrijven via de meld_token uit de link."""
    bedrijf = db.get_bedrijf_by_token(token)
    if not bedrijf:
        raise HTTPException(status_code=404, detail="Ongeldige link")
    # Zoek het bijbehorende bedrijf-account
    with db.get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE bedrijf_id = %s AND role = 'bedrijf' LIMIT 1", (bedrijf["id"],))
        row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Geen account gevonden voor dit bedrijf")
    user = dict(row)
    token_jwt = auth_module.create_token(
        user["id"], user["username"], user["role"],
        user.get("gemeente") or "", user.get("bedrijf_id")
    )
    return {
        "token": token_jwt,
        "username": bedrijf["naam"],
        "role": "bedrijf",
        "bedrijf_id": bedrijf["id"],
    }


@app.post("/api/auth/login")
async def login(username: str = Form(...), password: str = Form(...)):
    user = db.get_user_by_username(username)
    if not user or not auth_module.verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Onjuiste gebruikersnaam of wachtwoord")
    token = auth_module.create_token(
        user["id"], user["username"], user["role"],
        user.get("gemeente") or "", user.get("bedrijf_id")
    )
    return {
        "token": token,
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "gemeente": user.get("gemeente") or "",
        "bedrijf_id": user.get("bedrijf_id"),
        "organisatie": user.get("organisatie") or "",
        "auth_type": "local",
    }


@app.post("/api/auth/firebase-login")
async def firebase_login(request: Request):
    """Verifieer een Firebase ID-token en geef een CIRQO JWT terug."""
    data = await request.json()
    id_token = data.get("id_token")
    if not id_token:
        raise HTTPException(status_code=400, detail="id_token vereist")
    try:
        import firebase_admin.auth as fb_auth
        fs._get_db()  # zorg dat Firebase app geïnitialiseerd is
        decoded = fb_auth.verify_id_token(id_token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Ongeldig Firebase token: {e}")

    firebase_uid = decoded["uid"]
    email = decoded.get("email", "")

    # Haal naam op uit Firestore users3
    naam = email
    gemeente = ""
    role = "user"
    try:
        fsdb = fs._get_db()
        if fsdb:
            docs = fsdb.collection("users3").where("email", "==", email).limit(1).get()
            if not docs:
                # Probeer op UID als document-ID
                doc = fsdb.collection("users3").document(firebase_uid).get()
                docs = [doc] if doc.exists else []
            for doc in docs:
                d = doc.to_dict() or {}
                naam = d.get("Naamofbedrijf") or d.get("naam") or email
                gemeente = d.get("Gemeente") or d.get("gemeente") or ""
                if d.get("Administrator"):
                    role = "admin"
    except Exception as e:
        print(f"[firebase-login] Firestore lookup fout: {e}")

    user = db.upsert_firebase_user(firebase_uid, email, naam, gemeente, role)
    token = auth_module.create_token(
        user["id"], user["username"], user["role"],
        user.get("gemeente") or "", user.get("bedrijf_id")
    )
    return {
        "token": token,
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "gemeente": user.get("gemeente") or "",
        "bedrijf_id": user.get("bedrijf_id"),
        "organisatie": user.get("organisatie") or naam,
        "auth_type": "firebase",
    }


@app.post("/api/admin/import-firestore")
async def import_firestore(user=Depends(require_superadmin)):
    """Eenmalige import van Marketplaceoffers en ophaalverzoeken uit Firestore naar PostgreSQL."""
    fsdb = fs._get_db()
    if not fsdb:
        raise HTTPException(status_code=503, detail="Firestore niet beschikbaar")

    items_added = 0
    items_skipped = 0
    aanbiedingen_added = 0

    # Items importeren
    try:
        docs = fsdb.collection("Marketplaceoffers").stream()
        for doc in docs:
            d = doc.to_dict() or {}
            # Skip als het al een cirqo_web item is (die hebben we al in PostgreSQL)
            if d.get("bron") == "cirqo_web":
                items_skipped += 1
                continue
            try:
                photo_url = d.get("foto_url") or d.get("photoUrl") or d.get("photo_url") or ""
                label = d.get("label") or d.get("naam") or d.get("title") or "Onbekend"
                detail = d.get("detail") or d.get("beschrijving") or d.get("description") or ""
                gewicht = d.get("gewicht_kg") or d.get("gewicht") or 0
                gemeente = d.get("gemeente") or d.get("Gemeente") or ""
                category = d.get("categorie") or d.get("category") or ""
                note = d.get("opmerking") or d.get("note") or ""

                item_id = db.insert_item(photo_url, label, detail, float(gewicht) if gewicht else 0,
                                         gemeente, True, uploaded_by=None)
                if category or note:
                    db.update_item(item_id, note or None, category or None)
                # Markeer als geïmporteerd uit Firestore
                with db.get_cursor() as cur:
                    cur.execute("UPDATE items SET firestore_doc_id=%s WHERE id=%s",
                                (doc.id, item_id)) if _has_firestore_col() else None
                items_added += 1
            except Exception as e:
                print(f"[import] Item {doc.id} overgeslagen: {e}")
                items_skipped += 1
    except Exception as e:
        print(f"[import] Fout bij items: {e}")

    return {
        "ok": True,
        "items_added": items_added,
        "items_skipped": items_skipped,
        "aanbiedingen_added": aanbiedingen_added,
    }


@app.post("/api/admin/heranalyseer-gewichten")
async def heranalyseer_gewichten(gemeente: str = Form("Almere"), user=Depends(require_superadmin)):
    """Heranalyseer gewichten van alle items in een gemeente met Sonnet."""
    import ai, asyncio, time as _time
    with db.get_cursor() as cur:
        cur.execute(
            "SELECT id, ai_label, photo_url, gewicht_kg FROM items WHERE gemeente=%s AND photo_url IS NOT NULL AND photo_url != '' ORDER BY id",
            (gemeente,)
        )
        items = [dict(r) for r in cur.fetchall()]

    updated = skipped = 0
    for item in items:
        nieuw = await asyncio.get_event_loop().run_in_executor(
            None, ai.heranalyseer_gewicht, item["photo_url"], item["ai_label"]
        )
        if nieuw is not None:
            with db.get_cursor() as cur:
                cur.execute("UPDATE items SET gewicht_kg=%s WHERE id=%s", (nieuw, item["id"]))
            print(f"[gewicht] {item['ai_label']}: {item['gewicht_kg']} → {nieuw} kg")
            updated += 1
        else:
            skipped += 1
        await asyncio.sleep(0.3)

    return {"ok": True, "gemeente": gemeente, "updated": updated, "skipped": skipped}


@app.post("/api/admin/fix-aanbieding-statussen")
async def fix_aanbieding_statussen(user=Depends(require_superadmin)):
    """Herstel aanbieding-statussen vanuit Firestore ophaalverzoeken."""
    fsdb = fs._get_db()
    if not fsdb:
        raise HTTPException(status_code=503, detail="Firestore niet beschikbaar")

    STATUS_MAP = {
        "Ik wil dit ophalen": "ophalen",
        "Kom het mij brengen": "ophalen",
        "Niet nodig": "niet_nodig",
    }

    # Bouw lookup: firestore_doc_id → item_id
    with db.get_cursor() as cur:
        cur.execute("SELECT id, firestore_doc_id FROM items WHERE firestore_doc_id IS NOT NULL")
        doc_to_item = {r["firestore_doc_id"]: r["id"] for r in cur.fetchall()}

    # Bouw lookup: email → bedrijf_id en naam → bedrijf_id
    with db.get_cursor() as cur:
        cur.execute("SELECT b.id, b.naam, u.email FROM bedrijven b LEFT JOIN users u ON u.bedrijf_id = b.id")
        email_to_bedrijf = {}
        naam_to_bedrijf = {}
        for r in cur.fetchall():
            if r["email"]:
                email_to_bedrijf[r["email"]] = r["id"]
            naam_to_bedrijf[r["naam"].lower()] = r["id"]

    updated = 0
    skipped = 0

    docs = fsdb.collection("ophaalverzoeken").stream()
    for doc in docs:
        d = doc.to_dict() or {}
        verzoek_status = d.get("Verzoek") or d.get("verzoek") or ""
        status = STATUS_MAP.get(verzoek_status)
        if not status:
            skipped += 1
            continue

        verzender = d.get("verzender") or ""
        bedrijf_naam_fs = (d.get("Bedrijfsnaam") or d.get("bedrijfsnaam") or "").lower()
        bedrijf_id = email_to_bedrijf.get(verzender) or naam_to_bedrijf.get(bedrijf_naam_fs)
        if not bedrijf_id:
            skipped += 1
            continue

        aanbodref = d.get("Aanbodref") or d.get("aanbodref")
        doc_id = aanbodref.id if aanbodref and hasattr(aanbodref, "id") else None
        item_id = doc_to_item.get(doc_id) if doc_id else None

        # Fallback: match op foto URL (zonder token-parameter)
        if not item_id:
            foto = d.get("foto") or d.get("foto_url") or ""
            if foto:
                foto_pad = foto.split("?")[0]  # strip token
                with db.get_cursor() as cur:
                    cur.execute("SELECT id FROM items WHERE split_part(photo_url,'?',1) = %s LIMIT 1", (foto_pad,))
                    row = cur.fetchone()
                    item_id = row["id"] if row else None

        # Tweede fallback: match op Omschrijving + ontvanger
        if not item_id:
            omschrijving = d.get("Omschrijving") or d.get("omschrijving") or ""
            ontvanger = d.get("ontvanger") or ""
            if omschrijving and ontvanger:
                with db.get_cursor() as cur:
                    cur.execute("""
                        SELECT i.id FROM items i
                        JOIN users u ON u.id = i.uploaded_by
                        WHERE i.ai_label ILIKE %s AND u.email = %s
                        LIMIT 1
                    """, (omschrijving, ontvanger))
                    row = cur.fetchone()
                    item_id = row["id"] if row else None

        if not item_id:
            skipped += 1
            continue

        with db.get_cursor() as cur:
            cur.execute(
                "SELECT id FROM aanbiedingen WHERE item_id=%s AND bedrijf_id=%s",
                (item_id, bedrijf_id)
            )
            existing = cur.fetchone()
            if existing:
                cur.execute(
                    "UPDATE aanbiedingen SET status=%s WHERE id=%s",
                    (status, existing["id"])
                )
            else:
                aangemaakt = d.get("Datumaangemaakt") or d.get("datumaangemaakt")
                ts = aangemaakt.isoformat() if hasattr(aangemaakt, "isoformat") else None
                cur.execute(
                    "INSERT INTO aanbiedingen (item_id, bedrijf_id, status, created_at) VALUES (%s,%s,%s,%s)",
                    (item_id, bedrijf_id, status, ts)
                )
            updated += 1

    return {"ok": True, "updated": updated, "inserted": inserted, "skipped": skipped}


def _has_firestore_col():
    try:
        with db.get_cursor() as cur:
            cur.execute("SELECT firestore_doc_id FROM items LIMIT 1")
        return True
    except Exception:
        return False


@app.post("/api/auth/impersonate/{user_id}")
async def impersonate(user_id: int, user=Depends(get_current_user)):
    if user["role"] != "superadmin":
        raise HTTPException(status_code=403, detail="Geen toegang")
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
    token = auth_module.create_token(
        target["id"], target["username"], target["role"],
        target.get("gemeente") or "", target.get("bedrijf_id")
    )
    return {
        "token": token,
        "user_id": target["id"],
        "username": target["username"],
        "role": target["role"],
        "gemeente": target.get("gemeente") or "",
        "organisatie": target.get("organisatie") or "",
        "bedrijf_id": target.get("bedrijf_id"),
    }


@app.post("/api/auth/request-reset")
async def request_reset(email: str = Form(...)):
    """
    Zorgt dat er een Firebase-account bestaat voor dit e-mailadres.
    De browser stuurt daarna zelf sendPasswordResetEmail() — Firebase verstuurt de mail.
    """
    email = email.strip().lower()
    if "@" not in email:
        return {"ok": True}

    try:
        import firebase_admin.auth as _fb_auth
        fs._get_db()

        # Controleer of Firebase-account al bestaat
        firebase_exists = False
        try:
            _fb_auth.get_user_by_email(email)
            firebase_exists = True
        except Exception:
            pass

        if not firebase_exists:
            # Supabase-gebruiker zonder Firebase-account: maak Firebase-account aan
            user = db.get_user_by_email(email)
            if user:
                import secrets as _sec
                fb_user = _fb_auth.create_user(
                    email=email,
                    password=_sec.token_urlsafe(24),  # tijdelijk wachtwoord, wordt meteen gereset
                    email_verified=False,
                )
                # Koppel firebase_uid aan bestaand Supabase-account
                with db.get_cursor() as cur:
                    cur.execute(
                        "UPDATE users SET firebase_uid = %s, email = %s WHERE id = %s",
                        (fb_user.uid, email, user["id"]),
                    )
    except Exception as e:
        print(f"[request-reset] Fout: {e}")

    return {"ok": True}


@app.post("/api/auth/sync-password")
async def sync_password(request: Request):
    """Na Firebase wachtwoord-reset: sync het nieuwe wachtwoord naar Supabase."""
    data = await request.json()
    id_token = data.get("id_token")
    password = data.get("password", "")
    if not id_token or len(password) < 6:
        raise HTTPException(status_code=400, detail="Ongeldige invoer")
    try:
        import firebase_admin.auth as _fb_auth
        fs._get_db()
        decoded = _fb_auth.verify_id_token(id_token)
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Ongeldig token: {e}")
    firebase_uid = decoded["uid"]
    email = decoded.get("email", "")
    # Zoek Supabase-gebruiker op firebase_uid of email/username
    user = db.get_user_by_firebase_uid(firebase_uid)
    if not user and email:
        user = db.get_user_by_email(email)
    if not user:
        return {"ok": True}  # geen Supabase-account, niets te doen
    db.update_user_password(user["id"], password)
    return {"ok": True}


@app.get("/api/auth/me")
async def me(user=Depends(get_current_user)):
    db_user = db.get_user_by_id(user["id"])
    organisatie = (db_user or {}).get("organisatie") or ""
    # Bedrijf-gebruikers: val terug op de naam uit de bedrijven-tabel
    if not organisatie and user.get("bedrijf_id"):
        with db.get_cursor() as cur:
            cur.execute("SELECT naam FROM bedrijven WHERE id = %s", (user["bedrijf_id"],))
            row = cur.fetchone()
            if row:
                organisatie = row["naam"]
    return {
        "id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "gemeente": user.get("gemeente") or "",
        "organisatie": organisatie,
        "auto_doorsturen": bool((db_user or {}).get("auto_doorsturen")),
    }


# ── Gemeenten ─────────────────────────────────────────────────────────────────

@app.get("/api/gemeenten")
async def list_gemeenten(user=Depends(get_current_user)):
    if DEFAULT_BRAND == "bar":
        return BAR_GEMEENTEN
    if DEFAULT_BRAND == "rwm":
        return RWM_GEMEENTEN
    return db.get_gemeenten()


@app.get("/api/milieustraten")
async def list_milieustraten():
    if DEFAULT_BRAND == "rwm":
        return RWM_MILIEUSTRATEN
    return {}


@app.get("/api/gemeenten/stats")
async def gemeente_stats(user=Depends(require_superadmin)):
    return db.get_gemeente_stats()


# ── Gebruikersbeheer ──────────────────────────────────────────────────────────

@app.get("/api/users")
async def list_users(user=Depends(require_admin)):
    all_users = db.get_all_users()
    # superadmin ziet iedereen; gemeente-admin ziet alleen eigen gemeente
    if user["role"] == "superadmin":
        return all_users
    return [u for u in all_users if u["gemeente"] == user["gemeente"] and u["role"] != "superadmin"]


@app.post("/api/users")
async def create_user(
    username: str = Form(...),
    password: str = Form(...),
    gemeente: str = Form(""),
    role: str = Form("user"),
    admin=Depends(require_admin),
):
    # Gemeente-admins kunnen alleen users in hun eigen gemeente aanmaken
    if admin["role"] == "admin":
        gemeente = admin["gemeente"]
        if role not in ("user", "admin"):
            raise HTTPException(status_code=403, detail="Je kunt alleen scanners of beheerders aanmaken")
    # Superadmin kan ook admins aanmaken, maar geen superadmins via API
    if role == "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin aanmaken niet toegestaan via API")
    try:
        user_id = db.create_user(username, password, role, gemeente)
        return db.get_user_by_id(user_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Gebruikersnaam al in gebruik")


@app.delete("/api/users/{user_id}")
async def delete_user(user_id: int, admin=Depends(require_admin)):
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Je kunt jezelf niet verwijderen")
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404)
    # Gemeente-admin mag alleen eigen gemeente-users verwijderen
    if admin["role"] == "admin" and target["gemeente"] != admin["gemeente"]:
        raise HTTPException(status_code=403, detail="Geen rechten voor deze gebruiker")
    if target["role"] == "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin kan niet worden verwijderd")
    db.delete_user(user_id)
    return {"ok": True}


@app.patch("/api/users/{user_id}/role")
async def change_user_role(
    user_id: int,
    role: str = Form(...),
    bedrijf_id: Optional[int] = Form(None),
    admin=Depends(require_admin),
):
    target = db.get_user_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404)
    if target["role"] == "superadmin":
        raise HTTPException(status_code=403, detail="Superadmin kan niet worden gewijzigd")
    if role not in ("user", "admin", "bedrijf"):
        raise HTTPException(status_code=400, detail="Ongeldige rol")
    db.update_user_role(user_id, role, bedrijf_id)
    return {"ok": True}


@app.patch("/api/users/{user_id}/gemeente")
async def change_gemeente(
    user_id: int,
    gemeente: str = Form(...),
    user=Depends(get_current_user),
):
    if user["id"] != user_id:
        raise HTTPException(status_code=403, detail="Geen rechten")
    with db.get_cursor() as cur:
        cur.execute("UPDATE users SET gemeente = %s WHERE id = %s", (gemeente.strip(), user_id))
    new_token = auth_module.create_token(
        user["id"], user["username"], user["role"],
        gemeente.strip(), user.get("bedrijf_id")
    )
    return {"ok": True, "token": new_token, "gemeente": gemeente.strip()}


@app.patch("/api/users/{user_id}/password")
async def change_password(
    user_id: int,
    password: str = Form(...),
    user=Depends(get_current_user),
):
    if user["role"] == "user" and user["id"] != user_id:
        raise HTTPException(status_code=403, detail="Geen rechten")
    target = db.get_user_by_id_full(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Gebruiker niet gevonden")
    # Update Firebase Auth als de gebruiker een Firebase-account heeft
    if target.get("firebase_uid"):
        try:
            import firebase_admin.auth as _fb_auth
            fs._get_db()
            _fb_auth.update_user(target["firebase_uid"], password=password)
        except Exception as e:
            print(f"[change-password] Firebase update fout: {e}")
    db.update_user_password(user_id, password)
    return {"ok": True}


@app.patch("/api/users/{user_id}/organisatie")
async def change_organisatie(
    user_id: int,
    organisatie: str = Form(...),
    user=Depends(get_current_user),
):
    if user["id"] != user_id:
        raise HTTPException(status_code=403, detail="Geen rechten")
    with db.get_cursor() as cur:
        cur.execute("UPDATE users SET organisatie = %s WHERE id = %s", (organisatie.strip(), user_id))
    return {"ok": True, "organisatie": organisatie.strip()}


@app.patch("/api/users/{user_id}/auto-doorsturen")
async def set_auto_doorsturen(
    user_id: int,
    enabled: bool = Form(...),
    user=Depends(get_current_user),
):
    if user["id"] != user_id:
        raise HTTPException(status_code=403, detail="Geen rechten")
    db.set_auto_doorsturen(user_id, enabled)
    return {"ok": True, "auto_doorsturen": enabled}


# ── Bedrijven ─────────────────────────────────────────────────────────────────

@app.get("/api/bedrijven")
async def get_bedrijven(gemeente: Optional[str] = None, user=Depends(require_admin)):
    g = _gemeente_filter(user, gemeente)
    return db.get_bedrijven(g)


@app.get("/api/bedrijven-voor-scan")
async def bedrijven_voor_scan(
    gemeente: str,
    category: Optional[str] = None,
    item_id: Optional[int] = None,
    user=Depends(get_current_user),
):
    gemeenten = _gemeenten_expand(gemeente)
    alle = db.get_bedrijven(None if gemeenten else gemeente, gemeenten=gemeenten)
    if category:
        match_ids = {b["id"] for b in db.get_bedrijven_for_item(None if gemeenten else gemeente, category, gemeenten=gemeenten)}
        if not match_ids:
            match_ids = {b["id"] for b in db.get_bedrijven_for_item(None, category)}
    else:
        match_ids = set()
    already_offered = set()
    if item_id:
        with db.get_cursor() as cur:
            cur.execute("SELECT bedrijf_id FROM aanbiedingen WHERE item_id = %s", (item_id,))
            already_offered = {r["bedrijf_id"] for r in cur.fetchall()}
    for b in alle:
        b["categorie_match"] = b["id"] in match_ids
        b["al_aangeboden"]   = b["id"] in already_offered
    alle.sort(key=lambda b: (b["al_aangeboden"], not b["categorie_match"], b["naam"]))
    return alle


@app.post("/api/bedrijven")
async def create_bedrijf(
    naam: str = Form(...),
    gemeente: str = Form(...),
    contactpersoon: str = Form(""),
    email: str = Form(""),
    telefoon: str = Form(""),
    categorieen: str = Form(""),
    login_username: str = Form(""),
    login_password: str = Form(""),
    user=Depends(require_admin),
):
    cat_list = [c.strip() for c in categorieen.split(",") if c.strip()]
    bedrijf_id = db.create_bedrijf(naam, gemeente, contactpersoon, email, telefoon, cat_list)
    if login_username and login_password:
        db.create_user(login_username, login_password, role="bedrijf",
                       gemeente=gemeente, bedrijf_id=bedrijf_id)
    return {"id": bedrijf_id}


@app.patch("/api/bedrijven/{bedrijf_id}")
async def update_bedrijf(
    bedrijf_id: int,
    naam: str = Form(...),
    contactpersoon: str = Form(""),
    email: str = Form(""),
    telefoon: str = Form(""),
    categorieen: str = Form(""),
    user=Depends(require_admin),
):
    cat_list = [c.strip() for c in categorieen.split(",") if c.strip()]
    db.update_bedrijf(bedrijf_id, naam, contactpersoon, email, telefoon, cat_list)
    return {"ok": True}


@app.delete("/api/bedrijven/{bedrijf_id}")
async def delete_bedrijf(bedrijf_id: int, user=Depends(require_admin)):
    db.delete_bedrijf(bedrijf_id)
    return {"ok": True}


# ── Aanbiedingen ──────────────────────────────────────────────────────────────

@app.post("/api/aanbiedingen")
async def create_aanbieding(
    item_id: int = Form(...),
    bedrijf_id: int = Form(...),
    user=Depends(get_current_user),
):
    aanbieding_id = db.create_aanbieding(item_id, bedrijf_id, user_id=user["id"])
    fs.sync_aanbieding({"id": aanbieding_id, "item_id": item_id, "bedrijf_id": bedrijf_id, "status": "open"})
    gemeente = _gemeente_filter(user)
    gemeenten = _gemeenten_expand(gemeente)
    cache_key = f"items:{gemeenten or gemeente}:0:{user['id']}"
    cache_module.delete(cache_key, f"items:None:0:{user['id']}")

    # Push op de achtergrond — vertraagt het response niet
    async def _stuur_bedrijf_push():
        item = db.get_item(item_id)
        label = (item or {}).get("ai_label") or "nieuw materiaal"
        subscriptions = db.get_push_subscriptions_for_bedrijf(bedrijf_id)
        print(f"[push] Aanbieding voor bedrijf {bedrijf_id}: {len(subscriptions)} subscription(s)")
        for sub in subscriptions:
            ok = push_module.send_push(
                sub["subscription"],
                title="Nieuw aanbod — CIRQO",
                body=f"Er is {label} aangeboden.",
                url="/",
            )
            print(f"[push] Verzonden naar sub {sub['id']}: {'OK' if ok else 'MISLUKT'}")
            if not ok:
                db.delete_push_subscription(sub["subscription"])

    asyncio.create_task(_stuur_bedrijf_push())
    return {"id": aanbieding_id}


@app.post("/api/aanbiedingen/bulk")
async def create_aanbiedingen_bulk(
    request: Request,
    user=Depends(get_current_user),
):
    data = await request.json()
    item_id = data.get("item_id")
    bedrijf_ids = data.get("bedrijf_ids", [])
    if not item_id or not bedrijf_ids:
        raise HTTPException(status_code=400, detail="item_id en bedrijf_ids vereist")

    ids = []
    for bedrijf_id in bedrijf_ids:
        try:
            aanbieding_id = db.create_aanbieding(item_id, int(bedrijf_id), user_id=user["id"])
            ids.append(aanbieding_id)
            fs.sync_aanbieding({"id": aanbieding_id, "item_id": item_id, "bedrijf_id": int(bedrijf_id), "status": "open"})
        except Exception:
            pass

    gemeente = _gemeente_filter(user)
    gemeenten = _gemeenten_expand(gemeente)
    cache_key = f"items:{gemeenten or gemeente}:0:{user['id']}"
    cache_module.delete(cache_key, f"items:None:0:{user['id']}")

    async def _stuur_bulk_push():
        item = db.get_item(item_id)
        label = (item or {}).get("ai_label") or "nieuw materiaal"
        for bedrijf_id in bedrijf_ids:
            subscriptions = db.get_push_subscriptions_for_bedrijf(int(bedrijf_id))
            for sub in subscriptions:
                ok = push_module.send_push(
                    sub["subscription"],
                    title="Nieuw aanbod — CIRQO",
                    body=f"Er is {label} aangeboden.",
                    url="/",
                )
                if not ok:
                    db.delete_push_subscription(sub["subscription"])

    asyncio.create_task(_stuur_bulk_push())
    return {"ids": ids, "count": len(ids)}


@app.get("/api/push/vapid-key")
async def get_vapid_key():
    return {"public_key": push_module.get_public_key()}


@app.post("/api/push/subscribe")
async def push_subscribe(request: Request, user=Depends(get_current_user)):
    data = await request.json()
    subscription = data.get("subscription")
    if not subscription:
        raise HTTPException(status_code=400, detail="subscription vereist")
    import json as _json
    bedrijf_id = user.get("bedrijf_id")
    user_id = user["id"]
    db.save_push_subscription(_json.dumps(subscription), bedrijf_id=bedrijf_id, user_id=user_id)
    print(f"[push] Subscription opgeslagen voor user {user_id} (bedrijf_id={bedrijf_id})")
    return {"ok": True}


@app.get("/api/push/debug")
async def push_debug(user=Depends(require_admin)):
    """Toon alle push subscriptions (alleen voor beheerders)."""
    with db.get_cursor() as cur:
        cur.execute("SELECT bedrijf_id, id, LEFT(subscription, 60) as sub_preview FROM push_subscriptions ORDER BY bedrijf_id")
        return [dict(r) for r in cur.fetchall()]


@app.get("/api/aanbiedingen")
async def get_aanbiedingen(gemeente: Optional[str] = None, user=Depends(require_admin)):
    g = _gemeente_filter(user, gemeente)
    return db.get_aanbiedingen_voor_beheer(g)


@app.get("/api/mijn-aanbiedingen-als-aanbieder")
async def mijn_aanbiedingen_als_aanbieder(user=Depends(get_current_user)):
    cache_key = f"mijn_aanbiedingen:{user['id']}"
    cached = cache_module.get(cache_key)
    if cached is not None:
        return cached
    data = db.get_aanbiedingen_door_user(user["id"])
    cache_module.set(cache_key, data, ttl=30)
    return data


@app.get("/api/mijn-aanbiedingen")
async def mijn_aanbiedingen(user=Depends(get_current_user)):
    if user["role"] != "bedrijf" or not user.get("bedrijf_id"):
        raise HTTPException(status_code=403)
    return db.get_aanbiedingen_voor_bedrijf(user["bedrijf_id"])


@app.patch("/api/mijn-aanbiedingen/{aanbieding_id}")
async def update_mijn_aanbieding(aanbieding_id: int, status: str = Form(...),
                                  user=Depends(get_current_user)):
    if not user.get("bedrijf_id"):
        raise HTTPException(status_code=403)
    if status not in ("ophalen", "niet_nodig"):
        raise HTTPException(status_code=400, detail="Ongeldige status")
    db.update_aanbieding_status(aanbieding_id, status)
    fs.update_aanbieding_status(aanbieding_id, status)

    async def _afhandel():
        with db.get_cursor() as cur:
            cur.execute("SELECT aangeboden_door, item_id FROM aanbiedingen WHERE id = %s", (aanbieding_id,))
            row = cur.fetchone()
        if not row or not row["aangeboden_door"]:
            return
        offerer_id = row["aangeboden_door"]
        item_id    = row["item_id"]
        item       = db.get_item(item_id)
        label      = (item or {}).get("ai_label") or "materiaal"
        bedrijf_naam = user.get("username", "het bedrijf")

        if status == "niet_nodig":
            # Auto-doorsturen als aanbieder dat heeft ingesteld
            aanbieder = db.get_user_by_id(offerer_id)
            if aanbieder and aanbieder.get("auto_doorsturen"):
                gemeente = (item or {}).get("gemeente")
                category = (item or {}).get("category")
                volgend  = db.get_volgend_bedrijf(item_id, gemeente, category, user_id=offerer_id)
                if volgend:
                    nieuw_id = db.create_aanbieding(item_id, volgend["id"], user_id=offerer_id)
                    cache_module.delete(f"items:{gemeente}:0:{offerer_id}", f"items:None:0:{offerer_id}")
                    # Push naar aanbieder: doorgestuurd
                    subs = db.get_push_subscriptions_voor_user(offerer_id)
                    for sub in subs:
                        push_module.send_push(sub["subscription"],
                            title="Automatisch doorgestuurd",
                            body=f"{label} is doorgestuurd naar {volgend['naam']}",
                            url="/")
                    # Push naar volgend bedrijf
                    subscriptions = db.get_push_subscriptions_for_bedrijf(volgend["id"])
                    for sub in subscriptions:
                        push_module.send_push(sub["subscription"],
                            title="Nieuw aanbod",
                            body=f"Er is {label} beschikbaar voor jou",
                            url="/")
                    return  # geen "niet nodig" push meer nodig

        # Standaard push naar aanbieder
        status_tekst = "wil het ophalen 🚚" if status == "ophalen" else "heeft het niet nodig"
        subs = db.get_push_subscriptions_voor_user(offerer_id)
        for sub in subs:
            push_module.send_push(sub["subscription"],
                title=f"{bedrijf_naam} reageert",
                body=f"{bedrijf_naam} {status_tekst}: {label}",
                url="/")

    asyncio.create_task(_afhandel())
    return {"ok": True}


# ── Berichten ─────────────────────────────────────────────────────────────────

@app.get("/api/aanbiedingen/{aanbieding_id}/berichten")
async def get_berichten(aanbieding_id: int, user=Depends(get_current_user)):
    # Toegang: aanbieder, bedrijf dat de aanbieding ontving, of admin
    info = db.get_aanbieding_deelnemers(aanbieding_id)
    if not info:
        raise HTTPException(status_code=404)
    is_aanbieder = info.get("aangeboden_door") == user["id"]
    is_bedrijf   = user.get("bedrijf_id") == info.get("bedrijf_id")
    is_admin     = user["role"] in ("admin", "superadmin")
    if not (is_aanbieder or is_bedrijf or is_admin):
        raise HTTPException(status_code=403)
    return db.get_berichten(aanbieding_id)


@app.post("/api/aanbiedingen/{aanbieding_id}/berichten")
async def stuur_bericht(
    aanbieding_id: int,
    tekst: str = Form(...),
    user=Depends(get_current_user),
):
    if not tekst.strip():
        raise HTTPException(status_code=400, detail="Bericht mag niet leeg zijn")
    info = db.get_aanbieding_deelnemers(aanbieding_id)
    if not info:
        raise HTTPException(status_code=404)
    is_aanbieder = info.get("aangeboden_door") == user["id"]
    is_bedrijf   = user.get("bedrijf_id") == info.get("bedrijf_id")
    is_admin     = user["role"] in ("admin", "superadmin")
    if not (is_aanbieder or is_bedrijf or is_admin):
        raise HTTPException(status_code=403)

    naam = localStorage_naam = user.get("username", "onbekend")
    bericht = db.stuur_bericht(aanbieding_id, user["id"], tekst)

    # Push naar de andere partij op de achtergrond
    async def _push_bericht():
        label = info.get("ai_label") or "materiaal"
        print(f"[push-chat] is_bedrijf={is_bedrijf}, aanbieding={aanbieding_id}, label={label!r}")
        if is_bedrijf:
            offerer_id = info.get("aangeboden_door")
            print(f"[push-chat] bedrijf→aanbieder offerer_id={offerer_id}")
            if offerer_id:
                subs = db.get_push_subscriptions_voor_user(offerer_id)
                print(f"[push-chat] {len(subs)} sub(s) voor user {offerer_id}")
                for sub in subs:
                    ok = push_module.send_push(sub["subscription"],
                        title=f"Nieuw bericht over {label}",
                        body=f"{naam}: {tekst[:80]}", url="/")
                    print(f"[push-chat] verzonden: {'OK' if ok else 'MISLUKT'}")
                    if not ok:
                        db.delete_push_subscription(sub["subscription"])
        else:
            bedrijf_id_push = info.get("bedrijf_id")
            print(f"[push-chat] aanbieder→bedrijf bedrijf_id={bedrijf_id_push}")
            if bedrijf_id_push:
                subs = db.get_push_subscriptions_for_bedrijf(bedrijf_id_push)
                print(f"[push-chat] {len(subs)} sub(s) voor bedrijf {bedrijf_id_push}")
                for sub in subs:
                    ok = push_module.send_push(sub["subscription"],
                        title=f"Nieuw bericht over {label}",
                        body=f"{naam}: {tekst[:80]}", url="/")
                    print(f"[push-chat] verzonden: {'OK' if ok else 'MISLUKT'}")
                    if not ok:
                        db.delete_push_subscription(sub["subscription"])

    asyncio.create_task(_push_bericht())
    return {**bericht, "user_id": user["id"],
            "naam": naam, "tekst": tekst.strip()}


# ── Inzamellijst ─────────────────────────────────────────────────────────────

@app.get("/api/inzamellijst")
async def get_inzamellijst(gemeente: Optional[str] = None, user=Depends(require_admin)):
    g = _gemeente_filter(user, gemeente) or user.get("gemeente") or ""
    if not g:
        raise HTTPException(status_code=400, detail="Gemeente vereist")
    return db.get_inzamellijst(g)


@app.post("/api/inzamellijst")
async def add_inzamellijst(
    product: str = Form(...),
    gemeente: Optional[str] = Form(None),
    user=Depends(require_admin),
):
    g = gemeente.strip() if gemeente and gemeente.strip() else user.get("gemeente") or ""
    if not g:
        raise HTTPException(status_code=400, detail="Gemeente vereist")
    if not product.strip():
        raise HTTPException(status_code=400, detail="Product mag niet leeg zijn")
    entry_id = db.add_to_inzamellijst(g, product)
    return {"id": entry_id, "product": product.strip(), "gemeente": g}


@app.put("/api/inzamellijst")
async def set_inzamellijst(request: Request, user=Depends(require_admin)):
    """Vervangt de volledige inzamellijst voor een gemeente (bulk)."""
    data = await request.json()
    g = (data.get("gemeente") or "").strip() or user.get("gemeente") or ""
    if not g:
        raise HTTPException(status_code=400, detail="Gemeente vereist")
    producten = [str(p).strip() for p in data.get("producten", []) if str(p).strip()]
    db.set_inzamellijst(g, producten)
    return {"ok": True, "count": len(producten)}


@app.delete("/api/inzamellijst/{entry_id}")
async def delete_inzamellijst(entry_id: int, gemeente: Optional[str] = None, user=Depends(require_admin)):
    g = _gemeente_filter(user, gemeente) or user.get("gemeente") or ""
    if not g:
        raise HTTPException(status_code=400, detail="Gemeente vereist")
    db.remove_from_inzamellijst(entry_id, g)
    return {"ok": True}


# ── Upload ────────────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload(
    file: UploadFile = File(...),
    manual_note: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    gemeente_override: Optional[str] = Form(None),
    user=Depends(get_current_user),
):
    try:
        content = await file.read()
        try:
            from PIL import Image, ImageOps
            import io as _io
            img = Image.open(_io.BytesIO(content))
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            if img.width > 512:
                ratio = 512 / img.width
                img = img.resize((512, int(img.height * ratio)), Image.LANCZOS)
            buf = _io.BytesIO()
            img.save(buf, format="JPEG", quality=72)
            content = buf.getvalue()
        except Exception as e:
            print(f"[upload] Resize overgeslagen: {e}")

        image_b64 = base64.b64encode(content).decode("utf-8")
        loop = asyncio.get_event_loop()
        # GPS-gemeente heeft voorrang boven account-gemeente
        gemeente = gemeente_override.strip() if gemeente_override and gemeente_override.strip() else (user.get("gemeente") or None)

        # Inzamellijst ophalen: eerst specifieke gemeente, dan fallback op alles
        inzamellijst_items = []
        if gemeente:
            inzamellijst_items = [e["product"] for e in db.get_inzamellijst(gemeente)]
        if not inzamellijst_items:
            # Geen gemeente of geen lijst voor dit gemeente → alle producten als fallback
            inzamellijst_items = [e["product"] for e in db.get_inzamellijst_alle()]

        (label, detail, gewicht_kg, ai_category, geaccepteerd), photo_url = await asyncio.gather(
            loop.run_in_executor(None, ai_module.analyse_photo, image_b64, inzamellijst_items),
            loop.run_in_executor(None, storage_module.upload_photo, content),
        )

        item_id = db.insert_item(photo_url, label, detail, gewicht_kg, gemeente, True, uploaded_by=user["id"])
        # Gebruik AI-categorie tenzij handmatig overschreven
        final_category = category if category else ai_category
        if manual_note or final_category:
            db.update_item(item_id, manual_note, final_category)
        item = db.get_item(item_id)
        fs.sync_item(item)
        # Cache invalideren met BAR-expanded gemeenten zodat key matcht
        gemeenten_exp = _gemeenten_expand(user.get("gemeente"))
        user_gem = user.get("gemeente") or gemeente
        cache_module.delete(
            f"items:{gemeenten_exp or user_gem}:0:{user['id']}",
            f"items:None:0:{user['id']}",
            f"stats:{gemeente}", "stats:None"
        )
        # Bedrijven ophalen op basis van account-gemeente (niet GPS), BAR-expanded
        acct_gemeente = user.get("gemeente") or None
        acct_gemeenten = _gemeenten_expand(acct_gemeente)
        alle_bedrijven = db.get_bedrijven(None if acct_gemeenten else acct_gemeente, gemeenten=acct_gemeenten)
        if final_category:
            match_ids = {b["id"] for b in db.get_bedrijven_for_item(acct_gemeente, final_category, gemeenten=acct_gemeenten)}
            if not match_ids:
                match_ids = {b["id"] for b in db.get_bedrijven_for_item(None, final_category)}
        else:
            match_ids = set()
        for b in alle_bedrijven:
            b["categorie_match"] = b["id"] in match_ids
        alle_bedrijven.sort(key=lambda b: (not b["categorie_match"], b["naam"]))
        item["bedrijven"] = alle_bedrijven
        print(f"[main] Item {item_id}: {label} ({gewicht_kg} kg) [{gemeente}]")
        return item

    except HTTPException:
        raise
    except Exception as e:
        import traceback
        print(f"[upload] FOUT: {traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Items ─────────────────────────────────────────────────────────────────────

@app.get("/api/items")
async def list_items(limit: int = 200, offset: int = 0, gemeente: Optional[str] = None, user=Depends(get_current_user)):
    bedrijf_id = user.get("bedrijf_id")
    if bedrijf_id:
        # Items aangeboden ÁÁN dit bedrijf
        ontvangen = db.get_items_voor_bedrijf(bedrijf_id)
        # Items die het bedrijf zelf gescand/aangeboden heeft (als scanner)
        user_id = user["id"]
        g = user.get("gemeente") or None
        gm = _gemeenten_expand(g)
        eigen = db.get_items(200, 0, None if gm else g, user_id=user_id, gemeenten=gm, own_user_id=user_id)
        ontvangen_ids = {i["id"] for i in ontvangen}
        extra = [i for i in eigen if i["id"] not in ontvangen_ids]
        return ontvangen + extra
    gemeente = _gemeente_filter(user, gemeente)
    gemeenten = _gemeenten_expand(gemeente)
    user_id = user["id"]
    is_admin = user["role"] in ("admin", "superadmin")
    # Scanner always sees their own items regardless of GPS gemeente
    own_user_id = user_id if user["role"] == "user" else None
    cache_key = f"items:{gemeenten or gemeente}:{offset}:{user_id}"
    cached = cache_module.get(cache_key)
    if cached is not None:
        return cached
    items = db.get_items(limit, offset, None if gemeenten else gemeente, user_id=user_id, gemeenten=gemeenten, own_user_id=own_user_id, all_aanbiedingen=is_admin)
    cache_module.set(cache_key, items, ttl=30)
    return items


@app.get("/api/items/{item_id}")
async def get_item(item_id: int, user=Depends(get_current_user)):
    item = db.get_item(item_id)
    if not item:
        raise HTTPException(status_code=404)
    return item


@app.get("/api/items/{item_id}/aanbiedingen")
async def get_item_aanbiedingen(item_id: int, user=Depends(get_current_user)):
    return db.get_aanbiedingen_voor_item(item_id)


@app.patch("/api/aanbiedingen/{aanbieding_id}/status")
async def update_aanbieding_status_admin(
    aanbieding_id: int,
    status: str = Form(...),
    user=Depends(get_current_user),
):
    if status not in ("open", "ophalen", "niet_nodig"):
        raise HTTPException(status_code=400, detail="Ongeldige status")
    db.update_aanbieding_status(aanbieding_id, status)
    fs.update_aanbieding_status(aanbieding_id, status)
    return {"ok": True}


@app.patch("/api/items/{item_id}")
async def update_item(
    item_id: int,
    manual_note: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    user=Depends(get_current_user),
):
    if not db.get_item(item_id):
        raise HTTPException(status_code=404)
    db.update_item(item_id, manual_note, category)
    return {"ok": True}


@app.delete("/api/items/{item_id}")
async def delete_item(item_id: int, user=Depends(get_current_user)):
    if not db.get_item(item_id):
        raise HTTPException(status_code=404)
    db.delete_item(item_id)
    fs.delete_item(item_id)
    gemeente = _gemeente_filter(user)
    cache_module.delete(f"items:{gemeente}:0:{user['id']}", f"items:None:0:{user['id']}", f"stats:{gemeente}", "stats:None")
    return {"ok": True}


# ── Dashboard (gecombineerd endpoint) ────────────────────────────────────────

@app.get("/api/dashboard")
async def get_dashboard(days: int = 7, gemeente: Optional[str] = None, user=Depends(get_current_user)):
    gemeente = _gemeente_filter(user, gemeente)
    gemeenten = _gemeenten_expand(gemeente)
    cache_key = f"dashboard:{gemeenten or gemeente}:{days}"
    cached = cache_module.get(cache_key)
    if cached is not None:
        return cached

    g = None if gemeenten else gemeente
    stats, charts, recent = await asyncio.gather(
        asyncio.get_event_loop().run_in_executor(None, lambda: db.get_stats(g, gemeenten=gemeenten)),
        asyncio.get_event_loop().run_in_executor(None, lambda: db.get_chart_data(days, g, gemeenten=gemeenten)),
        asyncio.get_event_loop().run_in_executor(None, lambda: db.get_items(6, 0, g, gemeenten=gemeenten)),
    )
    result = {"stats": stats, "charts": charts, "recent": recent}
    cache_module.set(cache_key, result, ttl=30)
    return result


# ── Statistieken & export ─────────────────────────────────────────────────────

@app.get("/api/mijn-volgorde")
async def get_mijn_volgorde(user=Depends(get_current_user)):
    return db.get_volgorde(user["id"])


@app.put("/api/mijn-volgorde")
async def sla_mijn_volgorde(request: Request, user=Depends(get_current_user)):
    bedrijf_ids = await request.json()
    if not isinstance(bedrijf_ids, list):
        raise HTTPException(status_code=400)
    db.sla_volgorde_op(user["id"], [int(i) for i in bedrijf_ids])
    return {"ok": True}


@app.get("/api/netwerk")
async def get_netwerk(gemeente: Optional[str] = None, user=Depends(require_admin)):
    g = _gemeente_filter(user, gemeente)
    return db.get_netwerk_data(g)


@app.get("/api/deelnemers")
async def get_deelnemers(user=Depends(get_current_user)):
    """Alle deelnemende bedrijven in het netwerk — zichtbaar voor ingelogde bedrijfsaccounts."""
    gemeente = user.get("gemeente", "")
    gemeenten = _gemeenten_expand(gemeente)
    with db.get_cursor() as cur:
        if gemeenten:
            cur.execute("""
                SELECT b.id, b.naam, b.gemeente, b.email, b.telefoon,
                       COALESCE(array_agg(DISTINCT bc.category)
                         FILTER (WHERE bc.category IS NOT NULL), '{}') AS categorieen,
                       COUNT(DISTINCT a.id) FILTER (WHERE a.status != 'niet_nodig') AS aanbieding_count
                FROM bedrijven b
                LEFT JOIN bedrijf_categorieen bc ON bc.bedrijf_id = b.id
                LEFT JOIN aanbiedingen a ON a.bedrijf_id = b.id
                WHERE b.gemeente = ANY(%s)
                GROUP BY b.id
                ORDER BY b.naam
            """, (gemeenten,))
        else:
            cur.execute("""
                SELECT b.id, b.naam, b.gemeente, b.email, b.telefoon,
                       COALESCE(array_agg(DISTINCT bc.category)
                         FILTER (WHERE bc.category IS NOT NULL), '{}') AS categorieen,
                       COUNT(DISTINCT a.id) FILTER (WHERE a.status != 'niet_nodig') AS aanbieding_count
                FROM bedrijven b
                LEFT JOIN bedrijf_categorieen bc ON bc.bedrijf_id = b.id
                LEFT JOIN aanbiedingen a ON a.bedrijf_id = b.id
                WHERE b.gemeente = %s
                GROUP BY b.id
                ORDER BY b.naam
            """, (gemeente,))
        return {"gemeente": gemeente, "bedrijven": [dict(r) for r in cur.fetchall()]}


@app.get("/api/stats")
async def get_stats(gemeente: Optional[str] = None, user=Depends(get_current_user)):
    bedrijf_id = user.get("bedrijf_id")
    if bedrijf_id:
        items = db.get_items_voor_bedrijf(bedrijf_id)
        totaal_kg = sum(i["gewicht_kg"] or 0 for i in items)
        from collections import Counter
        cat_counts = Counter(i["category"] for i in items if i.get("category"))
        categories = [{"category": k, "count": v} for k, v in cat_counts.most_common()]
        return {"total": len(items), "today": 0, "totaal_kg": round(totaal_kg, 1), "categories": categories}
    if user["role"] == "superadmin":
        # Superadmin: vrij filteren op gemeente
        pass
    elif user["role"] in ("admin",):
        gemeente = _gemeente_filter(user, gemeente)
    else:
        # Gewone gebruiker: alleen eigen aangeboden items
        data = db.get_stats(user_id=user["id"])
        return data
    gemeenten = _gemeenten_expand(gemeente)
    cache_key = f"stats:{gemeenten or gemeente}"
    cached = cache_module.get(cache_key)
    if cached is not None:
        return cached
    g = None if gemeenten else gemeente
    data = db.get_stats(g, gemeenten=gemeenten)
    cache_module.set(cache_key, data, ttl=30)
    return data


@app.get("/api/charts")
async def get_charts(days: int = 30, gemeente: Optional[str] = None, user=Depends(get_current_user)):
    gemeente = _gemeente_filter(user, gemeente)
    gemeenten = _gemeenten_expand(gemeente)
    cache_key = f"charts:{gemeenten or gemeente}:{days}"
    cached = cache_module.get(cache_key)
    if cached is not None:
        return cached
    g = None if gemeenten else gemeente
    data = db.get_chart_data(days, g, gemeenten=gemeenten)
    cache_module.set(cache_key, data, ttl=60)
    return data


@app.get("/api/export/csv")
async def export_csv(gemeente: Optional[str] = None, user=Depends(require_admin)):
    csv_data = db.export_csv(_gemeente_filter(user, gemeente))
    fname = f"bouwkringloop_{gemeente or 'alle'}_{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_data.encode("utf-8-sig"),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={fname}"},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/debug")
async def debug():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return {"key_set": bool(key), "key_prefix": key[:12] if key else "leeg"}


# ── Pagina's ──────────────────────────────────────────────────────────────────

from fastapi.responses import FileResponse
import mimetypes

@app.get("/static/{path:path}")
async def static_files(path: str):
    file_path = f"static/{path}"
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404)
    media_type, _ = mimetypes.guess_type(file_path)
    ext = os.path.splitext(path)[1].lower()
    if ext in (".html",):
        cache = "no-store"
    elif ext in (".png", ".webp", ".jpg", ".jpeg", ".svg", ".ico", ".woff2"):
        cache = "public, max-age=604800, immutable"  # 7 dagen
    else:
        cache = "public, max-age=86400"  # 1 dag voor css/js
    return FileResponse(file_path, media_type=media_type or "application/octet-stream",
                        headers={"Cache-Control": cache})


@app.get("/sw.js")
async def service_worker():
    with open("static/sw.js", "r", encoding="utf-8") as f:
        return Response(content=f.read(), media_type="application/javascript",
                        headers={"Cache-Control": "no-store"})


DEFAULT_BRAND = os.getenv("BRAND", "bar")

# Hostnames die een specifiek brand activeren (ook als substring)
HOST_BRAND_MAP = {
    "hvc": "hvc",
    "bar": "bar",
}


def _detect_brand(request: Request) -> str:
    host = request.headers.get("host", "").lower().split(":")[0]
    for keyword, brand in HOST_BRAND_MAP.items():
        if keyword in host:
            return brand
    return DEFAULT_BRAND


@app.get("/", response_class=HTMLResponse)
async def scan_app(request: Request):
    return _render_html("static/index.html", _detect_brand(request))


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return _render_html("static/login.html", _detect_brand(request))


@app.get("/auth-action", response_class=HTMLResponse)
async def auth_action_page(request: Request):
    return _render_html("static/auth-action.html", _detect_brand(request))


@app.get("/beheer", response_class=HTMLResponse)
async def beheer_page(request: Request):
    return _render_html("static/beheer.html", _detect_brand(request))


@app.get("/debug-brand")
async def debug_brand(request: Request):
    import os as _os2
    detected = _detect_brand(request)
    return {"DEFAULT_BRAND": DEFAULT_BRAND, "detected_brand": detected, "host": request.headers.get("host"), "BRAND_ENV": _os2.getenv("BRAND")}


@app.get("/bedrijf", response_class=HTMLResponse)
async def bedrijf_page(request: Request):
    return _render_html("static/bedrijf.html", _detect_brand(request))


@app.get("/bedrijf/{token}", response_class=HTMLResponse)
async def bedrijf_page_token(token: str, request: Request):
    return _render_html("static/bedrijf.html", _detect_brand(request))



import os as _os, hashlib as _hashlib
_THUMB_DIR = "/tmp/thumbs"
_os.makedirs(_THUMB_DIR, exist_ok=True)

def _firebase_thumb_url(original_url: str) -> str:
    """Transform a Firebase Storage URL to its _1024x1024 resized variant."""
    import re, urllib.parse
    # Extract path from Firebase Storage URL
    # https://firebasestorage.googleapis.com/v0/b/BUCKET/o/PATH?alt=media&token=...
    m = re.match(r'(https://firebasestorage\.googleapis\.com/v0/b/[^/]+/o/)([^?]+)(\?.*)?', original_url)
    if not m:
        return original_url
    base, encoded_path, _ = m.group(1), m.group(2), m.group(3)
    path = urllib.parse.unquote(encoded_path)
    if not path.endswith('.jpg'):
        return original_url
    thumb_path = path[:-4] + '_1024x1024.jpg'
    return base + urllib.parse.quote(thumb_path, safe='') + '?alt=media'


@app.get("/api/thumb")
async def get_thumb(url: str, size: int = 400):
    import ssl, urllib.request, io
    from PIL import Image
    from fastapi.responses import Response

    cache_key = _hashlib.md5(f"{url}{size}".encode()).hexdigest()
    thumb_path = f"{_THUMB_DIR}/{cache_key}.jpg"

    if _os.path.exists(thumb_path):
        with open(thumb_path, "rb") as f:
            return Response(content=f.read(), media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=604800"})

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    # Try the _1024x1024 Firebase variant first (much smaller, no resize needed)
    thumb_url = _firebase_thumb_url(url)
    fetch_url = thumb_url if thumb_url != url else url
    need_resize = (fetch_url == url)

    try:
        with urllib.request.urlopen(fetch_url, timeout=10, context=ssl_ctx) as r:
            data = r.read()
    except Exception:
        if fetch_url != url:
            # Fallback to original
            try:
                with urllib.request.urlopen(url, timeout=10, context=ssl_ctx) as r:
                    data = r.read()
                need_resize = True
            except Exception as e:
                raise HTTPException(status_code=502, detail=str(e))
        else:
            raise HTTPException(status_code=502, detail="fetch failed")

    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        if need_resize:
            img.thumbnail((size, size), Image.LANCZOS)
        else:
            img.thumbnail((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=82)
        result = buf.getvalue()
        with open(thumb_path, "wb") as f:
            f.write(result)
        return Response(content=result, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/tuya/test/{kleur}")
async def tuya_test(kleur: str):
    import tuya as t
    if kleur == "wit":
        return t.lamp_groen()
    elif kleur == "oranje":
        return t.lamp_rood()
    elif kleur == "uit":
        return t.lamp_uit()
    elif kleur == "status":
        return t.lamp_status()
    elif kleur == "functies":
        return t.lamp_functies()
    raise HTTPException(status_code=400, detail="wit / oranje / uit / status / functies")


@app.get("/kiosk", response_class=HTMLResponse)
async def kiosk_page():
    return _render_html("static/kiosk.html", DEFAULT_BRAND)


@app.post("/api/kiosk/scan")
async def kiosk_scan(file: UploadFile = File(...), gemeente: str = Form("Almere")):
    import base64, asyncio
    from PIL import Image, ImageOps
    import io as _io

    content = await file.read()
    try:
        img = Image.open(_io.BytesIO(content))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        if img.width > 768:
            ratio = 768 / img.width
            img = img.resize((768, int(img.height * ratio)), Image.LANCZOS)
        buf = _io.BytesIO()
        img.save(buf, format="JPEG", quality=80)
        content = buf.getvalue()
    except Exception as e:
        print(f"[kiosk] resize fout: {e}")

    image_b64 = base64.b64encode(content).decode("utf-8")
    inzamellijst_items = [e["product"] for e in db.get_inzamellijst(gemeente)]
    if not inzamellijst_items:
        inzamellijst_items = [e["product"] for e in db.get_inzamellijst_alle()]

    loop = asyncio.get_event_loop()
    label, detail, gewicht_kg, category, geaccepteerd = await loop.run_in_executor(
        None, ai_module.analyse_photo, image_b64, inzamellijst_items
    )

    # Lamp aansturen — fire and forget
    import tuya as tuya_module
    async def stuur_lamp():
        try:
            if geaccepteerd:
                await loop.run_in_executor(None, tuya_module.lamp_groen)
            else:
                await loop.run_in_executor(None, tuya_module.lamp_rood)
        except Exception as e:
            print(f"[tuya] lamp fout: {e}")
    asyncio.create_task(stuur_lamp())

    return {
        "geaccepteerd": bool(geaccepteerd),
        "label": label or "Onbekend",
        "detail": detail or "",
        "gewicht_kg": gewicht_kg,
        "category": category,
    }


BRANDS = {
    "cirqo": {
        "primary": "#86bc97", "secondary": "#6aaa82",
        "logo": "/static/cirqo-logo.webp", "name": "CIRQO",
        "sub": "Digitaal productuitwisselingsnetwerk", "gemeente": "Almere",
    },
    "bar": {
        "primary": "#09be86", "secondary": "#07a874",
        "logo": "/static/bar-logo.jpg", "name": "BAR Afvalbeheer",
        "sub": "Breng uw afval slim in", "gemeente": "Barendrecht",
    },
    "hvc": {
        "primary": "#E3000F", "secondary": "#c20000",
        "logo": "/static/hvc-logo.jpg", "name": "HVC",
        "sub": "Digitaal productuitwisselingsnetwerk", "gemeente": "Alkmaar",
    },
    "bouwkringloop": {
        "primary": "#e67026", "secondary": "#c45c1a",
        "logo": "/static/bouwkringloop-logo.jpg", "name": "De Bouwkringloop",
        "sub": "Milieustraat Almere-Buiten", "gemeente": "",
    },
    "rwm": {
        "primary": "#F5C200", "secondary": "#D4A800",
        "logo": "/static/rwm-logo.svg", "name": "RWM",
        "sub": "afval & reiniging", "gemeente": "Roermond",
    },
}

def _brand_css(brand: str) -> str:
    b = BRANDS.get(brand, BRANDS["cirqo"])
    return f""":root {{ --orange: {b['primary']}; --orange2: {b['secondary']}; }}
.hdr-logo-img, .cat-logo, .kiosk-logo {{ content: url('{b['logo']}') !important; }}"""

def _render_html(path: str, brand: str) -> HTMLResponse:
    with open(path, "r", encoding="utf-8") as f:
        html = f.read()
    b = BRANDS.get(brand, BRANDS["cirqo"])
    # Inject brand CSS inline
    brand_css_tag = f'<style>{_brand_css(brand)}</style>'
    html = html.replace("</head>", f"{brand_css_tag}\n</head>", 1)
    # Swap logo src — vervang alle bekende logo-paden
    for known_logo in ["/static/cirqo-logo.webp", "/static/bar-logo.jpg", "/static/hvc-logo.svg", "/static/bouwkringloop-logo.jpg"]:
        html = html.replace(known_logo, b["logo"])
    # Swap subtitle
    html = html.replace("Milieustraat Almere-Buiten", b["sub"])
    html = html.replace("CIRQO", b["name"])
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})

@app.get("/brand.css")
async def brand_css(request: Request):
    css = _brand_css(_detect_brand(request))
    return Response(content=css, media_type="text/css", headers={"Cache-Control": "no-cache"})

@app.get("/bar/brand.css")
async def bar_brand_css():
    css = _brand_css("bar")
    return Response(content=css, media_type="text/css", headers={"Cache-Control": "no-cache"})

@app.get("/bar", response_class=HTMLResponse)
@app.get("/bar/", response_class=HTMLResponse)
async def bar_home():
    return _render_html("static/index.html", "bar")

@app.get("/bar/kiosk", response_class=HTMLResponse)
async def bar_kiosk():
    return _render_html("static/kiosk.html", "bar")

@app.get("/bar/catalogus", response_class=HTMLResponse)
async def bar_catalogus():
    return _render_html("static/catalogus.html", "bar")


@app.get("/hvc/brand.css")
async def hvc_brand_css():
    return Response(content=_brand_css("hvc"), media_type="text/css", headers={"Cache-Control": "no-cache"})

@app.get("/hvc", response_class=HTMLResponse)
@app.get("/hvc/", response_class=HTMLResponse)
async def hvc_home():
    return _render_html("static/index.html", "hvc")

@app.get("/hvc/login", response_class=HTMLResponse)
async def hvc_login():
    return _render_html("static/login.html", "hvc")

@app.get("/hvc/beheer", response_class=HTMLResponse)
async def hvc_beheer():
    return _render_html("static/beheer.html", "hvc")

@app.get("/hvc/bedrijf", response_class=HTMLResponse)
async def hvc_bedrijf():
    return _render_html("static/bedrijf.html", "hvc")


@app.get("/rwm/brand.css")
async def rwm_brand_css():
    return Response(content=_brand_css("rwm"), media_type="text/css", headers={"Cache-Control": "no-cache"})

@app.get("/rwm", response_class=HTMLResponse)
@app.get("/rwm/", response_class=HTMLResponse)
async def rwm_home():
    return _render_html("static/index.html", "rwm")

@app.get("/rwm/login", response_class=HTMLResponse)
async def rwm_login():
    return _render_html("static/login.html", "rwm")

@app.get("/rwm/kiosk", response_class=HTMLResponse)
async def rwm_kiosk():
    return _render_html("static/kiosk.html", "rwm")

@app.get("/rwm/catalogus", response_class=HTMLResponse)
async def rwm_catalogus():
    return _render_html("static/catalogus.html", "rwm")

@app.get("/rwm/beheer", response_class=HTMLResponse)
async def rwm_beheer():
    return _render_html("static/beheer.html", "rwm")

@app.get("/rwm/bedrijf", response_class=HTMLResponse)
async def rwm_bedrijf():
    return _render_html("static/bedrijf.html", "rwm")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_page():
    return _render_html("static/dashboard.html", DEFAULT_BRAND)


@app.get("/catalogus", response_class=HTMLResponse)
@app.get("/pilotalmere", response_class=HTMLResponse)
@app.get("/pilotalmere/", response_class=HTMLResponse)
async def catalogus_page():
    html = _render_html("static/catalogus.html", DEFAULT_BRAND).body.decode()
    html = re.sub(r'class="cat-hdr-sub">[^<]*<', 'class="cat-hdr-sub">Ingezameld bouwmateriaal · Milieustraat Almere-Buiten<', html)
    return HTMLResponse(html)


@app.get("/api/catalogus")
async def get_catalogus(gemeente: str = "Almere", limit: int = 48, offset: int = 0):
    gemeenten = _gemeenten_expand(gemeente)
    gem_filter = "gemeente = ANY(%s)" if gemeenten else "gemeente = %s"
    gem_param = gemeenten if gemeenten else gemeente
    with db.get_cursor() as cur:
        cur.execute(f"""
            SELECT id, ai_label, ai_detail, photo_url, photo_url_thumb, gewicht_kg, category
            FROM items
            WHERE {gem_filter} AND photo_url IS NOT NULL AND photo_url != ''
            ORDER BY timestamp DESC
            LIMIT %s OFFSET %s
        """, (gem_param, limit, offset))
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute(f"SELECT COUNT(*) AS cnt FROM items WHERE {gem_filter} AND photo_url IS NOT NULL AND photo_url != ''", (gem_param,))
        total = cur.fetchone()["cnt"]
        return {"items": rows, "total": total, "offset": offset, "limit": limit}


@app.post("/api/admin/bouw-thumb-urls")
async def bouw_thumb_urls(gemeente: str = "Almere", credentials: HTTPAuthorizationCredentials = Depends(security)):
    verify_superadmin(credentials)
    import re, urllib.parse as _up
    from firebase_admin import storage as _storage

    bucket = _storage.bucket()

    with db.get_cursor() as cur:
        cur.execute("""
            SELECT id, photo_url FROM items
            WHERE gemeente = %s AND photo_url IS NOT NULL AND photo_url_thumb IS NULL
        """, (gemeente,))
        items = cur.fetchall()

    updated = 0
    errors = 0
    for item in items:
        url = item["photo_url"]
        m = re.match(r'https://firebasestorage\.googleapis\.com/v0/b/([^/]+)/o/([^?]+)', url)
        if not m:
            continue
        bucket_name, encoded_path = m.group(1), m.group(2)
        path = _up.unquote(encoded_path)
        if not path.endswith('.jpg'):
            continue
        thumb_path = path[:-4] + '_1024x1024.jpg'
        try:
            blob = bucket.blob(thumb_path)
            blob.reload()
            token = (blob.metadata or {}).get('firebaseStorageDownloadTokens', '')
            if token:
                thumb_url = f"https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{_up.quote(thumb_path, safe='')}?alt=media&token={token}"
            else:
                # Fallback: use original
                thumb_url = url
            with db.get_cursor() as cur2:
                cur2.execute("UPDATE items SET photo_url_thumb = %s WHERE id = %s", (thumb_url, item["id"]))
            updated += 1
        except Exception:
            errors += 1

    return {"updated": updated, "errors": errors}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
