import psycopg2, psycopg2.extras, os, re, urllib.parse as up, sys
import firebase_admin
from firebase_admin import credentials, storage

if not firebase_admin._apps:
    cred = credentials.Certificate('serviceaccount.json')
    firebase_admin.initialize_app(cred, {'storageBucket': 'database-e5575.firebasestorage.app'})

bucket = storage.bucket()
conn = psycopg2.connect(os.environ['DATABASE_URL'], sslmode='require')
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute("SELECT id, photo_url FROM items WHERE gemeente='Almere' AND photo_url IS NOT NULL AND photo_url_thumb IS NULL")
items = cur.fetchall()
print(f'{len(items)} items te verwerken', flush=True)

updated = errors = 0
for item in items:
    url = item['photo_url']
    m = re.match(r'https://firebasestorage\.googleapis\.com/v0/b/([^/]+)/o/([^?]+)', url)
    if not m: continue
    bucket_name, encoded_path = m.group(1), m.group(2)
    path = up.unquote(encoded_path)
    if not path.endswith('.jpg'): continue
    thumb_path = path[:-4] + '_1024x1024.jpg'
    try:
        blob = bucket.blob(thumb_path)
        blob.reload()
        token = (blob.metadata or {}).get('firebaseStorageDownloadTokens', '')
        thumb_url = f'https://firebasestorage.googleapis.com/v0/b/{bucket_name}/o/{up.quote(thumb_path, safe="")}?alt=media&token={token}' if token else url
        cur.execute('UPDATE items SET photo_url_thumb = %s WHERE id = %s', (thumb_url, item['id']))
        updated += 1
        if updated % 20 == 0:
            conn.commit()
            print(f'  {updated} gedaan...', flush=True)
    except Exception as e:
        errors += 1

conn.commit()
cur.close()
conn.close()
print(f'Klaar: {updated} bijgewerkt, {errors} fouten')
