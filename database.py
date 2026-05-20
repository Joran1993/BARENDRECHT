"""
Database laag — PostgreSQL via Supabase
"""
import os
import psycopg2
import psycopg2.extras
import psycopg2.pool
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

DATABASE_URL = os.environ.get("DATABASE_URL")

_pool: psycopg2.pool.ThreadedConnectionPool = None
_POOL_MIN = 2
_POOL_MAX = int(os.environ.get("DB_POOL_MAX", "20"))

_CONNECT_KWARGS = dict(
    connect_timeout=10,
    keepalives=1,
    keepalives_idle=30,
    keepalives_interval=10,
    keepalives_count=5,
)


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.ThreadedConnectionPool(
            _POOL_MIN, _POOL_MAX, DATABASE_URL, **_CONNECT_KWARGS
        )
        print(f"[db] Connection pool aangemaakt (min={_POOL_MIN}, max={_POOL_MAX})")
    return _pool


@contextmanager
def get_cursor():
    pool = _get_pool()
    conn = pool.getconn()
    try:
        # Herstel stale verbinding
        if conn.closed:
            pool.putconn(conn, close=True)
            conn = pool.getconn()
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except psycopg2.OperationalError as e:
        try:
            conn.rollback()
        except Exception:
            pass
        # Verwijder kapotte verbinding uit de pool en probeer opnieuw met nieuwe
        try:
            pool.putconn(conn, close=True)
        except Exception:
            pass
        conn = None
        print(f"[db] Verbindingsfout, nieuwe verbinding: {e}")
        conn = pool.getconn()
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        if conn is not None:
            try:
                pool.putconn(conn)
            except Exception:
                pass


def init_db():
    with get_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id          SERIAL PRIMARY KEY,
                timestamp   TEXT NOT NULL,
                photo_url   TEXT,
                photo_url_thumb TEXT,
                ai_label    TEXT,
                ai_detail   TEXT,
                gewicht_kg  REAL,
                manual_note TEXT,
                category    TEXT,
                gemeente    TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id           SERIAL PRIMARY KEY,
                username     TEXT NOT NULL UNIQUE,
                password     TEXT NOT NULL,
                role         TEXT NOT NULL DEFAULT 'user',
                gemeente     TEXT,
                created_at   TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bedrijven (
                id           SERIAL PRIMARY KEY,
                naam         TEXT NOT NULL,
                gemeente     TEXT NOT NULL,
                contactpersoon TEXT,
                email        TEXT,
                telefoon     TEXT,
                created_at   TEXT NOT NULL
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS bedrijf_categorieen (
                bedrijf_id   INTEGER NOT NULL REFERENCES bedrijven(id) ON DELETE CASCADE,
                category     TEXT NOT NULL,
                PRIMARY KEY (bedrijf_id, category)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS aanbiedingen (
                id           SERIAL PRIMARY KEY,
                item_id      INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
                bedrijf_id   INTEGER NOT NULL REFERENCES bedrijven(id) ON DELETE CASCADE,
                status       TEXT NOT NULL DEFAULT 'open',
                created_at   TEXT NOT NULL,
                updated_at   TEXT
            )
        """)
        # bedrijf_id kolom toevoegen aan users als die nog niet bestaat
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS bedrijf_id INTEGER REFERENCES bedrijven(id) ON DELETE SET NULL")
        cur.execute("ALTER TABLE aanbiedingen ADD COLUMN IF NOT EXISTS aangeboden_door INTEGER REFERENCES users(id) ON DELETE SET NULL")
        # meld_token kolom toevoegen aan bedrijven als die nog niet bestaat
        cur.execute("""
            ALTER TABLE bedrijven ADD COLUMN IF NOT EXISTS meld_token TEXT
        """)
        # tokens genereren voor bestaande bedrijven zonder token
        cur.execute("SELECT id FROM bedrijven WHERE meld_token IS NULL")
        for row in cur.fetchall():
            import uuid
            cur.execute("UPDATE bedrijven SET meld_token = %s WHERE id = %s",
                        (str(uuid.uuid4()), row["id"]))
        cur.execute("""
            CREATE TABLE IF NOT EXISTS inzamellijst (
                id          SERIAL PRIMARY KEY,
                gemeente    TEXT NOT NULL,
                product     TEXT NOT NULL,
                created_at  TEXT NOT NULL,
                UNIQUE (gemeente, product)
            )
        """)
        cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS geaccepteerd BOOLEAN")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS push_subscriptions (
                id           SERIAL PRIMARY KEY,
                bedrijf_id   INTEGER REFERENCES bedrijven(id) ON DELETE CASCADE,
                user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
                subscription TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
        """)
        cur.execute("ALTER TABLE push_subscriptions ADD COLUMN IF NOT EXISTS user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS organisatie TEXT")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS auto_doorsturen BOOLEAN DEFAULT FALSE")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS berichten (
                id           SERIAL PRIMARY KEY,
                aanbieding_id INTEGER NOT NULL REFERENCES aanbiedingen(id) ON DELETE CASCADE,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                tekst        TEXT NOT NULL,
                created_at   TEXT NOT NULL
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_aanbiedingen_aangeboden_door ON aanbiedingen(aangeboden_door)")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS user_bedrijf_prioriteit (
                id         SERIAL PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                bedrijf_id INTEGER NOT NULL REFERENCES bedrijven(id) ON DELETE CASCADE,
                positie    INTEGER NOT NULL DEFAULT 0,
                UNIQUE (user_id, bedrijf_id)
            )
        """)
        cur.execute("CREATE INDEX IF NOT EXISTS idx_ubp_user ON user_bedrijf_prioriteit(user_id, positie)")
        cur.execute("ALTER TABLE items ADD COLUMN IF NOT EXISTS uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_items_uploaded_by ON items(uploaded_by)")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS firebase_uid TEXT UNIQUE")
        cur.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS email TEXT")
        # Zorg dat er een UNIQUE constraint op subscription staat (migratie van oude constraint)
        cur.execute("""
            DO $$ BEGIN
                IF NOT EXISTS (
                    SELECT 1 FROM pg_constraint
                    WHERE conname = 'push_subscriptions_subscription_key'
                ) THEN
                    -- Verwijder oude constraint als die bestaat
                    ALTER TABLE push_subscriptions DROP CONSTRAINT IF EXISTS push_subscriptions_bedrijf_id_subscription_key;
                    ALTER TABLE push_subscriptions ADD CONSTRAINT push_subscriptions_subscription_key UNIQUE (subscription);
                END IF;
            END $$;
        """)
    _create_default_admin()
    _create_waardlanden_accounts()
    print("[db] PostgreSQL tabellen gereed")


def _create_default_admin():
    username = os.environ.get("ADMIN_USERNAME", "admin")
    password = os.environ.get("ADMIN_PASSWORD", "")
    gemeente = os.environ.get("ADMIN_GEMEENTE", "")
    if not password:
        return
    from auth import hash_password
    with get_cursor() as cur:
        cur.execute("SELECT 1 FROM users WHERE username = %s", (username,))
        if not cur.fetchone():
            cur.execute(
                "INSERT INTO users (username, password, role, gemeente, created_at) VALUES (%s, %s, 'superadmin', %s, %s)",
                (username, hash_password(password), gemeente, datetime.now(timezone.utc).isoformat(timespec="seconds")),
            )


def _create_waardlanden_accounts():
    """Maak Waardlanden admin-accounts aan als ze nog niet bestaan."""
    from auth import hash_password
    accounts = [
        ("gittaspruit@waardlanden.nl", "Gitta Spruit", "admin", "waardlanden", "Waardlanden2025!"),
        ("waardlanden-test",           "Waardlanden Test", "admin", "waardlanden", "test-waardlanden"),
    ]
    with get_cursor() as cur:
        for username, naam, role, gemeente, password in accounts:
            cur.execute("SELECT 1 FROM users WHERE username = %s OR email = %s", (username, username))
            if cur.fetchone():
                continue
            cur.execute(
                """INSERT INTO users (username, password, role, gemeente, organisatie, email, created_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (username, hash_password(password), role, gemeente, naam, username,
                 datetime.now(timezone.utc).isoformat(timespec="seconds"))
            )
            print(f"[db] Account aangemaakt: {username} (role={role}, gemeente={gemeente})")


# ── Gebruikers ────────────────────────────────────────────────────────────────

def get_user_by_username(username: str):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE LOWER(username) = %s", (username.lower(),))
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_by_id(user_id: int):
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, username, role, gemeente, organisatie, auto_doorsturen, created_at FROM users WHERE id = %s", (user_id,)
        )
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_by_firebase_uid(firebase_uid: str):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE firebase_uid = %s", (firebase_uid,))
        row = cur.fetchone()
        return dict(row) if row else None


def upsert_firebase_user(firebase_uid: str, email: str, naam: str,
                         gemeente: str = "", role: str = "user") -> dict:
    """Maak of update een gebruiker op basis van Firebase UID. Geeft het user-dict terug."""
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE firebase_uid = %s", (firebase_uid,))
        row = cur.fetchone()
        if not row and email:
            # Fallback: zoek op email of username (pre-aangemaakte accounts koppelen aan Firebase)
            cur.execute("SELECT * FROM users WHERE email = %s OR username = %s", (email, email))
            row = cur.fetchone()
        if row:
            # Behoud bestaande rol voor admin/superadmin — Firebase login mag die niet terugzetten
            effective_role = row["role"] if row["role"] in ("superadmin", "admin") else role
            cur.execute(
                "UPDATE users SET email=%s, firebase_uid=%s, role=%s, organisatie=COALESCE(NULLIF(organisatie,''), %s), gemeente=COALESCE(NULLIF(gemeente,''), %s) WHERE id=%s RETURNING *",
                (email, firebase_uid, effective_role, naam, gemeente, row["id"])
            )
            return dict(cur.fetchone())
        # Nieuw account aanmaken — geen wachtwoord nodig (firebase_uid is de authenticatie)
        username = email.split("@")[0]
        # Zorg voor unieke username
        base = username
        i = 1
        while True:
            cur.execute("SELECT 1 FROM users WHERE username=%s", (username,))
            if not cur.fetchone():
                break
            username = f"{base}{i}"
            i += 1
        cur.execute(
            """INSERT INTO users (username, password, role, gemeente, organisatie, firebase_uid, email, created_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING *""",
            (username, "", role, gemeente, naam, firebase_uid, email,
             datetime.now(timezone.utc).isoformat(timespec="seconds"))
        )
        return dict(cur.fetchone())


def set_auto_doorsturen(user_id: int, enabled: bool):
    with get_cursor() as cur:
        cur.execute("UPDATE users SET auto_doorsturen = %s WHERE id = %s", (enabled, user_id))


def get_volgorde(user_id: int) -> list:
    with get_cursor() as cur:
        cur.execute("""
            SELECT ubp.positie, b.id, b.naam, b.gemeente
            FROM user_bedrijf_prioriteit ubp
            JOIN bedrijven b ON b.id = ubp.bedrijf_id
            WHERE ubp.user_id = %s
            ORDER BY ubp.positie
        """, (user_id,))
        return [dict(r) for r in cur.fetchall()]


def sla_volgorde_op(user_id: int, bedrijf_ids: list):
    with get_cursor() as cur:
        cur.execute("DELETE FROM user_bedrijf_prioriteit WHERE user_id = %s", (user_id,))
        for pos, bid in enumerate(bedrijf_ids):
            cur.execute(
                "INSERT INTO user_bedrijf_prioriteit (user_id, bedrijf_id, positie) VALUES (%s, %s, %s) ON CONFLICT (user_id, bedrijf_id) DO UPDATE SET positie = %s",
                (user_id, bid, pos, pos)
            )


def get_volgend_bedrijf(item_id: int, gemeente: Optional[str], category: Optional[str], user_id: Optional[int] = None) -> Optional[dict]:
    """Volgt de door de gebruiker ingestelde volgorde; valt terug op categorie-match + naam."""
    with get_cursor() as cur:
        al_aangeboden = set()
        cur.execute("SELECT bedrijf_id FROM aanbiedingen WHERE item_id = %s", (item_id,))
        al_aangeboden = {r["bedrijf_id"] for r in cur.fetchall()}

        if user_id:
            cur.execute("""
                SELECT b.id, b.naam
                FROM user_bedrijf_prioriteit ubp
                JOIN bedrijven b ON b.id = ubp.bedrijf_id
                WHERE ubp.user_id = %s AND b.id != ALL(%s)
                ORDER BY ubp.positie
                LIMIT 1
            """, (user_id, list(al_aangeboden) or [0]))
            row = cur.fetchone()
            if row:
                return dict(row)

        # Fallback: categorie-match eerst, dan naam
        cur.execute("""
            SELECT b.id, b.naam
            FROM bedrijven b
            LEFT JOIN bedrijf_categorieen bc ON bc.bedrijf_id = b.id AND bc.category = %s
            WHERE b.gemeente = %s AND b.id != ALL(%s)
            ORDER BY (bc.category IS NOT NULL) DESC, b.naam
            LIMIT 1
        """, (category, gemeente, list(al_aangeboden) or [0]))
        row = cur.fetchone()
        return dict(row) if row else None


def get_all_users():
    with get_cursor() as cur:
        cur.execute("SELECT id, username, role, gemeente, bedrijf_id, created_at FROM users ORDER BY created_at")
        return [dict(r) for r in cur.fetchall()]


def create_user(username: str, password: str, role: str = "user",
                gemeente: str = "", bedrijf_id: Optional[int] = None) -> int:
    from auth import hash_password
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO users (username, password, role, gemeente, bedrijf_id, organisatie, created_at) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (username, hash_password(password), role, gemeente, bedrijf_id, username, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        return cur.fetchone()["id"]


def delete_user(user_id: int):
    with get_cursor() as cur:
        cur.execute("DELETE FROM users WHERE id = %s", (user_id,))


def update_user_role(user_id: int, role: str, bedrijf_id: Optional[int] = None):
    with get_cursor() as cur:
        cur.execute(
            "UPDATE users SET role = %s, bedrijf_id = %s WHERE id = %s",
            (role, bedrijf_id, user_id)
        )


def update_user_password(user_id: int, new_password: str):
    from auth import hash_password
    with get_cursor() as cur:
        cur.execute("UPDATE users SET password = %s WHERE id = %s", (hash_password(new_password), user_id))


def get_user_by_id_full(user_id: int):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def get_user_by_email(email: str):
    with get_cursor() as cur:
        e = email.lower().strip()
        cur.execute("SELECT * FROM users WHERE LOWER(email) = %s OR LOWER(username) = %s", (e, e))
        row = cur.fetchone()
        return dict(row) if row else None


def create_reset_token(user_id: int, token: str, expires_at):
    with get_cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (user_id,))
        cur.execute(
            "INSERT INTO password_reset_tokens (token, user_id, expires_at) VALUES (%s, %s, %s)",
            (token, user_id, expires_at),
        )


def get_reset_token(token: str):
    with get_cursor() as cur:
        cur.execute(
            "SELECT * FROM password_reset_tokens WHERE token = %s AND expires_at > NOW()",
            (token,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def delete_reset_token(token: str):
    with get_cursor() as cur:
        cur.execute("DELETE FROM password_reset_tokens WHERE token = %s", (token,))


def get_gemeenten():
    with get_cursor() as cur:
        cur.execute(
            "SELECT DISTINCT gemeente FROM users WHERE gemeente IS NOT NULL AND gemeente != '' ORDER BY gemeente"
        )
        return [r["gemeente"] for r in cur.fetchall()]


def get_gemeente_stats():
    """Overzicht per gemeente op basis van items."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT
                gemeente,
                COUNT(*) as item_count,
                COALESCE(SUM(gewicht_kg), 0) as totaal_kg,
                COUNT(DISTINCT uploaded_by) as user_count
            FROM items
            WHERE gemeente IS NOT NULL AND gemeente != ''
            GROUP BY gemeente
            ORDER BY totaal_kg DESC
        """)
        return [dict(r) for r in cur.fetchall()]


# ── Items ─────────────────────────────────────────────────────────────────────

def insert_item(photo_url: Optional[str], ai_label: Optional[str],
                ai_detail: Optional[str], gewicht_kg: Optional[float] = None,
                gemeente: Optional[str] = None,
                geaccepteerd: Optional[bool] = None,
                uploaded_by: Optional[int] = None) -> int:
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO items (timestamp, photo_url, ai_label, ai_detail, gewicht_kg, gemeente, geaccepteerd, uploaded_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (datetime.now(timezone.utc).isoformat(timespec="seconds"), photo_url, ai_label, ai_detail, gewicht_kg, gemeente, geaccepteerd, uploaded_by),
        )
        return cur.fetchone()["id"]


def update_item(item_id: int, manual_note: Optional[str], category: Optional[str]):
    with get_cursor() as cur:
        cur.execute(
            "UPDATE items SET manual_note = %s, category = %s WHERE id = %s",
            (manual_note, category, item_id),
        )


def get_items(limit: int = 50, offset: int = 0, gemeente: Optional[str] = None, user_id: Optional[int] = None, gemeenten: Optional[list] = None, own_user_id: Optional[int] = None, all_aanbiedingen: bool = False):
    with get_cursor() as cur:
        base = "SELECT id, timestamp, photo_url, ai_label, ai_detail, gewicht_kg, manual_note, category, gemeente, geaccepteerd, uploaded_by FROM items"
        if gemeenten:
            if own_user_id:
                cur.execute(
                    f"{base} WHERE (gemeente = ANY(%s) OR uploaded_by = %s) ORDER BY timestamp DESC LIMIT %s OFFSET %s",
                    (gemeenten, own_user_id, limit, offset),
                )
            else:
                cur.execute(
                    f"{base} WHERE gemeente = ANY(%s) ORDER BY timestamp DESC LIMIT %s OFFSET %s",
                    (gemeenten, limit, offset),
                )
        elif gemeente:
            if own_user_id:
                cur.execute(
                    f"{base} WHERE (gemeente = %s OR uploaded_by = %s) ORDER BY timestamp DESC LIMIT %s OFFSET %s",
                    (gemeente, own_user_id, limit, offset),
                )
            else:
                cur.execute(
                    f"{base} WHERE gemeente = %s ORDER BY timestamp DESC LIMIT %s OFFSET %s",
                    (gemeente, limit, offset),
                )
        else:
            cur.execute(
                f"{base} ORDER BY timestamp DESC LIMIT %s OFFSET %s",
                (limit, offset),
            )
        items = [dict(r) for r in cur.fetchall()]

        if all_aanbiedingen and items:
            item_ids = [i["id"] for i in items]
            placeholders = ",".join(["%s"] * len(item_ids))
            cur.execute(
                f"""SELECT DISTINCT ON (a.item_id) a.item_id, a.id as aanbieding_id,
                       a.status as aanbieding_status, a.created_at as aanbieding_created_at,
                       COALESCE(u.organisatie, u.username) as aangeboden_door_naam,
                       bdr.naam as bedrijf_naam
                    FROM aanbiedingen a
                    LEFT JOIN users u ON u.id = a.aangeboden_door
                    LEFT JOIN bedrijven bdr ON bdr.id = a.bedrijf_id
                    WHERE a.item_id IN ({placeholders})
                    ORDER BY a.item_id, a.created_at DESC""",
                item_ids,
            )
            aanbiedingen_map = {r["item_id"]: dict(r) for r in cur.fetchall()}
            for item in items:
                a = aanbiedingen_map.get(item["id"])
                if a:
                    item["aanbieding_id"] = a["aanbieding_id"]
                    item["aanbieding_status"] = a["aanbieding_status"]
                    item["aanbieding_created_at"] = a["aanbieding_created_at"]
                    item["aangeboden_door_naam"] = a["aangeboden_door_naam"]
                    item["bedrijf_naam"] = a["bedrijf_naam"]
        elif user_id and items:
            item_ids = [i["id"] for i in items]
            placeholders = ",".join(["%s"] * len(item_ids))
            cur.execute(
                f"SELECT DISTINCT ON (item_id) item_id, id as aanbieding_id, status as aanbieding_status, created_at as aanbieding_created_at, bedrijf_id FROM aanbiedingen WHERE item_id IN ({placeholders}) AND aangeboden_door = %s ORDER BY item_id, created_at DESC",
                item_ids + [user_id],
            )
            aanbiedingen_map = {r["item_id"]: dict(r) for r in cur.fetchall()}
            if aanbiedingen_map:
                bedrijf_ids = list({r["bedrijf_id"] for r in aanbiedingen_map.values() if r.get("bedrijf_id")})
                bedrijven_map = {}
                if bedrijf_ids:
                    ph = ",".join(["%s"] * len(bedrijf_ids))
                    cur.execute(f"SELECT id, naam FROM bedrijven WHERE id IN ({ph})", bedrijf_ids)
                    bedrijven_map = {r["id"]: r["naam"] for r in cur.fetchall()}
                aanbieding_ids = [r["aanbieding_id"] for r in aanbiedingen_map.values()]
                bericht_map = {}
                if aanbieding_ids:
                    ph = ",".join(["%s"] * len(aanbieding_ids))
                    cur.execute(
                        f"SELECT aanbieding_id, COUNT(*) as bericht_count, MAX(created_at) as last_bericht_at FROM berichten WHERE aanbieding_id IN ({ph}) GROUP BY aanbieding_id",
                        aanbieding_ids,
                    )
                    bericht_map = {r["aanbieding_id"]: dict(r) for r in cur.fetchall()}
                for item in items:
                    a = aanbiedingen_map.get(item["id"])
                    if a:
                        item["aanbieding_id"] = a["aanbieding_id"]
                        item["aanbieding_status"] = a["aanbieding_status"]
                        item["aanbieding_created_at"] = a["aanbieding_created_at"]
                        item["bedrijf_naam"] = bedrijven_map.get(a["bedrijf_id"])
                        b = bericht_map.get(a["aanbieding_id"])
                        if b:
                            item["bericht_count"] = b["bericht_count"]
                            item["last_bericht_at"] = b["last_bericht_at"]

        return items


def get_item(item_id: int, include_photo: bool = False):
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, timestamp, photo_url, ai_label, ai_detail, gewicht_kg, manual_note, category, gemeente, geaccepteerd FROM items WHERE id = %s",
            (item_id,),
        )
        row = cur.fetchone()
        return dict(row) if row else None


def delete_item(item_id: int):
    with get_cursor() as cur:
        cur.execute("DELETE FROM items WHERE id = %s", (item_id,))


def get_stats(gemeente: Optional[str] = None, user_id: Optional[int] = None, gemeenten: Optional[list] = None):
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d") + "%"
    filters = []
    params  = []
    if gemeenten:
        filters.append("gemeente = ANY(%s)"); params.append(gemeenten)
    elif gemeente:
        filters.append("gemeente = %s"); params.append(gemeente)
    if user_id:
        filters.append("uploaded_by = %s"); params.append(user_id)
    where = ("WHERE " + " AND ".join(filters)) if filters else ""
    with get_cursor() as cur:
        cur.execute(f"SELECT COUNT(*) as n FROM items {where}", params)
        total = cur.fetchone()["n"]
        cur.execute(f"SELECT COUNT(*) as n FROM items {where} {'AND' if where else 'WHERE'} timestamp LIKE %s", params + [today_str])
        today = cur.fetchone()["n"]
        cur.execute(f"SELECT COALESCE(SUM(gewicht_kg), 0) as kg FROM items {where}", params)
        totaal_kg = cur.fetchone()["kg"]
        cur.execute(f"SELECT category, COUNT(*) as count, COALESCE(SUM(gewicht_kg), 0) as kg FROM items {where} {'AND' if where else 'WHERE'} category IS NOT NULL GROUP BY category ORDER BY count DESC", params)
        categories = [dict(r) for r in cur.fetchall()]
        return {
            "total": total,
            "today": today,
            "totaal_kg": round(float(totaal_kg), 1),
            "categories": categories,
        }


def get_chart_data(days: int = 30, gemeente: Optional[str] = None, gemeenten: Optional[list] = None):
    with get_cursor() as cur:
        interval = f"{days} days"
        if gemeenten:
            cur.execute("""
                SELECT LEFT(timestamp, 10) as dag,
                       COUNT(*) as aantal,
                       COALESCE(SUM(gewicht_kg), 0) as kg
                FROM items
                WHERE gemeente = ANY(%s) AND timestamp >= (CURRENT_DATE - INTERVAL %s)::text
                GROUP BY dag ORDER BY dag
            """, (gemeenten, interval))
        elif gemeente:
            cur.execute("""
                SELECT LEFT(timestamp, 10) as dag,
                       COUNT(*) as aantal,
                       COALESCE(SUM(gewicht_kg), 0) as kg
                FROM items
                WHERE gemeente = %s AND timestamp >= (CURRENT_DATE - INTERVAL %s)::text
                GROUP BY dag ORDER BY dag
            """, (gemeente, interval))
        else:
            cur.execute("""
                SELECT LEFT(timestamp, 10) as dag,
                       COUNT(*) as aantal,
                       COALESCE(SUM(gewicht_kg), 0) as kg
                FROM items
                WHERE timestamp >= (CURRENT_DATE - INTERVAL %s)::text
                GROUP BY dag ORDER BY dag
            """, (interval,))
        return [dict(r) for r in cur.fetchall()]


# ── Bedrijven ─────────────────────────────────────────────────────────────────

def get_bedrijven(gemeente: Optional[str] = None, gemeenten: Optional[list] = None):
    with get_cursor() as cur:
        if gemeenten:
            cur.execute("""
                SELECT b.id, b.naam, b.gemeente, b.contactpersoon, b.email, b.telefoon,
                       COALESCE(array_agg(bc.category ORDER BY bc.category) FILTER (WHERE bc.category IS NOT NULL), '{}') as categorieen
                FROM bedrijven b
                LEFT JOIN bedrijf_categorieen bc ON bc.bedrijf_id = b.id
                WHERE b.gemeente = ANY(%s)
                GROUP BY b.id ORDER BY b.naam
            """, (gemeenten,))
        elif gemeente:
            cur.execute("""
                SELECT b.id, b.naam, b.gemeente, b.contactpersoon, b.email, b.telefoon,
                       COALESCE(array_agg(bc.category ORDER BY bc.category) FILTER (WHERE bc.category IS NOT NULL), '{}') as categorieen
                FROM bedrijven b
                LEFT JOIN bedrijf_categorieen bc ON bc.bedrijf_id = b.id
                WHERE b.gemeente = %s
                GROUP BY b.id ORDER BY b.naam
            """, (gemeente,))
        else:
            cur.execute("""
                SELECT b.id, b.naam, b.gemeente, b.contactpersoon, b.email, b.telefoon,
                       COALESCE(array_agg(bc.category ORDER BY bc.category) FILTER (WHERE bc.category IS NOT NULL), '{}') as categorieen
                FROM bedrijven b
                LEFT JOIN bedrijf_categorieen bc ON bc.bedrijf_id = b.id
                GROUP BY b.id ORDER BY b.gemeente, b.naam
            """)
        return [dict(r) for r in cur.fetchall()]


def get_netwerk_data(gemeente: Optional[str] = None) -> dict:
    with get_cursor() as cur:
        # Filter op gemeente van de items, niet het bedrijf
        item_where = "WHERE i.gemeente = %s" if gemeente else ""
        item_params = (gemeente,) if gemeente else ()

        # Bedrijven die gereageerd hebben op items uit deze gemeente
        niet_nodig_filter = "AND a.status != 'niet_nodig'"
        cur.execute(f"""
            SELECT b.id, b.naam, b.gemeente,
                   COUNT(a.id) as aanbieding_count,
                   COALESCE(array_agg(bc.category ORDER BY bc.category)
                     FILTER (WHERE bc.category IS NOT NULL), '{{}}') as categorieen
            FROM bedrijven b
            JOIN aanbiedingen a ON a.bedrijf_id = b.id
            JOIN items i ON i.id = a.item_id
            LEFT JOIN bedrijf_categorieen bc ON bc.bedrijf_id = b.id
            {item_where + (" AND" if item_where else "WHERE") + " a.status != 'niet_nodig'"}
            GROUP BY b.id ORDER BY aanbieding_count DESC
        """, item_params)
        bedrijven = [dict(r) for r in cur.fetchall()]

        # Aanbiedingen per categorie per bedrijf (exclusief niet_nodig)
        cur.execute(f"""
            SELECT a.bedrijf_id, i.category, COUNT(*) as count
            FROM aanbiedingen a
            JOIN items i ON i.id = a.item_id
            {"WHERE i.gemeente = %s AND" if gemeente else "WHERE"} i.category IS NOT NULL
            AND a.status != 'niet_nodig'
            GROUP BY a.bedrijf_id, i.category
        """, item_params)
        cat_per_bedrijf = {}
        for r in cur.fetchall():
            cat_per_bedrijf.setdefault(r["bedrijf_id"], []).append({"category": r["category"], "count": r["count"]})

        for b in bedrijven:
            cats = cat_per_bedrijf.get(b["id"], [])
            b["dominant_category"] = cats[0]["category"] if cats else None
            b["categorie_counts"] = cats

        return {
            "gemeente": gemeente or "Alle gemeenten",
            "bedrijven": bedrijven,
        }


def create_bedrijf(naam: str, gemeente: str, contactpersoon: str = "",
                   email: str = "", telefoon: str = "", categorieen: list = []) -> int:
    import uuid
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO bedrijven (naam, gemeente, contactpersoon, email, telefoon, created_at, meld_token) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (naam, gemeente, contactpersoon, email, telefoon, datetime.now(timezone.utc).isoformat(timespec="seconds"), str(uuid.uuid4())),
        )
        bedrijf_id = cur.fetchone()["id"]
        for cat in categorieen:
            cur.execute("INSERT INTO bedrijf_categorieen (bedrijf_id, category) VALUES (%s, %s) ON CONFLICT DO NOTHING", (bedrijf_id, cat))
        return bedrijf_id


def update_bedrijf(bedrijf_id: int, naam: str, contactpersoon: str = "",
                   email: str = "", telefoon: str = "", categorieen: list = []):
    with get_cursor() as cur:
        cur.execute(
            "UPDATE bedrijven SET naam=%s, contactpersoon=%s, email=%s, telefoon=%s WHERE id=%s",
            (naam, contactpersoon, email, telefoon, bedrijf_id),
        )
        cur.execute("DELETE FROM bedrijf_categorieen WHERE bedrijf_id = %s", (bedrijf_id,))
        for cat in categorieen:
            cur.execute("INSERT INTO bedrijf_categorieen (bedrijf_id, category) VALUES (%s, %s) ON CONFLICT DO NOTHING", (bedrijf_id, cat))


def delete_bedrijf(bedrijf_id: int):
    with get_cursor() as cur:
        cur.execute("DELETE FROM bedrijven WHERE id = %s", (bedrijf_id,))


def get_bedrijf_by_token(token: str):
    with get_cursor() as cur:
        cur.execute("SELECT * FROM bedrijven WHERE meld_token = %s", (token,))
        row = cur.fetchone()
        return dict(row) if row else None


def create_aanbieding(item_id: int, bedrijf_id: int, user_id: int = None) -> int:
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO aanbiedingen (item_id, bedrijf_id, status, created_at, aangeboden_door) VALUES (%s, %s, 'open', %s, %s) RETURNING id",
            (item_id, bedrijf_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), user_id),
        )
        return cur.fetchone()["id"]


def get_aanbiedingen_voor_item(item_id: int):
    with get_cursor() as cur:
        cur.execute("""
            SELECT a.id, a.status, a.created_at, a.updated_at,
                   b.naam as bedrijf_naam,
                   u.username as aangeboden_door_naam,
                   COALESCE(u.organisatie, u.username) as aanbieder_naam
            FROM aanbiedingen a
            JOIN bedrijven b ON b.id = a.bedrijf_id
            LEFT JOIN users u ON u.id = a.aangeboden_door
            WHERE a.item_id = %s
            ORDER BY a.created_at DESC
        """, (item_id,))
        return [dict(r) for r in cur.fetchall()]


def get_aanbiedingen_voor_bedrijf(bedrijf_id: int):
    with get_cursor() as cur:
        cur.execute("""
            SELECT a.id, a.status, a.created_at, a.updated_at,
                   i.ai_label, i.ai_detail, i.gewicht_kg, i.category, i.photo_url, i.gemeente
            FROM aanbiedingen a
            JOIN items i ON i.id = a.item_id
            WHERE a.bedrijf_id = %s
            ORDER BY a.created_at DESC
        """, (bedrijf_id,))
        return [dict(r) for r in cur.fetchall()]


def get_aanbiedingen_voor_beheer(gemeente: Optional[str] = None):
    with get_cursor() as cur:
        if gemeente:
            cur.execute("""
                SELECT a.id, a.status, a.created_at, a.updated_at,
                       b.naam as bedrijf_naam, b.meld_token,
                       i.ai_label, i.gewicht_kg, i.category, i.gemeente
                FROM aanbiedingen a
                JOIN bedrijven b ON b.id = a.bedrijf_id
                JOIN items i ON i.id = a.item_id
                WHERE b.gemeente = %s
                ORDER BY a.created_at DESC
            """, (gemeente,))
        else:
            cur.execute("""
                SELECT a.id, a.status, a.created_at, a.updated_at,
                       b.naam as bedrijf_naam, b.meld_token,
                       i.ai_label, i.gewicht_kg, i.category, i.gemeente
                FROM aanbiedingen a
                JOIN bedrijven b ON b.id = a.bedrijf_id
                JOIN items i ON i.id = a.item_id
                ORDER BY a.created_at DESC
            """)
        return [dict(r) for r in cur.fetchall()]


def update_aanbieding_status(aanbieding_id: int, status: str):
    with get_cursor() as cur:
        cur.execute(
            "UPDATE aanbiedingen SET status = %s, updated_at = %s WHERE id = %s",
            (status, datetime.now(timezone.utc).isoformat(timespec="seconds"), aanbieding_id),
        )


def get_bedrijven_for_item(gemeente: Optional[str], category: str, gemeenten: Optional[list] = None):
    """Geeft bedrijven terug die deze categorie afnemen, optioneel gefilterd op gemeente."""
    with get_cursor() as cur:
        if gemeenten:
            cur.execute("""
                SELECT b.id, b.naam, b.contactpersoon, b.email, b.telefoon
                FROM bedrijven b
                JOIN bedrijf_categorieen bc ON bc.bedrijf_id = b.id
                WHERE b.gemeente = ANY(%s) AND bc.category = %s
                ORDER BY b.naam
            """, (gemeenten, category))
        elif gemeente:
            cur.execute("""
                SELECT b.id, b.naam, b.contactpersoon, b.email, b.telefoon
                FROM bedrijven b
                JOIN bedrijf_categorieen bc ON bc.bedrijf_id = b.id
                WHERE b.gemeente = %s AND bc.category = %s
                ORDER BY b.naam
            """, (gemeente, category))
        else:
            cur.execute("""
                SELECT b.id, b.naam, b.contactpersoon, b.email, b.telefoon
                FROM bedrijven b
                JOIN bedrijf_categorieen bc ON bc.bedrijf_id = b.id
                WHERE bc.category = %s
                ORDER BY b.naam
            """, (category,))
        return [dict(r) for r in cur.fetchall()]


# ── Inzamellijst ──────────────────────────────────────────────────────────────

def get_inzamellijst(gemeente: str) -> list:
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, product FROM inzamellijst WHERE gemeente = %s ORDER BY product",
            (gemeente,)
        )
        return [dict(r) for r in cur.fetchall()]


def get_inzamellijst_alle() -> list:
    """Geeft alle unieke producten uit alle gemeenten — fallback als gemeente onbekend is."""
    with get_cursor() as cur:
        cur.execute(
            "SELECT DISTINCT product FROM inzamellijst ORDER BY product"
        )
        return [dict(r) for r in cur.fetchall()]


def add_to_inzamellijst(gemeente: str, product: str) -> int:
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO inzamellijst (gemeente, product, created_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING RETURNING id",
            (gemeente, product.strip(), datetime.now(timezone.utc).isoformat(timespec="seconds"))
        )
        row = cur.fetchone()
        return row["id"] if row else None


def remove_from_inzamellijst(entry_id: int, gemeente: str):
    with get_cursor() as cur:
        cur.execute(
            "DELETE FROM inzamellijst WHERE id = %s AND gemeente = %s",
            (entry_id, gemeente)
        )


def set_inzamellijst(gemeente: str, products: list):
    """Vervang de volledige inzamellijst voor een gemeente in één keer."""
    with get_cursor() as cur:
        cur.execute("DELETE FROM inzamellijst WHERE gemeente = %s", (gemeente,))
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for product in products:
            p = product.strip()
            if p:
                cur.execute(
                    "INSERT INTO inzamellijst (gemeente, product, created_at) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (gemeente, p, now)
                )


# ── Push subscriptions ────────────────────────────────────────────────────────

def save_push_subscription(subscription: str, bedrijf_id: int = None, user_id: int = None):
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO push_subscriptions (bedrijf_id, user_id, subscription, created_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (subscription) DO UPDATE SET bedrijf_id = EXCLUDED.bedrijf_id, user_id = EXCLUDED.user_id""",
            (bedrijf_id, user_id, subscription, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )


def get_push_subscriptions_for_bedrijf(bedrijf_id: int) -> list:
    """Haalt subscriptions op voor een bedrijf: via bedrijf_id directe koppeling én via users.bedrijf_id."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT ps.id, ps.subscription FROM push_subscriptions ps
            WHERE ps.bedrijf_id = %s
            UNION
            SELECT ps.id, ps.subscription FROM push_subscriptions ps
            JOIN users u ON u.id = ps.user_id
            WHERE u.bedrijf_id = %s
        """, (bedrijf_id, bedrijf_id))
        return [dict(r) for r in cur.fetchall()]


def delete_push_subscription(subscription: str):
    with get_cursor() as cur:
        cur.execute("DELETE FROM push_subscriptions WHERE subscription = %s", (subscription,))


def get_items_voor_bedrijf(bedrijf_id: int) -> list:
    """Geeft items met aanbieding-info terug voor dit bedrijf."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT i.id, i.timestamp, i.photo_url, i.ai_label, i.ai_detail,
                   i.gewicht_kg, i.manual_note, i.category, i.gemeente, i.geaccepteerd,
                   a.id as aanbieding_id, a.status as aanbieding_status,
                   COALESCE(u.organisatie, u.username) as aangeboden_door_naam, a.aangeboden_door as aangeboden_door_id,
                   (SELECT COUNT(*) FROM berichten b WHERE b.aanbieding_id = a.id) as bericht_count,
                   (SELECT MAX(created_at) FROM berichten b WHERE b.aanbieding_id = a.id) as last_bericht_at
            FROM items i
            JOIN aanbiedingen a ON a.item_id = i.id AND a.bedrijf_id = %s
            LEFT JOIN users u ON u.id = a.aangeboden_door
            ORDER BY i.timestamp DESC
        """, (bedrijf_id,))
        return [dict(r) for r in cur.fetchall()]


def get_push_subscriptions_voor_user(user_id: int) -> list:
    with get_cursor() as cur:
        cur.execute(
            "SELECT id, subscription FROM push_subscriptions WHERE user_id = %s",
            (user_id,)
        )
        return [dict(r) for r in cur.fetchall()]


def get_aanbiedingen_door_user(user_id: int) -> list:
    with get_cursor() as cur:
        cur.execute("""
            SELECT a.id as aanbieding_id, a.status as aanbieding_status,
                   a.created_at as aanbieding_created_at,
                   b.naam as bedrijf_naam,
                   i.id, i.timestamp, i.photo_url, i.ai_label, i.ai_detail,
                   i.gewicht_kg, i.manual_note, i.category, i.gemeente
            FROM aanbiedingen a
            JOIN items i ON i.id = a.item_id
            JOIN bedrijven b ON b.id = a.bedrijf_id
            WHERE a.aangeboden_door = %s
            ORDER BY a.created_at DESC
        """, (user_id,))
        return [dict(r) for r in cur.fetchall()]


# ── Berichten ────────────────────────────────────────────────────────────────

def get_berichten(aanbieding_id: int) -> list:
    with get_cursor() as cur:
        cur.execute("""
            SELECT b.id, b.tekst, b.created_at,
                   u.id as user_id,
                   COALESCE(u.organisatie, u.username) as naam
            FROM berichten b
            JOIN users u ON u.id = b.user_id
            WHERE b.aanbieding_id = %s
            ORDER BY b.created_at ASC
        """, (aanbieding_id,))
        return [dict(r) for r in cur.fetchall()]


def stuur_bericht(aanbieding_id: int, user_id: int, tekst: str) -> dict:
    with get_cursor() as cur:
        cur.execute(
            "INSERT INTO berichten (aanbieding_id, user_id, tekst, created_at) VALUES (%s, %s, %s, %s) RETURNING id, created_at",
            (aanbieding_id, user_id, tekst.strip(), datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        row = cur.fetchone()
        return {"id": row["id"], "created_at": row["created_at"]}


def get_aanbieding_deelnemers(aanbieding_id: int) -> dict:
    """Geeft aangeboden_door user_id en bedrijf_id terug voor pushnotificaties."""
    with get_cursor() as cur:
        cur.execute("""
            SELECT a.aangeboden_door, a.bedrijf_id, a.item_id,
                   i.ai_label
            FROM aanbiedingen a
            JOIN items i ON i.id = a.item_id
            WHERE a.id = %s
        """, (aanbieding_id,))
        row = cur.fetchone()
        return dict(row) if row else {}


def export_csv(gemeente: Optional[str] = None) -> str:
    import csv, io
    with get_cursor() as cur:
        if gemeente:
            cur.execute("""
                SELECT id, timestamp, gemeente, ai_label, ai_detail, gewicht_kg, manual_note, category
                FROM items WHERE gemeente = %s ORDER BY timestamp DESC
            """, (gemeente,))
        else:
            cur.execute("""
                SELECT id, timestamp, gemeente, ai_label, ai_detail, gewicht_kg, manual_note, category
                FROM items ORDER BY timestamp DESC
            """)
        rows = cur.fetchall()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["ID", "Tijdstip", "Gemeente", "Label", "Beschrijving", "Gewicht (kg)", "Opmerking", "Categorie"])
    for r in rows:
        writer.writerow([r["id"], r["timestamp"], r["gemeente"] or "",
                         r["ai_label"] or "", r["ai_detail"] or "",
                         r["gewicht_kg"] or "", r["manual_note"] or "", r["category"] or ""])
    return buf.getvalue()
