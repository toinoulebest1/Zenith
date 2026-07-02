import sys
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
import json
import difflib # Nécessaire pour le matching Chosic
import subprocess # Nécessaire pour le transcodage FLAC

# Configuration des chemins pour imports locaux
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)
PROJECT_ROOT = os.path.abspath(os.path.join(current_dir, '..'))

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

# Imports locaux
try:
    from qobuz_api import QobuzClient, get_app_credentials
    from lyrics_search import LyricsSearcher 
except ImportError:
    from .qobuz_api import QobuzClient, get_app_credentials
    from .lyrics_search import LyricsSearcher

import logging
import random
import threading
import base64
import tempfile
import requests
import requests.adapters
import re
import urllib.parse
import hashlib
import string
import unicodedata 
from ytmusicapi import YTMusic
from deep_translator import GoogleTranslator
from io import BytesIO
import struct
from struct import pack, unpack

# AES for CENC decryption (Amazon Music) - imported after logger init below
CRYPTO_AVAILABLE = False

# --- PATCH GLOBAL CONNEXIONS ---
requests.adapters.DEFAULT_POOLSIZE = 100
requests.adapters.DEFAULT_RETRIES = 3

# Tentative d'import pour rapidfuzz
try:
    from rapidfuzz import fuzz
    FUZZ_AVAILABLE = True
except ImportError:
    FUZZ_AVAILABLE = False

# --- CONFIGURATION (chargée depuis .env ou variables d'environnement) ---
# Identifiants Qobuz officiels. Le token est lié à l'app_id : app_id + secret + token
# doivent être cohérents. Surcharge possible via variables d'environnement.
USER_ID      = os.getenv('QOBUZ_USER_ID', '3317884')
TOKEN        = os.getenv('QOBUZ_TOKEN', 'N-6BS7eXzhLLp2hyeyIpL7kb-1XxiKV3W-wRVHPFzT3KaZCXVMv8-66tCb4y40-NwwGkGz10lqjlkLyo3rS6iA')
APP_ID       = os.getenv('QOBUZ_APP_ID', '798273057')
QOBUZ_SECRET = os.getenv('QOBUZ_SECRET', 'abb21364945c0583309667d13ca3d93a')

TIDAL_HUND_BASE = "https://api.monochrome.tf"

TIDAL_HUND_BASE = "https://api.monochrome.tf"

# API Tidal hifi-api (recherche + lecture). Instance auto-hébergée (HTTP → appelée
# uniquement côté serveur ; l'audio est proxifié, le navigateur n'y touche jamais).
TIDAL_HIFI_BASE = os.getenv('TIDAL_HIFI_BASE', "http://rgoggwgg0ws4ks0gogw8o0s8.46.224.72.133.sslip.io")
TIDAL_HIFI_KEY = os.getenv('TIDAL_HIFI_KEY', "")

# MODE DEBUG : Qobuz en pause, la recherche ne renvoie QUE des titres Tidal (pour
# isoler/analyser la lecture Tidal). Remettre à False pour réactiver toutes les sources.
TIDAL_ONLY_MODE = os.getenv('TIDAL_ONLY_MODE', '1') == '1'

# Credentials Tidal chargés uniquement depuis les variables d'environnement ou token.json
FALLBACK_TIDAL_CREDENTIALS = {
    "client_ID":      os.getenv('CLIENT_ID', ''),
    "client_secret":  os.getenv('CLIENT_SECRET', ''),
    "refresh_token":  os.getenv('REFRESH_TOKEN', '')
}

# --- AMAZON MUSIC API ---
AMAZON_MUSIC_API_BASE = "https://t2tunes.site/api/amazon-music"

# --- QOBUZ ALTERNATIVE API (source principale : recherche + audio) ---
# Renvoie une URL CDN directe (redirigeable) : compatible Vercel serverless.
QOBUZ_ALT_API_BASE = "https://qobuz.kennyy.com.br"
QOBUZ_ENABLED = True   # Qobuz : recherche + audio
QOBUZ_OFFICIAL_ENABLED = True  # API Qobuz officielle (token+secret) en priorité ; kennyy = repli

# --- SQUID.WTF (Amazon Music) API ---
# EN PAUSE : incompatible Vercel (déchiffrement ffmpeg + flux >4.5 Mo).
# Code conservé pour réactivation sur un hôte non-serverless.
SQUID_ENABLED = False
SQUID_API_BASE = "https://amz.squid.wtf"
SQUID_COUNTRY = "US"
SQUID_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

# --- STREAM URL CACHE (évite d'appeler l'alt API à chaque lecture) ---
_stream_url_cache: dict = {}  # {track_id: {'url': str, 'ts': float}}
STREAM_CACHE_TTL = 25 * 60   # 25 minutes (URLs Qobuz expirent vers 30 min)

# --- INIT FASTAPI ---
app = FastAPI(title="Zenith API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ZenithAPI")

# Deferred import of Crypto (needs logger)
try:
    from Crypto.Cipher import AES
    from Crypto.Util import Counter as CryptoCounter
    CRYPTO_AVAILABLE = True
except Exception as e:
    logger.warning(f"pycryptodome not available: {e}")

lyrics_engine = LyricsSearcher()
yt = YTMusic()

# --- GESTION TOKEN TIDAL OFFICIEL ---
class TidalAuthManager:
    def __init__(self):
        self.access_token = None
        self.expires_at = 0
        # Valeurs initiales (Fallback)
        self.client_id = FALLBACK_TIDAL_CREDENTIALS["client_ID"]
        self.client_secret = FALLBACK_TIDAL_CREDENTIALS["client_secret"]
        self.refresh_token = FALLBACK_TIDAL_CREDENTIALS["refresh_token"]
        
        # Surcharge par ENV si dispo
        if os.getenv("CLIENT_ID"): self.client_id = os.getenv("CLIENT_ID")
        if os.getenv("CLIENT_SECRET"): self.client_secret = os.getenv("CLIENT_SECRET")
        if os.getenv("REFRESH_TOKEN"): self.refresh_token = os.getenv("REFRESH_TOKEN")
        
        # Surcharge par fichier si dispo
        self.load_from_file()
        
    def load_from_file(self):
        possible_paths = ['token.json', 'api/token.json', '../token.json']
        for path in possible_paths:
            full_path = os.path.join(PROJECT_ROOT, path) if '..' not in path else os.path.abspath(os.path.join(current_dir, '..', 'token.json'))
            if os.path.exists(path):
                try:
                    with open(path, 'r') as f:
                        data = json.load(f)
                        entry = None
                        if isinstance(data, list) and len(data) > 0:
                            entry = data[0]
                        elif isinstance(data, dict):
                            entry = data
                        
                        if entry:
                            if entry.get('refresh_token'): self.refresh_token = entry.get('refresh_token')
                            if entry.get('client_ID'): self.client_id = entry.get('client_ID')
                            if entry.get('client_secret'): self.client_secret = entry.get('client_secret')
                            logger.info(f"[TidalAuth] Loaded config from {path}")
                            return
                except Exception as e:
                    logger.error(f"[TidalAuth] Error reading {path}: {e}")

    def get_token(self):
        if self.access_token and time.time() < self.expires_at:
            return self.access_token
            
        if not self.refresh_token:
            logger.warning("[TidalAuth] No refresh token available.")
            return None

        logger.info(f"[TidalAuth] Refreshing Token (Client: {self.client_id[:4]}...)")
        try:
            r = requests.post(
                "https://auth.tidal.com/v1/oauth2/token",
                data={
                    "client_id": self.client_id,
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                    "scope": "r_usr+w_usr+w_sub",
                },
                auth=(self.client_id, self.client_secret),
                timeout=10
            )
            r.raise_for_status()
            data = r.json()
            self.access_token = data["access_token"]
            self.expires_at = time.time() + data.get("expires_in", 3600) - 60
            logger.info("[TidalAuth] Token refreshed successfully.")
            return self.access_token
        except Exception as e:
            logger.error(f"[TidalAuth] Error refreshing token: {e}")
            try: logger.error(f"[TidalAuth] Response: {r.text}")
            except: pass
            return None

tidal_auth = TidalAuthManager()

# --- CLIENT SQUID.WTF (Amazon Music) ---
class SquidClient:
    """
    Client pour amz.squid.wtf : résout le proof-of-work ALTCHA/PBKDF2,
    met en cache le token captcha (réutilisable), et expose search/track/stream.
    """
    def __init__(self):
        self.token = None
        self.token_ts = 0
        self.token_ttl = 8 * 60  # le token est réutilisable ; on le rafraîchit toutes les ~8 min
        self._lock = threading.Lock()
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": SQUID_UA,
            "Referer": f"{SQUID_API_BASE}/",
            "Origin": SQUID_API_BASE,
        })

    def _solve_pow(self, params):
        """Brute-force le counter du PoW PBKDF2-HMAC-SHA256."""
        nonce = bytes.fromhex(params['nonce'])
        salt = bytes.fromhex(params['salt'])
        kp = bytes.fromhex(params['keyPrefix'])
        cost = params['cost']
        klen = params['keyLength']
        counter = 0
        t0 = time.time()
        while counter < 10_000_000:
            dk = hashlib.pbkdf2_hmac('sha256', nonce + struct.pack('>I', counter), salt, cost, dklen=klen)
            if dk[:len(kp)] == kp:
                return counter, dk.hex(), (time.time() - t0) * 1000
            counter += 1
        raise RuntimeError("Squid PoW: aucune solution trouvée")

    def _new_token(self):
        r = self.session.get(f"{SQUID_API_BASE}/api/captcha/challenge", timeout=20)
        r.raise_for_status()
        ch = r.json()
        params = ch['parameters']
        counter, dk_hex, took = self._solve_pow(params)
        payload = {
            "challenge": {"parameters": params, "signature": ch['signature']},
            "solution": {"counter": counter, "derivedKey": dk_hex, "time": took},
        }
        b64 = base64.b64encode(json.dumps(payload).encode()).decode()
        rv = self.session.post(f"{SQUID_API_BASE}/api/captcha/verify",
                               json={"payload": b64}, timeout=20)
        rv.raise_for_status()
        token = rv.json().get('token')
        if not token:
            raise RuntimeError("Squid captcha: pas de token dans la réponse verify")
        logger.info(f"[Squid] Nouveau token captcha (counter={counter}, {round(took)}ms)")
        return token

    def get_token(self, force=False):
        with self._lock:
            if not force and self.token and (time.time() - self.token_ts) < self.token_ttl:
                return self.token
            self.token = self._new_token()
            self.token_ts = time.time()
            return self.token

    def _request(self, method, path, json_body=None, params=None, stream=False):
        """Requête authentifiée avec retry unique si le captcha est rejeté."""
        url = path if path.startswith('http') else f"{SQUID_API_BASE}{path}"
        for attempt in range(2):
            token = self.get_token(force=(attempt == 1))
            headers = {"x-captcha-token": token}
            if json_body is not None:
                headers["Content-Type"] = "application/json"
            resp = self.session.request(method, url, json=json_body, params=params,
                                        headers=headers, stream=stream, timeout=90)
            if resp.status_code in (401, 403) and attempt == 0:
                logger.info(f"[Squid] {path} -> {resp.status_code}, renouvellement du token")
                continue
            return resp
        return resp

    def search(self, query, limit=50):
        try:
            resp = self._request('POST', '/api/search',
                                 json_body={"query": query, "country": SQUID_COUNTRY})
            if resp.status_code != 200:
                logger.error(f"[Squid Search] Status {resp.status_code}: {resp.text[:200]}")
                return []
            data = resp.json()
            results = []
            for t in data.get('trackList', [])[:limit]:
                asin = t.get('asin')
                if not asin:
                    continue
                img = (t.get('album') or {}).get('image') or 'https://placehold.co/300x300/1a1a1a/666666?text=Music'
                results.append({
                    'id': asin,
                    'title': t.get('title', 'Titre Inconnu'),
                    'performer': {'name': t.get('artistName') or t.get('primaryArtistName') or 'Inconnu'},
                    'album': {
                        'title': (t.get('album') or {}).get('title', ''),
                        'image': {'large': img, 'small': img},
                    },
                    'duration': 0,  # non fourni par /api/search
                    'maximum_bit_depth': 16,
                    'source': 'qobuz',  # libellé conservé pour l'UX (prefetch HD, badges)
                })
            return results
        except Exception as e:
            logger.error(f"[Squid Search] Exception: {e}")
            return []

    def get_track_full(self, asin, tier='best'):
        """Renvoie (metadata, drm_key, stream_path, codec) ou None."""
        try:
            resp = self._request('POST', '/api/track',
                                 json_body={"asin": asin, "tier": tier, "country": SQUID_COUNTRY})
            if resp.status_code != 200:
                logger.error(f"[Squid Track] {asin} tier={tier} -> {resp.status_code}")
                return None
            d = resp.json()
            meta = d.get('metadata', {}) or {}
            key = (d.get('drm') or {}).get('key')
            stream = d.get('stream') or {}
            return meta, key, stream.get('url'), stream.get('codec')
        except Exception as e:
            logger.error(f"[Squid Track] Exception: {e}")
            return None

    def get_track_meta(self, asin):
        """Métadonnées seules, formatées en objet track Zenith."""
        res = self.get_track_full(asin)
        if not res:
            # repli : métadonnées sans tier (pas de clé requise)
            try:
                resp = self._request('POST', '/api/track',
                                     json_body={"asin": asin, "country": SQUID_COUNTRY})
                d = resp.json() if resp.status_code == 200 else {}
                meta = d.get('metadata', {}) or {}
            except Exception:
                meta = {}
        else:
            meta = res[0]
        if not meta:
            return None
        cover = meta.get('cover') or 'https://placehold.co/300x300/1a1a1a/666666?text=Music'
        return {
            'id': asin,
            'title': meta.get('title', 'Titre Inconnu'),
            'performer': {'name': meta.get('artist', 'Inconnu')},
            'album': {'title': meta.get('album', ''), 'image': {'large': cover, 'small': cover}},
            'duration': 0,
            'maximum_bit_depth': 16,
            'isrc': meta.get('isrc'),
            'date': meta.get('date') or meta.get('year'),
            'source': 'qobuz',
        }

    def fetch_encrypted_stream(self, asin):
        """
        Télécharge le flux chiffré + récupère la clé.
        Renvoie (encrypted_bytes, key_hex, codec) ou None.
        Essaie les tiers du meilleur au plus bas.
        """
        for tier in ('best', 'hd', 'standard'):
            res = self.get_track_full(asin, tier=tier)
            if not res:
                continue
            _meta, key, stream_path, codec = res
            if not key or not stream_path:
                continue
            try:
                resp = self._request('GET', stream_path, stream=False)
                if resp.status_code != 200:
                    logger.error(f"[Squid Stream] {asin} {stream_path} -> {resp.status_code}")
                    continue
                return resp.content, key, (codec or 'flac')
            except Exception as e:
                logger.error(f"[Squid Stream] Exception {asin} tier={tier}: {e}")
                continue
        return None


squid = SquidClient()

ASIN_RE = re.compile(r'^B[0-9A-Z]{9}$')  # ASIN Amazon : 'B' + 9 alphanum (≠ id Qobuz numérique)

def is_asin(track_id):
    return bool(track_id and ASIN_RE.match(str(track_id)))

# --- CLIENT QOBUZ ---
class TokenQobuzClient(QobuzClient):
    def __init__(self, app_id, token, secret=None, secrets=None):
        self.id = str(app_id)
        self.session = self._make_session()
        self.base = "https://www.qobuz.com/api.json/0.2/"
        self.uat = token
        self.session.headers.update({"X-User-Auth-Token": self.uat})
        if secret:
            # app_id + secret connus et cohérents avec le token → pas de scraping/test
            self.sec = secret
            self.secrets = {"fixed": secret}
        else:
            self.secrets = secrets or {}
            self.sec = None
            self.cfg_setup()

    def _make_session(self):
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Zenith Player (FastAPI)",
            "X-App-Id": self.id,
            "Content-Type": "application/json;charset=UTF-8"
        })
        return s

client = None
if QOBUZ_OFFICIAL_ENABLED:
    try:
        logger.info(f"Init Qobuz officiel (app_id {APP_ID})...")
        client = TokenQobuzClient(APP_ID, TOKEN, secret=QOBUZ_SECRET)
        logger.info("Qobuz officiel prêt.")
    except Exception as e:
        logger.error(f"Init Error Qobuz officiel: {e}")
else:
    logger.info("Qobuz via API alt (kennyy) — pas de client officiel requis.")

# --- UTILITAIRES ---
def clean_string(s):
    if not s: return ""
    s = str(s)
    s = unicodedata.normalize('NFD', s)
    s = s.encode('ascii', 'ignore').decode("utf-8")
    s = s.lower().strip()
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

def fix_qobuz_title(track):
    try:
        if 'version' in track and track['version']:
            ver = str(track['version']).strip()
            if ver.lower() not in ['album version', 'original version', 'standard version', 'remastered', 'remaster', 'single version']:
                if ver.lower() not in track['title'].lower():
                    track['title'] = f"{track['title']} ({ver})"
    except: pass
    return track

def get_hq_yt_image(url):
    if not url: return 'https://placehold.co/300x300/1a1a1a/666666?text=Music'
    if '=w' in url: return re.sub(r'=w\d+-h\d+', '=w1200-h1200', url)
    return re.sub(r'w\d+-h\d+(-l\d+)?', 'w1200-h1200-l100', url)

def extract_thumbnail_hd(track):
    thumbs = []
    for key in ["thumbnails", "thumbnail"]:
        if key in track and track[key]:
            data = track[key]
            if isinstance(data, list): thumbs = data; break
            elif isinstance(data, dict):
                if "thumbnails" in data: thumbs = data["thumbnails"]; break
                else: thumbs = [data]; break
            elif isinstance(data, str): return get_hq_yt_image(data)
    if not thumbs: return 'https://placehold.co/300x300/1a1a1a/666666?text=Music'
    try: thumbs.sort(key=lambda x: x.get("width", 0))
    except: pass 
    return get_hq_yt_image(thumbs[-1].get("url"))

def parse_duration(d):
    if not d: return 0
    if isinstance(d, (int, float)): return int(d)
    if isinstance(d, str) and ':' in d:
        parts = d.split(':')
        if len(parts) == 2: return int(parts[0]) * 60 + int(parts[1])
        elif len(parts) == 3: return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return 0

def ms_to_lrc(ms):
    try:
        s = float(ms) / 1000
        m = int(s // 60)
        rs = s % 60
        return f"[{m:02d}:{rs:05.2f}]"
    except:
        return "[00:00.00]"

def tidal_uuid_to_url(uuid):
    if not uuid: return 'https://placehold.co/300x300/1a1a1a/666666?text=Tidal'
    path = uuid.replace('-', '/')
    return f"https://resources.tidal.com/images/{path}/640x640.jpg"

# --- TIDAL OFFICIAL SEARCH & HUND AUDIO ---

def _tidal_headers():
    h = {'User-Agent': SQUID_UA, 'Accept': 'application/json'}
    if TIDAL_HIFI_KEY:
        h['X-API-Key'] = TIDAL_HIFI_KEY
    return h

def _tidal_track_obj(t):
    """Mappe un track Tidal (hifi-api) → objet 'tidal_hund' avec qualité (tags)."""
    if not t or not t.get('id') or not t.get('title'):
        return None
    tags = (t.get('mediaMetadata') or {}).get('tags') or []
    hires = ('HIRES_LOSSLESS' in tags) or ('MQA' in tags)
    bd = 24 if hires else 16
    sr = 96.0 if hires else 44.1  # approximation (Tidal n'expose pas le sr en recherche)
    alb = t.get('album') or {}
    return {
        'id': str(t['id']),
        'title': t.get('title'),
        'performer': {'name': (t.get('artist') or {}).get('name', 'Inconnu')},
        'album': {'title': alb.get('title'), 'image': {'large': tidal_uuid_to_url(alb.get('cover'))}},
        'duration': t.get('duration', 0),
        'isrc': t.get('isrc'),
        'maximum_bit_depth': bd,
        'maximum_sampling_rate': sr,
        'source': 'tidal_hund',
    }

def sync_search_tidal(query, limit=25):
    """Recherche Tidal via hifi-api → objets 'tidal_hund'. Lecture via /tidal_manifest."""
    try:
        r = requests.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/search/",
                         params={'s': query, 'limit': limit},
                         headers=_tidal_headers(), timeout=12)
        if r.status_code != 200:
            logger.warning(f"[Tidal Search] HTTP {r.status_code}")
            return []
        items = (r.json().get('data') or {}).get('items', []) or []
    except Exception as e:
        logger.error(f"[Tidal Search] {e}")
        return []
    out = []
    for t in items:
        obj = _tidal_track_obj(t)
        if obj:
            out.append(obj)
        if len(out) >= limit:
            break
    return out

def _tidal_resolve_one(title, artist):
    """Cherche un titre sur Tidal (par titre+artiste) → objet 'tidal_hund' ou None."""
    try:
        q = f"{title} {artist}".strip()
        r = requests.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/search/",
                         params={'s': q, 'limit': 1}, headers=_tidal_headers(), timeout=10)
        items = (r.json().get('data') or {}).get('items', []) or []
        return _tidal_track_obj(items[0]) if items else None
    except Exception:
        return None

def _tidal_native_radio(title, artist, limit=25):
    """Radio native Tidal (recommandations de la piste seed). Repli si YouTube échoue."""
    try:
        r = requests.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/search/",
                         params={'s': f"{title} {artist}".strip(), 'limit': 1},
                         headers=_tidal_headers(), timeout=12)
        items = (r.json().get('data') or {}).get('items', []) or []
        if not items:
            return []
        seed_id = items[0].get('id')
        rr = requests.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/recommendations/",
                          params={'id': seed_id}, headers=_tidal_headers(), timeout=20)
        recs = (rr.json().get('data') or {}).get('items', []) or []
        out = []
        for it in recs:
            t = it.get('track') or it
            if str(t.get('id')) == str(seed_id):
                continue
            obj = _tidal_track_obj(t)
            if obj:
                out.append(obj)
            if len(out) >= limit:
                break
        return out
    except Exception as e:
        logger.error(f"[Tidal Native Radio] {e}")
        return []

def sync_get_tidal_radio(title, artist, limit=25):
    """Radio : recommandations trouvées via YouTube (Automix), puis RÉSOLUES en titres
    Tidal (donc lecture 100 % Tidal). Repli sur les recommandations natives Tidal."""
    try:
        yt_tracks = sync_get_radio_queue(title, artist)  # YTMusic → titres + artistes
    except Exception as e:
        logger.warning(f"[Tidal Radio] YouTube seed KO: {e}")
        yt_tracks = []
    if yt_tracks:
        pairs = [(t.get('title'), (t.get('performer') or {}).get('name', '')) for t in yt_tracks[:limit]]
        results = [None] * len(pairs)
        with ThreadPoolExecutor(max_workers=8) as ex:
            futs = {ex.submit(_tidal_resolve_one, tt, aa): i for i, (tt, aa) in enumerate(pairs)}
            for f in futs:
                try:
                    results[futs[f]] = f.result()
                except Exception:
                    pass
        out, seen = [], set()
        for r in results:
            if r and r['id'] not in seen:
                out.append(r); seen.add(r['id'])
        if out:
            return out
    # Repli : recommandations natives Tidal
    return _tidal_native_radio(title, artist, limit)

def _sync_search_tidal_DISABLED(query, limit=50):
    return []
    # --- ORIGINAL TIDAL SEARCH CODE (PAUSED) ---
    # token = tidal_auth.get_token()
    # if not token:
    #     logger.error("[Tidal Search] Pas de token disponible. Vérifiez REFRESH_TOKEN ou token.json.")
    #     return []

    # logger.info(f"[Tidal Search] Query: {query}")
    # try:
    #     url = "https://api.tidal.com/v1/search/tracks"
    #     params = {
    #         "query": query,
    #         "limit": limit,
    #         "offset": 0,
    #         "countryCode": "US"
    #     }
    #     headers = {"Authorization": f"Bearer {token}"}
        
    #     res = requests.get(url, headers=headers, params=params, timeout=10)
    #     logger.info(f"[Tidal Search] Status: {res.status_code}")
        
    #     if res.status_code != 200:
    #         logger.error(f"[Tidal Search] Error Body: {res.text}")
    #         return []

    #     data = res.json()
    #     items = data.get('items', [])
    #     results = []
        
    #     for t in items:
    #         try:
    #             # Détection HiRes / 24 bits via les tags officiels
    #             bit_depth = 16
    #             tags = t.get('mediaMetadata', {}).get('tags', [])
    #             if "HIRES_LOSSLESS" in tags or "MQA" in tags:
    #                 bit_depth = 24
                
    #             track = {
    #                 'id': str(t['id']),
    #                 'title': t['title'],
    #                 'performer': {'name': (t.get('artist') or {}).get('name', 'Inconnu')},
    #                 'album': {
    #                     'title': (t.get('album') or {}).get('title'),
    #                     'image': {'large': tidal_uuid_to_url((t.get('album') or {}).get('cover'))}
    #                 },
    #                 'duration': t.get('duration', 0),
    #                 'maximum_bit_depth': bit_depth,
    #                 'source': 'tidal_hund',
    #                 'date': t.get('streamStartDate') # Extraction Date
    #             }
    #             results.append(track)
    #         except Exception as e:
    #             logger.error(f"[Tidal Search] Parse Error: {e}")
    #             continue
                
    #     logger.info(f"[Tidal Search] Found {len(results)} items")
    #     return results

    # except Exception as e:
    #     logger.error(f"[Tidal Search] Exception: {e}")
    #     return []

def sync_search_tidal_albums(query, limit=15):
    """DISABLED: Tidal album search is paused."""
    return []

def sync_get_tidal_album(album_id):
    """DISABLED: Tidal album details is paused."""
    return None

def get_tidal_stream_manifest(track_id):
    """DISABLED: Tidal stream manifest is paused."""
    return None

# --- AMAZON MUSIC SEARCH & STREAM ---

def _amazon_timestamp_to_date(ts):
    """Convertit un timestamp Unix (secondes) en date ISO string."""
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime('%Y-%m-%d')
    except:
        return None

def sync_search_amazon(query, limit=50):
    """
    Recherche via l'API Amazon Music (remplace Tidal).
    """
    logger.info(f"[Amazon Search] Query: {query}")
    try:
        url = f"{AMAZON_MUSIC_API_BASE}/search"
        params = {
            "query": query,
            "types": "track",
            "country": "FR"
        }
        res = requests.get(url, params=params, timeout=15)
        logger.info(f"[Amazon Search] Status: {res.status_code}")
        
        if res.status_code != 200:
            logger.error(f"[Amazon Search] Error Body: {res.text}")
            return []

        data = res.json()
        results_blocks = data.get('results', [])
        results = []
        
        for block in results_blocks:
            hits = block.get('hits', [])
            for hit in hits:
                doc = hit.get('document', {})
                doc_type = doc.get('__type', '')
                
                if 'CatalogTrack' not in doc_type:
                    continue
                    
                try:
                    # Extract cover image
                    art_url = ''
                    art_original = doc.get('artOriginal', {})
                    if art_original:
                        art_url = art_original.get('artUrl') or art_original.get('URL', '')
                    
                    if not art_url:
                        art_url = 'https://placehold.co/300x300/1a1a1a/666666?text=Amazon'
                    
                    track = {
                        'id': doc.get('asin', ''),
                        'title': doc.get('title', 'Titre Inconnu'),
                        'performer': {'name': doc.get('artistName', 'Inconnu')},
                        'album': {
                            'title': doc.get('albumName', ''),
                            'image': {'large': art_url}
                        },
                        'duration': doc.get('duration', 0),
                        'maximum_bit_depth': 16,
                        'source': 'amazon_music',
                        'date': _amazon_timestamp_to_date(doc.get('originalReleaseDate')),
                        'artist_asin': doc.get('artistAsin', ''),
                        'album_asin': doc.get('albumAsin', '')
                    }
                    results.append(track)
                    
                    if len(results) >= limit:
                        break
                except Exception as e:
                    logger.error(f"[Amazon Search] Parse Error: {e}")
                    continue
            
            if len(results) >= limit:
                break
                    
        logger.info(f"[Amazon Search] Found {len(results)} items")
        return results

    except Exception as e:
        logger.error(f"[Amazon Search] Exception: {e}")
        return []

def sync_search_amazon_albums(query, limit=15):
    """
    Recherche d'albums via l'API Amazon Music.
    """
    logger.info(f"[Amazon Album Search] Query: {query}")
    try:
        url = f"{AMAZON_MUSIC_API_BASE}/search"
        params = {
            "query": query,
            "types": "album",
            "country": "FR"
        }
        res = requests.get(url, params=params, timeout=15)
        
        if res.status_code != 200:
            return []

        data = res.json()
        results_blocks = data.get('results', [])
        results = []
        
        for block in results_blocks:
            hits = block.get('hits', [])
            for hit in hits:
                doc = hit.get('document', {})
                doc_type = doc.get('__type', '')
                
                if 'CatalogAlbum' not in doc_type and 'Album' not in doc_type:
                    # Also accept tracks and extract album info
                    if 'CatalogTrack' in doc_type and doc.get('albumAsin'):
                        art_url = ''
                        art_original = doc.get('artOriginal', {})
                        if art_original:
                            art_url = art_original.get('artUrl') or art_original.get('URL', '')
                    
                        album_entry = {
                            'id': doc.get('albumAsin', ''),
                            'title': doc.get('albumName', doc.get('title', '')),
                            'artist': {'name': doc.get('artistName', 'Inconnu')},
                            'image': {'large': art_url or 'https://placehold.co/300x300/1a1a1a/666666?text=Amazon'},
                            'source': 'amazon_music',
                            'date': _amazon_timestamp_to_date(doc.get('originalReleaseDate'))
                        }
                        # Avoid duplicate albums
                        if not any(a['id'] == album_entry['id'] for a in results):
                            results.append(album_entry)
                    continue
                    
                try:
                    art_url = ''
                    art_original = doc.get('artOriginal', {})
                    if art_original:
                        art_url = art_original.get('artUrl') or art_original.get('URL', '')
                    
                    results.append({
                        'id': doc.get('asin', ''),
                        'title': doc.get('title', doc.get('albumName', '')),
                        'artist': {'name': doc.get('artistName', 'Inconnu')},
                        'image': {'large': art_url or 'https://placehold.co/300x300/1a1a1a/666666?text=Amazon'},
                        'source': 'amazon_music',
                        'date': _amazon_timestamp_to_date(doc.get('originalReleaseDate'))
                    })
                    
                    if len(results) >= limit:
                        break
                except Exception as e:
                    logger.error(f"[Amazon Album Search] Parse Error: {e}")
                    continue
            
            if len(results) >= limit:
                break
        
        return results

    except Exception as e:
        logger.error(f"[Amazon Album Search] Error: {e}")
        return []

def sync_get_amazon_album(album_asin):
    """
    Récupère les détails d'un album Amazon Music via recherche.
    """
    logger.info(f"[Amazon Album] Fetching album: {album_asin}")
    try:
        # Search for the album to get tracks
        url = f"{AMAZON_MUSIC_API_BASE}/search"
        params = {
            "query": album_asin,
            "types": "track,album",
            "country": "FR"
        }
        res = requests.get(url, params=params, timeout=15)
        
        if res.status_code != 200:
            return None

        data = res.json()
        results_blocks = data.get('results', [])
        
        album_title = ""
        album_artist = ""
        album_cover = ""
        formatted_tracks = []
        
        for block in results_blocks:
            hits = block.get('hits', [])
            for hit in hits:
                doc = hit.get('document', {})
                
                if doc.get('albumAsin') == album_asin or doc.get('asin') == album_asin:
                    if not album_title:
                        album_title = doc.get('albumName', doc.get('title', ''))
                        album_artist = doc.get('artistName', 'Inconnu')
                        art_original = doc.get('artOriginal', {})
                        if art_original:
                            album_cover = art_original.get('artUrl') or art_original.get('URL', '')
                    
                    if 'CatalogTrack' in doc.get('__type', ''):
                        art_url = ''
                        art_original = doc.get('artOriginal', {})
                        if art_original:
                            art_url = art_original.get('artUrl') or art_original.get('URL', '')
                        
                        formatted_tracks.append({
                            'id': doc.get('asin', ''),
                            'title': doc.get('title', 'Titre Inconnu'),
                            'duration': doc.get('duration', 0),
                            'track_number': doc.get('trackNum'),
                            'performer': {'name': doc.get('artistName', 'Inconnu')},
                            'album': {
                                'title': album_title,
                                'image': {'large': art_url or album_cover}
                            },
                            'source': 'amazon_music',
                            'maximum_bit_depth': 16
                        })
        
        if not album_title:
            return None
        
        return {
            'id': album_asin,
            'title': album_title,
            'artist': {'name': album_artist},
            'image': {'large': album_cover or 'https://placehold.co/300x300/1a1a1a/666666?text=Amazon'},
            'source': 'amazon_music',
            'tracks': {'items': formatted_tracks}
        }

    except Exception as e:
        logger.error(f"[Amazon Album] Error: {e}")
        return None

def get_amazon_stream_url(asin):
    """
    Récupère l'URL de stream Amazon Music via l'API t2tunes.
    """
    logger.info(f"[Amazon Stream] Fetching stream for ASIN: {asin}")
    try:
        url = f"{AMAZON_MUSIC_API_BASE}/media-from-asin"
        params = {
            "asin": asin,
            "country": "FR",
            "codec": "flac"
        }
        res = requests.get(url, params=params, timeout=15)
        
        if res.status_code != 200:
            logger.error(f"[Amazon Stream] Error {res.status_code}: {res.text}")
            return None

        data = res.json()
        
        if not data or not isinstance(data, list) or len(data) == 0:
            logger.error("[Amazon Stream] Empty response")
            return None
        
        track_data = data[0]
        
        if not track_data.get('stremeable', False):
            logger.warning(f"[Amazon Stream] Track {asin} is not streamable")
            return None
        
        stream_info = track_data.get('streamInfo', {})
        stream_url = stream_info.get('streamUrl')
        
        if not stream_url:
            logger.error("[Amazon Stream] No stream URL found")
            return None
        
        # Build cover URL from template
        cover_url = ''
        template = track_data.get('templateCoverUrl', '')
        if template:
            cover_url = template.replace('{size}', '600').replace('{jpegQuality}', '85').replace('{format}', 'jpg')
        
        tags = track_data.get('tags', {})
        
        return {
            "url": stream_url,
            "format": stream_info.get('format', 'flac'),
            "codec": stream_info.get('codec', 'flac'),
            "sampleRate": stream_info.get('sampleRate', 44100),
            "cover": cover_url,
            "tags": tags,
            "kid": stream_info.get('kid'),
            "decryptionKey": track_data.get('decryptionKey')
        }

    except Exception as e:
        logger.error(f"[Amazon Stream] Exception: {e}")
        return None

# --- FONCTIONS SYNCHRONES (WRAPPED) ---

def sync_search_yt_lyrics(title, artist):
    query = f"{title} {artist}"
    try:
        results = yt.search(query, filter="songs", limit=1)
        if not results: return None
        video_id = results[0]['videoId']
        watch = yt.get_watch_playlist(video_id)
        if not watch or 'lyrics' not in watch or not watch['lyrics']: return None
        lyrics_id = watch['lyrics']
        lyrics_data = None
        try: lyrics_data = yt.get_lyrics(browseId=lyrics_id, timestamps=True)
        except Exception:
            try: lyrics_data = yt.get_lyrics(browseId=lyrics_id)
            except: pass
        if not lyrics_data: return None
        if lyrics_data.get('lyrics') and isinstance(lyrics_data['lyrics'], list):
            lines = []
            has_timestamps = False
            for line in lyrics_data['lyrics']:
                text = line.get('text', '') if isinstance(line, dict) else getattr(line, 'text', '')
                start_ms = line.get('start_time') if isinstance(line, dict) else getattr(line, 'start_time', None)
                if start_ms is not None:
                    lines.append(f"{ms_to_lrc(start_ms)} {text}")
                    has_timestamps = True
                else: lines.append(text)
            if lines:
                if has_timestamps: return {"type": "synced", "lyrics": "\n".join(lines)}
                else: return {"type": "plain", "lyrics": "\n".join(lines)}
        if isinstance(lyrics_data.get('lyrics'), str): return {"type": "plain", "lyrics": lyrics_data['lyrics']}
        return None
    except Exception as e:
        error_msg = str(e)
        if 'musicResponsiveListItemRenderer' in error_msg: pass 
        else: logger.error(f"YT Lyrics Warning: {error_msg}")
        return None

def sync_search_deezer(query, limit=25):
    try:
        url = "https://api.deezer.com/search"
        params = {'q': query, 'index': 0, 'limit': limit}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        results = []
        if 'data' in data:
            for t in data['data']:
                cover = t.get('album', {}).get('cover_xl') or t.get('album', {}).get('cover_big')
                results.append({
                    'id': str(t.get('id')),
                    'title': t.get('title'),
                    'performer': {'name': t.get('artist', {}).get('name', 'Inconnu')},
                    'album': {'title': t.get('album', {}).get('title'), 'image': {'large': cover}},
                    'duration': t.get('duration', 0),
                    'source': 'deezer',
                    'maximum_bit_depth': 16,
                    'date': t.get('album', {}).get('release_date') # Extraction Date
                })
        return results
    except Exception as e: return []

def sync_search_deezer_tracks(query, limit=25):
    """Recherche de titres Deezer pour la grille → objets 'deezer_flac' (id = ISRC).
    Sélectionner un de ces titres déclenche la lecture directe via le resolver Deezer."""
    try:
        r = requests.get("https://api.deezer.com/search",
                         params={'q': query, 'index': 0, 'limit': limit}, timeout=8)
        data = r.json()
        results = []
        for t in data.get('data', []):
            isrc = t.get('isrc')
            if not isrc:
                continue  # sans ISRC on ne peut pas résoudre via dzr
            alb = t.get('album', {}) or {}
            cover = alb.get('cover_xl') or alb.get('cover_big') or alb.get('cover_medium')
            results.append({
                'id': isrc,  # l'ISRC sert de clé pour /deezer_stream
                'title': t.get('title'),
                'performer': {'name': t.get('artist', {}).get('name', 'Inconnu')},
                'album': {'title': alb.get('title'), 'image': {'large': cover}},
                'duration': t.get('duration', 0),
                'isrc': isrc,
                'maximum_bit_depth': 16,
                'source': 'deezer_flac',
                'date': alb.get('release_date'),
            })
        return results
    except Exception as e:
        logger.error(f"[Deezer Search] {e}")
        return []

# --- TOP PAYS (classements par pays) ---
# Liste classée fournie par le flux RSS Apple Music (par pays, sans auth, max 100).
# Apple ne fournit pas l'ISRC, donc on l'enrichit via Deezer (ISRC + pochette propre).
# La piste est marquée 'chart_lazy' : la LECTURE est résolue à la demande (Qobuz
# d'abord par ISRC, fallback Deezer) par le pipeline /resolve_metadata existant.
# Clé = code pays ISO alpha-2. Cache par pays.
TOP_COUNTRIES = {
    'fr': 'France', 'us': 'États-Unis', 'gb': 'Royaume-Uni', 'de': 'Allemagne',
    'es': 'Espagne', 'it': 'Italie', 'be': 'Belgique', 'ca': 'Canada',
    'br': 'Brésil', 'mx': 'Mexique', 'jp': 'Japon', 'kr': 'Corée du Sud',
    'nl': 'Pays-Bas', 'au': 'Australie', 'pt': 'Portugal', 'ma': 'Maroc',
}
TOP_COUNTRY_TTL = 60 * 30
_top_country_cache: dict = {}  # cc -> {'ts', 'tracks'}

def _primary_artist(artist, aggressive=False):
    """Ne garde que l'artiste principal (avant &, feat., ft., x, vs…).
    aggressive=True coupe aussi sur la virgule et '/' (pour Amazon, où l'on veut
    strictement l'artiste principal) ; sinon on les préserve (noms de groupes
    type « Tyler, The Creator », « AC/DC »)."""
    if not artist:
        return artist
    if aggressive:
        sep = r'\s*(?:,|&|×|/|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bvs\.?\b| x )\s*'
    else:
        sep = r'\s*(?:&|×|\bfeat\.?\b|\bft\.?\b|\bfeaturing\b|\bvs\.?\b| x )\s*'
    parts = re.split(sep, artist, maxsplit=1, flags=re.IGNORECASE)
    primary = parts[0].strip()
    return primary or artist.strip()

def sync_get_top_country(country, limit=100):
    cc = (country or 'fr').lower()
    if cc not in TOP_COUNTRIES:
        cc = 'fr'
    cached = _top_country_cache.get(cc)
    if cached and (time.time() - cached['ts']) < TOP_COUNTRY_TTL:
        return cached['tracks']
    try:
        r = requests.get(
            f'https://rss.applemarketingtools.com/api/v2/{cc}/music/most-played/{limit}/songs.json',
            timeout=10)
        entries = r.json().get('feed', {}).get('results', [])
    except Exception as e:
        logger.error(f"[TopCountry] RSS {cc}: {e}")
        return []
    # La liste vient directement d'Apple (titre + artiste + pochette) : pas de
    # résolution up-front (éviterait le throttling Deezer sur 100 titres). La lecture
    # résout Qobuz->Deezer à la demande via 'chart_lazy' (par titre/artiste).
    tracks = []
    for i, e in enumerate(entries):
        title = e.get('name')
        artist = _primary_artist(e.get('artistName'))
        if not title or not artist:
            continue
        art = (e.get('artworkUrl100') or '').replace('100x100', '600x600')
        tracks.append({
            'id': str(e.get('id') or f'{cc}-{i}'),
            'title': title,
            'performer': {'name': artist},
            'album': {'title': '', 'image': {'large': art}},
            'duration': 0,  # rempli à la résolution
            'maximum_bit_depth': 16,
            'source': 'chart_lazy',  # résolu Qobuz->Deezer à la lecture
            'rank': i + 1,
        })
    _top_country_cache[cc] = {'ts': time.time(), 'tracks': tracks}
    return tracks

# === AMAZON MUSIC (recherche API skill + lecture ClearKey via Shaka) ===
# Recherche : API web Amazon (skill.music.a2z.com), session anonyme via config.json.
# Lecture : un mirror communautaire (DMLS+Widevine côté serveur) résout, par ASIN, un
# MP4 fragmenté CENC (FLAC) + la clé ClearKey — sans cooldown ni identifiants. Repli sur
# zarz.moe. Le serveur proxifie les octets CHIFFRÉS par plages (compatible Vercel) et
# génère un manifeste DASH ; Shaka déchiffre en ClearKey dans le navigateur (ni ffmpeg
# ni téléchargement complet).
AMZ_SKILL_BASE = "https://na.mesk.skill.music.a2z.com/api"
AMZ_MUSIC_BASE = "https://music.amazon.com"
AMZ_MIRROR_BASE = os.getenv('AMZ_MIRROR_BASE', "https://amazon.anandserver.cfd")
AMZ_MIRROR_KEY = os.getenv('AMZ_MIRROR_KEY', "ak_8e3f1a7c2b5d9e4f0a6c3b8d1e5f2a9c7b4d0e6f")
AMZ_ZARZ_BASE = "https://api.zarz.moe/v1/dl/amazeamazeamaze"
AMZ_APP_UA = "Zenith/1.0"  # zarz.moe exige un User-Agent au format "AppName/Version"
AMZ_SESSION_TTL = 30 * 60
AMZ_MEDIA_TTL = 20 * 60
_amz_session = {'data': None, 'ts': 0}
_amz_media_cache: dict = {}    # asin -> {'url','key','kid',...,'bd','sr','ts'}
_amz_quality_cache: dict = {}  # asin -> (bit_depth, sample_rate_hz)

def _amz_quality_from_url(url):
    """Déduit (bit_depth, sample_rate_hz) du paramètre ql= de l'URL CloudFront Amazon.
    ex: ql=UHD_48 → (24, 48000) ; ql=HD → (16, 44100)."""
    m = re.search(r'[?&]ql=([A-Za-z]+)_?(\d*)', url or '')
    if not m:
        return (16, 44100)
    tier = m.group(1).upper()
    sr = int(m.group(2)) * 1000 if m.group(2) else 0
    bd = 24 if tier == 'UHD' else 16
    if not sr:
        sr = 96000 if tier == 'UHD' else 44100
    return (bd, sr)

def _amz_resolve_source(asin):
    """Résout (streamUrl, key) pour un ASIN : mirror communautaire puis repli zarz.moe."""
    try:
        r = requests.get(f"{AMZ_MIRROR_BASE}/api/track/{asin}",
                         headers={'X-API-Key': AMZ_MIRROR_KEY, 'User-Agent': SQUID_UA}, timeout=25)
        if r.status_code == 200:
            d = r.json()
            if d.get('streamUrl') and d.get('decryptionKey'):
                return d['streamUrl'], d['decryptionKey'].strip()
    except Exception as e:
        logger.warning(f"[Amazon] mirror {asin}: {e}")
    try:
        r = requests.get(f"{AMZ_ZARZ_BASE}/media", params={'asin': asin, 'codec': 'flac'},
                         headers={'User-Agent': AMZ_APP_UA, 'Accept': 'application/json'}, timeout=25)
        if r.status_code == 200:
            data = r.json()
            if isinstance(data, list):
                data = data[0] if data else {}
            audio = (data or {}).get('audio') or {}
            if audio.get('url') and audio.get('key'):
                return audio['url'], audio['key'].strip()
    except Exception as e:
        logger.warning(f"[Amazon] zarz {asin}: {e}")
    return None, None

def _amz_quality(asin):
    """Qualité Amazon (bit_depth, sr_hz) via un seul appel mirror (mis en cache)."""
    if asin in _amz_quality_cache:
        return _amz_quality_cache[asin]
    url, _ = _amz_resolve_source(asin)
    q = _amz_quality_from_url(url) if url else (0, 0)
    _amz_quality_cache[asin] = q
    return q

def _amz_get_session():
    if _amz_session['data'] and (time.time() - _amz_session['ts']) < AMZ_SESSION_TTL:
        return _amz_session['data']
    try:
        r = requests.get(AMZ_MUSIC_BASE + "/config.json",
                         headers={'User-Agent': SQUID_UA, 'Accept': 'application/json'}, timeout=12)
        cfg = r.json()
        s = {'deviceId': cfg.get('deviceId', ''), 'sessionId': cfg.get('sessionId', ''),
             'appVersion': cfg.get('version', '1.0.9678.0'), 'lang': cfg.get('displayLanguage', 'en_US'),
             'csrf': cfg.get('csrf', {}) or {}}
        _amz_session['data'] = s; _amz_session['ts'] = time.time()
        return s
    except Exception as e:
        logger.error(f"[Amazon] session: {e}")
        return None

def _amz_headers(s, page_url):
    csrf = s.get('csrf', {})
    csrf_h = json.dumps({"interface": "CSRFInterface.v1_0.CSRFHeaderElement", "token": csrf.get('token', ''),
                         "timestamp": str(csrf.get('ts', int(time.time()))),
                         "rndNonce": str(csrf.get('rnd', random.randint(0, 2000000000)))})
    auth = json.dumps({"interface": "ClientAuthenticationInterface.v1_0.ClientTokenElement", "accessToken": ""})
    return json.dumps({
        "x-amzn-authentication": auth, "x-amzn-device-model": "WEBPLAYER", "x-amzn-device-width": "1920",
        "x-amzn-device-family": "WebPlayer", "x-amzn-device-id": s.get('deviceId', ''), "x-amzn-user-agent": SQUID_UA,
        "x-amzn-session-id": s.get('sessionId', ''), "x-amzn-device-height": "1080",
        "x-amzn-request-id": f"{str(random.random())[2:]}-{int(time.time()*1000)}",
        "x-amzn-device-language": s.get('lang', 'en_US'), "x-amzn-currency-of-preference": "USD",
        "x-amzn-os-version": "1.0", "x-amzn-application-version": s.get('appVersion', '1.0.9678.0'),
        "x-amzn-device-time-zone": "UTC", "x-amzn-timestamp": str(int(time.time() * 1000)), "x-amzn-csrf": csrf_h,
        "x-amzn-music-domain": "music.amazon.com", "x-amzn-referer": "", "x-amzn-affiliate-tags": "",
        "x-amzn-ref-marker": "", "x-amzn-page-url": page_url, "x-amzn-weblab-id-overrides": "",
        "x-amzn-video-player-token": "", "x-amzn-feature-flags": "", "x-amzn-has-profile-id": "", "x-amzn-age-band": ""})

def _amz_text(v):
    if v is None: return ""
    if isinstance(v, str): return v
    if isinstance(v, (int, float)): return str(v)
    if isinstance(v, dict):
        if isinstance(v.get('text'), str) and v['text']: return v['text']
        if v.get('defaultValue'):
            t = _amz_text(v['defaultValue'])
            if t: return t
        obs = v.get('observer')
        if isinstance(obs, dict) and obs.get('defaultValue'):
            t = _amz_text(obs['defaultValue'])
            if t: return t
    return ""

def _amz_find_by_interface(obj, target, out, depth=0):
    if depth > 40 or obj is None: return out
    if isinstance(obj, dict):
        iface = obj.get('interface')
        if isinstance(iface, str) and target in iface:
            out.append(obj)
        for v in obj.values():
            _amz_find_by_interface(v, target, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            _amz_find_by_interface(v, target, out, depth + 1)
    return out

def _amz_deeplink_track(deeplink):
    if not deeplink: return None
    try:
        p = urllib.parse.urlparse(deeplink)
        qs = urllib.parse.parse_qs(p.query)
        seg = [x for x in p.path.split('/') if x]
        kind = seg[0].lower() if seg else ''
        raw = seg[1] if len(seg) > 1 else ''
        if kind == 'albums' and qs.get('trackAsin'):
            return qs['trackAsin'][0]
        if kind == 'tracks' and raw:
            return raw
    except Exception:
        pass
    return None

def _amz_cover(url, size=1000):
    """Pochette Amazon Music HD : on remonte les dimensions AA###/SX###/SY### à {size}
    (ex. AA256 → AA1000, SX472_SY472 → SX1000_SY1000), le reste de l'URL est conservé."""
    if not url: return ''
    url = url.replace('{size}', str(size)).replace('{jpegQuality}', '95').replace('{format}', 'jpg')
    url = re.sub(r'_AA\d+', f'_AA{size}', url)
    url = re.sub(r'_SX\d+', f'_SX{size}', url)
    url = re.sub(r'_SY\d+', f'_SY{size}', url)
    return url

def _amz_dur(v):
    t = _amz_text(v)
    m = re.match(r'(\d+):(\d{2})', t or '')
    return int(m.group(1)) * 60 + int(m.group(2)) if m else 0

def sync_search_amazon(query, limit=15):
    """Recherche Amazon Music (API skill, session anonyme) → objets 'amazon_music' (id = ASIN)."""
    s = _amz_get_session()
    if not s:
        return []
    page = f"{AMZ_MUSIC_BASE}/search/{urllib.parse.quote(query)}"
    body = json.dumps({
        "filter": json.dumps({"IsLibrary": ["false"]}),
        "keyword": json.dumps({"interface": "Web.TemplatesInterface.v1_0.Touch.SearchTemplateInterface.SearchKeywordClientInformation", "keyword": query}),
        "suggestedKeyword": query,
        "userHash": json.dumps({"level": "LIBRARY_MEMBER"}),
        "headers": _amz_headers(s, page)})
    try:
        r = requests.post(AMZ_SKILL_BASE + "/showSearch",
                          headers={"Content-Type": "text/plain;charset=UTF-8", "User-Agent": SQUID_UA,
                                   "Origin": AMZ_MUSIC_BASE, "Referer": page}, data=body, timeout=15)
        if r.status_code != 200:
            logger.warning(f"[Amazon Search] HTTP {r.status_code}")
            return []
        data = r.json()
    except Exception as e:
        logger.error(f"[Amazon Search] {e}")
        return []
    shovelers = []
    for ifc in ("VisualShovelerWidgetElement", "FeaturedShovelerWidgetElement", "DescriptiveShowcaseWidgetElement"):
        _amz_find_by_interface(data, ifc, shovelers)
    out = []; seen = set()
    for sh in shovelers:
        items = sh.get('items')
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            iface = item.get('interface', '') or ''
            if 'DescriptiveRowItemElement' not in iface and 'SquareHorizontalItemElement' not in iface:
                continue
            deeplink = ''
            ptl = item.get('primaryTextLink') or {}
            if isinstance(ptl, dict):
                deeplink = ptl.get('deeplink', '') or ''
            if not deeplink:
                pl = item.get('primaryLink') or {}
                if isinstance(pl, dict):
                    deeplink = pl.get('deeplink', '') or ''
            asin = _amz_deeplink_track(deeplink)
            if not asin or asin in seen:
                continue
            name = _amz_text(item.get('primaryText'))
            if not name:
                continue
            artist = _amz_text(item.get('secondaryText1')) or _amz_text(item.get('secondaryText'))
            artist = _primary_artist(artist, aggressive=True)  # strictement l'artiste principal
            img = _amz_cover(item.get('image') if isinstance(item.get('image'), str) else '')
            seen.add(asin)
            out.append({
                'id': asin, 'title': name,
                'performer': {'name': artist or 'Inconnu'},
                'album': {'title': '', 'image': {'large': img}},
                'duration': _amz_dur(item.get('secondaryText3') or item.get('duration')),
                'maximum_bit_depth': 16, 'source': 'amazon_music',
            })
            if len(out) >= limit:
                return out
    return out

def sync_search_deezer_albums(query, limit=15):
    try:
        url = "https://api.deezer.com/search/album"
        params = {'q': query, 'index': 0, 'limit': limit}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        results = []
        if 'data' in data:
            for a in data['data']:
                cover = a.get('cover_xl') or a.get('cover_big') or a.get('cover_medium')
                results.append({
                    'id': str(a.get('id')),
                    'title': a.get('title'),
                    'artist': {'name': a.get('artist', {}).get('name', 'Inconnu')},
                    'image': {'large': cover},
                    'source': 'deezer',
                    'date': a.get('release_date') # Extraction Date
                })
        return results
    except Exception as e: return []

def sync_search_deezer_playlists(query, limit=100):
    try:
        url = "https://api.deezer.com/search/playlist"
        params = {'q': query, 'index': 0, 'limit': limit}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        results = []
        if 'data' in data:
            for p in data['data']:
                cover = p.get('picture_xl') or p.get('picture_big') or p.get('picture_medium')
                results.append({
                    "id": str(p.get('id')),
                    "name": p.get('title'),
                    "title": p.get('title'),
                    "performer": { "name": p.get('user', {}).get('name', 'Deezer') },
                    "type": "playlist",
                    "source": "deezer",
                    "image": cover
                })
        return results
    except Exception as e: return []

def sync_search_deezer_artists(query, limit=15):
    try:
        url = "https://api.deezer.com/search/artist"
        params = {'q': query, 'index': 0, 'limit': limit}
        r = requests.get(url, params=params, timeout=5)
        data = r.json()
        results = []
        if 'data' in data:
            for a in data['data']:
                cover = a.get('picture_xl') or a.get('picture_big') or a.get('picture_medium')
                results.append({
                    "id": str(a.get('id')),
                    "name": a.get('name'),
                    "title": a.get('name'),
                    "type": "artist",
                    "source": "deezer",
                    "image": cover
                })
        return results
    except Exception as e: return []

def sync_qobuz_search(query, limit=25, type='track'):
    """Recherche Qobuz : API officielle en priorité, repli sur l'API alt (kennyy)."""
    coll = 'tracks' if type == 'track' else 'albums'
    # 1. API officielle
    if QOBUZ_OFFICIAL_ENABLED and client:
        try:
            r = client.api_call("catalog/search", query=query, limit=max(limit, 20), offset=0)
            items = (r.get(coll) or {}).get('items', [])[:limit]
            for it in items:
                it['source'] = 'qobuz'
                if type == 'track':
                    fix_qobuz_title(it)
                it['date'] = it.get('release_date_original') or it.get('released_at')
            if items:
                return items
        except Exception as e:
            logger.error(f"[Qobuz Search officiel] {e}")
    # 2. Repli API alt (kennyy)
    try:
        r = requests.get(f"{QOBUZ_ALT_API_BASE}/api/get-music",
                         params={'q': query, 'offset': 0}, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        if not data.get('success'):
            return []
        items = data.get('data', {}).get(coll, {}).get('items', [])[:limit]
        for it in items:
            it['source'] = 'qobuz'
            if type == 'track':
                fix_qobuz_title(it)
            it['date'] = it.get('release_date_original') or it.get('released_at')
        return items
    except Exception as e:
        logger.error(f"[Qobuz Search alt] {e}")
    return []

def sync_get_qobuz_album(album_id):
    """Détails album Qobuz : API officielle en priorité, repli kennyy (album_id = UPC)."""
    # 1. API officielle (album_id = id Qobuz)
    if QOBUZ_OFFICIAL_ENABLED and client:
        try:
            d = client.get_album_meta(album_id)
            if d and d.get('id'):
                d['source'] = 'qobuz'
                img = (d.get('image') or {})
                cover = img.get('large') or img.get('small')
                for t in d.get('tracks', {}).get('items', []):
                    t['source'] = 'qobuz'
                    fix_qobuz_title(t)
                    t.setdefault('album', {'title': d.get('title'), 'image': {'large': cover}})
                    if not t.get('performer'):
                        t['performer'] = {'name': (d.get('artist') or {}).get('name', 'Inconnu')}
                return d
        except Exception as e:
            logger.error(f"[Qobuz Album officiel] {e}")
    # 2. Repli API alt (kennyy, album_id = UPC)
    try:
        r = requests.get(f"{QOBUZ_ALT_API_BASE}/api/get-album",
                         params={'album_id': album_id}, timeout=15)
        if r.status_code != 200:
            return None
        payload = r.json()
        if not payload.get('success'):
            return None
        d = payload['data']
        img = (d.get('image') or {})
        cover = img.get('large') or img.get('small')
        tracks = []
        for t in d.get('tracks', {}).get('items', []):
            t = dict(t)
            t['source'] = 'qobuz'
            fix_qobuz_title(t)
            t.setdefault('album', {'title': d.get('title'), 'image': {'large': cover}})
            if not t.get('performer'):
                t['performer'] = {'name': (d.get('artist') or {}).get('name', 'Inconnu')}
            tracks.append(t)
        return {
            'id': d.get('id') or album_id,
            'title': d.get('title'),
            'artist': {'name': (d.get('artist') or {}).get('name', 'Inconnu')},
            'image': {'large': cover},
            'source': 'qobuz',
            'maximum_bit_depth': d.get('maximum_bit_depth', 16),
            'tracks': {'items': tracks},
        }
    except Exception as e:
        logger.error(f"[Qobuz Album] Error: {e}")
        return None

def sync_get_deezer_artist_by_id(artist_id):
    try:
        r = requests.get(f"https://api.deezer.com/artist/{artist_id}", timeout=5)
        artist = r.json()
        if 'error' in artist or not artist.get('name'): return None

        r_top = requests.get(f"https://api.deezer.com/artist/{artist_id}/top", params={"limit": 20}, timeout=5)
        top_data = r_top.json()
        top_tracks = []
        for t in top_data.get("data", []):
            top_tracks.append({
                "id": str(t["id"]),
                "title": t["title"],
                "performer": {"name": artist["name"]},
                "album": {"title": t.get("album", {}).get("title"), "image": {"large": t.get("album", {}).get("cover_xl")}},
                "duration": t["duration"],
                "source": "deezer",
                "maximum_bit_depth": 16
            })
            
        r_alb = requests.get(f"https://api.deezer.com/artist/{artist_id}/albums", params={"limit": 50}, timeout=5)
        alb_data = r_alb.json()
        albums = []
        for a in alb_data.get("data", []):
            albums.append({
                "id": str(a["id"]),
                "title": a["title"],
                "image": {"large": a.get("cover_xl") or a.get("cover_big")},
                "release_date_original": a.get("release_date"),
                "source": "deezer",
                "maximum_bit_depth": 16
            })
            
        nb_fans = artist.get('nb_fan', 0)
        formatted_fans = f"{nb_fans:,}".replace(",", " ")
            
        return {
            "id": str(artist_id),
            "name": artist["name"],
            "image": artist.get("picture_xl") or artist.get("picture_big"),
            "albums": albums,
            "top_tracks": top_tracks,
            "bio": f"{formatted_fans} abonnés (Source: Deezer)"
        }
    except Exception as e:
        logger.error(f"Deezer ID Fetch Error: {e}")
        return None

def sync_search_artist_full(name):
    # 1. Tentative via alt API (top tracks) + Qobuz client si disponible (albums/bio)
    top_tracks_alt = sync_qobuz_search(name, limit=20)
    artist_top_tracks = []
    name_lower = name.lower()
    for t in top_tracks_alt:
        performer = t.get('performer', {}).get('name', '')
        if name_lower in performer.lower() or performer.lower() in name_lower:
            artist_top_tracks.append(t)
    artist_top_tracks = artist_top_tracks[:10]

    if client:
        try:
            r = client.api_call("artist/search", query=name, limit=1)
            items = r.get('artists', {}).get('items', [])
            if items:
                artist = items[0]
                artist_id = artist['id']
                details = client.api_call("artist/get", id=artist_id, extra="albums,biography", limit=50)
                bio = ""
                if 'biography' in details and details['biography']:
                    raw_bio = details['biography'].get('content', '')
                    bio = re.sub(r'<[^>]+>', '', raw_bio).strip()
                albums = []
                if 'albums' in details and 'items' in details['albums']:
                    for a in details['albums']['items']:
                        if str(a.get('artist', {}).get('id')) == str(artist_id):
                            a['source'] = 'qobuz'
                            albums.append(a)
                return {
                    "id": artist['id'],
                    "name": artist['name'],
                    "image": artist.get('image', {}).get('large', '').replace('_300', '_600'),
                    "albums": albums,
                    "top_tracks": artist_top_tracks or [],
                    "bio": bio
                }
        except Exception as e:
            logger.error(f"Qobuz Artist Search Error: {e}")

    # 2. Fallback DEEZER
    try:
        r = requests.get("https://api.deezer.com/search/artist", params={"q": name, "limit": 10}, timeout=5)
        data = r.json()
        if not data.get("data"): return None
        
        target_clean = clean_string(name)
        found_artist = None

        for candidate in data["data"]:
            cand_clean = clean_string(candidate["name"])
            if target_clean == cand_clean:
                found_artist = candidate
                break
            score = 0
            if FUZZ_AVAILABLE:
                score = fuzz.ratio(target_clean, cand_clean)
            else:
                if target_clean in cand_clean or cand_clean in target_clean:
                    len_diff = abs(len(target_clean) - len(cand_clean))
                    if len_diff < 3: score = 90
            if score >= 90:
                found_artist = candidate
                break
        
        if not found_artist: return None
        return sync_get_deezer_artist_by_id(found_artist["id"])

    except Exception as e:
        logger.error(f"Deezer Artist Full Error: {e}")
        return None

def sync_resolve_track(title, artist, isrc=None):
    """
    Recherche le titre sur Qobuz (alt API).
    Si un ISRC est fourni, il est utilisé en priorité (plus fiable que titre+artiste).
    """
    # 1. Recherche par ISRC (priorité absolue - identifiant unique)
    if isrc:
        try:
            items = sync_qobuz_search(isrc, limit=10, type='track')
            for rec in items:
                if rec.get('isrc', '').upper() == isrc.upper():
                    rec['source'] = 'qobuz'
                    fix_qobuz_title(rec)
                    if rec.get('album', {}).get('image', {}).get('large'):
                        rec['album']['image']['large'] = rec['album']['image']['large'].replace('_300', '_600')
                    logger.info(f"[Resolve] ISRC match: {isrc} → qobuz:{rec['id']}")
                    return rec
        except Exception as e:
            logger.warning(f"[Resolve] ISRC search failed: {e}")

    # Si pas de titre/artiste : tenter Deezer par ISRC puis abandonner
    if not title or not artist:
        if DEEZER_FALLBACK_ENABLED and isrc:
            try:
                dz = sync_deezer_lookup(None, None, isrc)
                if dz: return dz
            except Exception: pass
        return None

    target_artist = clean_string(artist)
    target_title = clean_string(title)

    # 2. Recherche Qobuz par titre+artiste
    try:
        items = sync_qobuz_search(f"{title} {artist}", limit=5)
        for rec in items:
            rec_title = clean_string(rec['title'])
            rec_artist = clean_string(rec.get('performer', {}).get('name', ''))
            match_artist = False
            if FUZZ_AVAILABLE:
                if fuzz.ratio(target_artist, rec_artist) > 65: match_artist = True
            elif target_artist in rec_artist or rec_artist in target_artist: match_artist = True

            if match_artist:
                match_title = False
                if FUZZ_AVAILABLE:
                    if fuzz.ratio(target_title, rec_title) > 60: match_title = True
                elif target_title in rec_title or rec_title in target_title: match_title = True

                if match_title:
                    rec['source'] = 'qobuz'
                    if rec.get('album', {}).get('image', {}).get('large'):
                        rec['album']['image']['large'] = rec['album']['image']['large'].replace('_300', '_600')
                    return rec
    except: pass

    # 3. Fallback Deezer (titres absents du catalogue Qobuz)
    if DEEZER_FALLBACK_ENABLED:
        try:
            dz = sync_deezer_lookup(title, artist, isrc)
            if dz:
                logger.info(f"[Resolve] Deezer fallback: {dz['id']} ({dz['title']})")
                return dz
        except Exception as e:
            logger.warning(f"[Resolve] Deezer fallback failed: {e}")

    return None

# --- YOUTUBE MUSIC RADIO LOGIC ---

def sync_get_radio_queue(title, artist):
    """
    Radio basée sur YTMusic (YouTube Music) pour remplacer Chosic.
    Utilise l'Automix (RDAMVM) pour une radio officielle.
    """
    query = f"{title} {artist}"
    logger.info(f"[Radio YTMusic] Recherche pour : {query}")
    
    results_data = []

    try:
        # 1. Recherche rapide du seed
        search_results = yt.search(query, filter='songs', limit=1)

        if not search_results:
            logger.warning("[Radio YTMusic] Musique non trouvée pour le seed.")
            return []

        target_song = search_results[0]
        video_id = target_song.get('videoId')
        logger.info(f"[Radio YTMusic] Seed ID: {video_id}. Loading Automix...")

        # 2. Récupération de la playlist Automix (Radio)
        # RDAMVM + video_id génère la radio infinie officielle
        try:
            playlist_data = yt.get_watch_playlist(videoId=video_id, playlistId=f"RDAMVM{video_id}", limit=25)
        except Exception as e:
            logger.warning(f"[Radio YTMusic] RDAMVM failed ({e}), trying default watch.")
            playlist_data = yt.get_watch_playlist(videoId=video_id, limit=25)

        tracks = playlist_data.get('tracks', [])

        # 3. Traitement et nettoyage
        for track in tracks:
            # On ignore le titre seed pour éviter la répétition immédiate
            if track.get('videoId') == video_id:
                continue
            
            if track.get('videoId'):
                # Extraction Image (Logique spécifique fournie)
                thumbnails = track.get('thumbnails') or track.get('thumbnail')
                image_url = 'https://placehold.co/300x300/1a1a1a/666666?text=Music'
                
                # Si c'est un dictionnaire qui contient une liste
                if isinstance(thumbnails, dict):
                    thumbnails = thumbnails.get('thumbnails', [])
                
                # Maintenant qu'on est sûr d'avoir une liste, on prend le dernier élément
                if thumbnails and isinstance(thumbnails, list):
                    raw_url = thumbnails[-1]['url']
                    
                    # Forçage du format Google Carré HD (544x544)
                    if "googleusercontent" in raw_url:
                        base_url = raw_url.split('=')[0]
                        image_url = f"{base_url}=w544-h544-l90-rj"
                    else:
                        image_url = re.sub(r'w\d+-h\d+(-l\d+)?', 'w1200-h1200-l90', raw_url)

                # Extraction Artiste
                artist_name = "Inconnu"
                if track.get('artists'):
                    # Ne prendre que le premier artiste (artiste principal)
                    artist_name = track['artists'][0]['name']

                # Extraction Album
                album_title = "Unknown"
                if track.get('album'):
                    album_title = track['album']['name'] if isinstance(track['album'], dict) else str(track['album'])

                # Construction de l'objet compatible Zenith
                song_obj = {
                    "id": track['videoId'],
                    "title": track['title'],
                    "performer": { "name": artist_name },
                    "album": { "title": album_title, "image": { "large": image_url } },
                    "img": image_url,
                    "duration": parse_duration(track.get('length')), 
                    "source": "spotify_lazy", # Force la résolution Qobuz/Tidal côté client
                    "isRadio": True
                }
                
                results_data.append(song_obj)

        logger.info(f"[Radio YTMusic] {len(results_data)} titres trouvés.")
        return results_data

    except Exception as e:
        logger.error(f"[Radio YTMusic] Erreur : {e}")
        return []

class TranslationRequest(BaseModel):
    lines: list[str]
    target: str = 'fr'

@app.post('/translate_lines')
async def translate_lines_route(req: TranslationRequest):
    try:
        text_blob = '\n'.join(req.lines)
        def _translate():
            translator = GoogleTranslator(source='auto', target=req.target)
            return translator.translate(text_blob)
        translated_blob = await run_in_threadpool(_translate)
        translated_lines = translated_blob.split('\n')
        if len(translated_lines) != len(req.lines):
            while len(translated_lines) < len(req.lines): translated_lines.append("")
        return JSONResponse({"translated": translated_lines})
    except Exception as e: return JSONResponse({"error": str(e)}, status_code=500)

@app.get('/radio_queue')
async def get_radio_queue(artist: str, title: str):
    if not artist or not title: raise HTTPException(400, "Missing params")
    # Mode Tidal-only : radio brute YouTube (Automix). La lecture est résolue en Tidal
    # au clic, via /resolve_metadata (voir plus bas). On ne résout donc que le titre joué.
    if TIDAL_ONLY_MODE:
        tracks = await run_in_threadpool(sync_get_radio_queue, title, artist)
        if not tracks: raise HTTPException(404, "No results")
        return JSONResponse(tracks)
    tracks = await run_in_threadpool(sync_get_radio_queue, title, artist)
    if not tracks: raise HTTPException(404, "No results")
    return JSONResponse(tracks)

@app.get('/search')
async def search_tracks(q: str, type: str = 'all'):
    # MODE DEBUG TIDAL : Qobuz en pause, on ne renvoie que des titres Tidal
    if TIDAL_ONLY_MODE:
        tidal = await run_in_threadpool(sync_search_tidal, q, 50) if type in ['track', 'all'] else []
        return JSONResponse({"tracks": tidal, "albums": [], "external_playlists": [], "artists": []})

    tasks = []
    if type in ['track', 'all']:
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 50, 'track'))
        tasks.append(run_in_threadpool(sync_search_deezer_tracks, q, 25))
        tasks.append(run_in_threadpool(sync_search_amazon, q, 15))
        tasks.append(run_in_threadpool(sync_search_tidal, q, 25))
    if type in ['album', 'all']:
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 15, 'album'))
        tasks.append(run_in_threadpool(sync_search_deezer_albums, q, 15))
    if type in ['artist', 'all']:
        tasks.append(run_in_threadpool(sync_search_deezer_artists, q, 15))

    finished = await asyncio.gather(*tasks, return_exceptions=True)
    idx = 0
    qobuz_tracks = []; amazon_tracks = []; deezer_tracks = []; tidal_tracks = []
    qobuz_albums = []; amazon_albums = []; deezer_albums = []
    deezer_artists = []

    if type in ['track', 'all']:
        r1 = finished[idx]; idx += 1; qobuz_tracks = r1 if isinstance(r1, list) else []
        r2 = finished[idx]; idx += 1; deezer_tracks = r2 if isinstance(r2, list) else []
        r3 = finished[idx]; idx += 1; amazon_tracks = r3 if isinstance(r3, list) else []
        r3b = finished[idx]; idx += 1; tidal_tracks = r3b if isinstance(r3b, list) else []
    if type in ['album', 'all']:
        r4 = finished[idx]; idx += 1; qobuz_albums = r4 if isinstance(r4, list) else []
        r6 = finished[idx]; idx += 1; deezer_albums = r6 if isinstance(r6, list) else []
    if type in ['artist', 'all']:
        r7 = finished[idx]; idx += 1; deezer_artists = r7 if isinstance(r7, list) else []

    deezer_playlists = []
    if type in ['playlist', 'all']:
        deezer_playlists = await run_in_threadpool(sync_search_deezer_playlists, q, 100)

    # DEDUPLICATION AVANCÉE POUR LES TITRES
    def get_dedup_sig(track):
        t = track.get('title', '').lower()
        t = re.sub(r'\s*[\(\[].*?[\)\]]', '', t)
        t = re.sub(r'\s*-\s*.*', '', t)
        t = clean_string(t)

        p = track.get('performer', {}).get('name', '')
        if not p: p = track.get('artist', {}).get('name', '')
        p = clean_string(p)

        return f"{t}|{p}"

    # PRIORITÉ QUALITÉ : pour les doublons, on compare Qobuz / Amazon / Tidal et on
    # affiche la source de meilleure qualité (bit depth puis échantillonnage).
    # Tidal : qualité connue dès la recherche (tags). Amazon : résolue via le mirror (bornée).
    amazon_by_sig = {}
    for t in amazon_tracks:
        amazon_by_sig.setdefault(get_dedup_sig(t), t)
    tidal_by_sig = {}
    for t in tidal_tracks:
        tidal_by_sig.setdefault(get_dedup_sig(t), t)
    qobuz_sigs = [get_dedup_sig(t) for t in qobuz_tracks]

    def _track_q(t):  # (bit_depth, sample_rate_hz)
        try: bd = int(t.get('maximum_bit_depth') or 16)
        except: bd = 16
        try: sr = float(t.get('maximum_sampling_rate') or 44.1) * 1000
        except: sr = 44100.0
        return (bd, sr)

    # Amazon : résolution qualité bornée (réseau) uniquement pour les doublons Qobuz
    dup_asins = list(dict.fromkeys(
        amazon_by_sig[s]['id'] for s in qobuz_sigs if s in amazon_by_sig))[:6]
    amz_q = {}
    if dup_asins:
        res = await asyncio.gather(*[run_in_threadpool(_amz_quality, a) for a in dup_asins],
                                   return_exceptions=True)
        for a, q in zip(dup_asins, res):
            amz_q[a] = q if isinstance(q, tuple) else (0, 0)

    def _has_cover(tr):
        img = ((tr.get('album') or {}).get('image') or {}).get('large') or ''
        return bool(img) and 'placehold' not in img

    combined_tracks = []
    sigs = set()
    for i, t in enumerate(qobuz_tracks):
        s = qobuz_sigs[i]
        chosen = t; best_q = _track_q(t)
        # Tidal (qualité gratuite via tags)
        tid = tidal_by_sig.get(s)
        if tid:
            tq = _track_q(tid)
            if tq > best_q:
                chosen = dict(tid); best_q = tq
        # Amazon (qualité résolue)
        amz = amazon_by_sig.get(s)
        if amz:
            aq = amz_q.get(amz['id'], (0, 0))
            if aq[0] > 0 and aq > best_q:
                chosen = dict(amz); chosen['maximum_bit_depth'] = aq[0]; best_q = aq
        # Si on a changé de source et que la nouvelle n'a pas de pochette, garder celle de Qobuz
        if chosen is not t and not _has_cover(chosen) and _has_cover(t):
            chosen.setdefault('album', {}).setdefault('image', {})['large'] = t['album']['image']['large']
        combined_tracks.append(chosen)
        sigs.add(s)

    # Insertion Deezer (si pas de doublon)
    for t in deezer_tracks:
        s = get_dedup_sig(t)
        if s not in sigs:
            combined_tracks.append(t)
            sigs.add(s)

    # Insertion Amazon (titres absents de Qobuz/Deezer ; lecture FLAC ClearKey)
    for t in amazon_tracks:
        s = get_dedup_sig(t)
        if s not in sigs:
            combined_tracks.append(t)
            sigs.add(s)

    # Insertion Tidal (titres absents ailleurs ; lecture non câblée pour l'instant)
    for t in tidal_tracks:
        s = get_dedup_sig(t)
        if s not in sigs:
            combined_tracks.append(t)
            sigs.add(s)

    # DEDUPLICATION AVANCÉE POUR LES ALBUMS
    combined_albums = qobuz_albums
    album_sigs = set()
    for a in qobuz_albums: 
        s = f"{clean_string(a['title'])}{clean_string(a.get('artist',{}).get('name'))}"
        album_sigs.add(s)

    # Insertion Amazon Music Albums
    for a in amazon_albums:
        s = f"{clean_string(a['title'])}{clean_string(a.get('artist',{}).get('name'))}"
        if s not in album_sigs: combined_albums.append(a); album_sigs.add(s)
        
    # Insertion Deezer Albums
    for a in deezer_albums:
        s = f"{clean_string(a['title'])}{clean_string(a.get('artist',{}).get('name'))}"
        if s not in album_sigs: combined_albums.append(a); album_sigs.add(s)
    
    return JSONResponse({
        "tracks": combined_tracks,
        "albums": combined_albums,
        "external_playlists": deezer_playlists,
        "artists": deezer_artists
    })

@app.get('/artist_bio')
async def get_artist_bio_route(name: str, id: str = None, source: str = None):
    if source == 'deezer' and id:
        data = await run_in_threadpool(sync_get_deezer_artist_by_id, id)
        if data: return JSONResponse(data)
    
    data = await run_in_threadpool(sync_search_artist_full, name)
    if data: return JSONResponse(data)
    else: return JSONResponse({"bio": f"Artiste non trouvé : {name}", "image": "", "nb_fans": 0, "top_tracks": [], "albums": []})

@app.get('/blind_test_tracks')
async def get_blind_test_tracks(theme: str = 'Global Hits', limit: int = 5):
    try:
        limit = min(max(limit, 1), 20)
        tracks = await run_in_threadpool(sync_qobuz_search, theme, limit * 3)
        if not tracks: raise HTTPException(404)
        random.shuffle(tracks)
        selection = tracks[:limit]
        final = []
        for t in selection:
            final.append({
                'id': t['id'], 'title': t['title'],
                'artist': t.get('performer', {}).get('name', 'Unknown'),
                'album': t.get('album', {}).get('title'),
                'img': t.get('album', {}).get('image', {}).get('large', '').replace('_300', '_600'),
                'duration': t['duration'], 'source': 'qobuz'
            })
        return JSONResponse(final)
    except: raise HTTPException(500, "Error")

@app.get('/yt_playlist')
async def get_yt_playlist_details_route(id: str):
    def _fetch():
        try:
            if id.startswith('MPRE') or id.startswith('OLAK'): details = yt.get_album(id)
            else: details = yt.get_playlist(id, limit=100)
            tracks = []
            art = extract_thumbnail_hd(details)
            for t in details.get('tracks', []):
                try:
                    img = extract_thumbnail_hd(t) if t.get('thumbnails') else art
                    tracks.append({
                        "id": t.get("videoId"), "title": t.get("title"),
                        "performer": { "name": t.get("artists", [{'name':'Inconnu'}])[0]['name'] },
                        "album": { "title": details.get('title'), "image": { "large": img } },
                        "duration": parse_duration(t.get("duration") or t.get("lengthSeconds")),
                        "source": "yt_lazy", "type": "track", "img": img
                    })
                except: continue
            return { "id": id, "title": details.get('title'), "tracks": tracks, "image": art }
        except Exception as e: return {"error": str(e)}
    res = await run_in_threadpool(_fetch)
    if "error" in res: raise HTTPException(500, res["error"])
    return JSONResponse(res)
    
@app.get('/top_countries')
async def get_top_countries_list():
    """Liste des pays disponibles pour les classements."""
    return JSONResponse([{'code': c, 'name': n} for c, n in TOP_COUNTRIES.items()])

@app.get('/top_country')
async def get_top_country_route(country: str = 'fr'):
    tracks = await run_in_threadpool(sync_get_top_country, country, 100)
    return JSONResponse({'country': country.lower(), 'tracks': tracks})

@app.get('/deezer_playlist')
async def get_deezer_playlist_details_route(id: str):
    def _fetch():
        try:
            r = requests.get(f"https://api.deezer.com/playlist/{id}", timeout=5)
            data = r.json()
            if 'error' in data: return {"error": data['error'].get('message')}
            r_tracks = requests.get(f"https://api.deezer.com/playlist/{id}/tracks", params={'limit': 500}, timeout=5)
            tracks_json = r_tracks.json()
            raw_tracks = tracks_json.get('data', [])
            final_tracks = []
            art = data.get('picture_xl') or data.get('picture_big') or data.get('picture_medium')
            for t in raw_tracks:
                alb = t.get('album', {}) or {}
                # Pochette propre à l'album du titre (sinon repli sur la pochette playlist)
                cover = alb.get('cover_xl') or alb.get('cover_big') or alb.get('cover_medium') or art
                final_tracks.append({
                    'id': str(t.get('id')),
                    'title': t.get('title'),
                    'isrc': t.get('isrc'),  # permet la résolution Qobuz précise par ISRC
                    'performer': {'name': t.get('artist', {}).get('name', data.get('artist', {}).get('name'))},
                    'album': {'title': alb.get('title') or data.get('title'), 'image': {'large': cover}},
                    'duration': t.get('duration', 0),
                    'source': 'deezer',
                    'maximum_bit_depth': 16,
                    'type': 'track'
                })
            return { "id": str(data.get('id')), "title": data.get('title'), "tracks": final_tracks, "image": art }
        except Exception as e: return {"error": str(e)}
    res = await run_in_threadpool(_fetch)
    if "error" in res: raise HTTPException(500, res["error"])
    return JSONResponse(res)

@app.get('/resolve_stream')
async def resolve_and_stream(title: str, artist: str):
    if TIDAL_ONLY_MODE:
        raise HTTPException(404, "Qobuz/Deezer paused (Tidal-only mode)")
    match = await run_in_threadpool(sync_resolve_track, title, artist)
    if match:
        rid = match['id']
        if match['source'] == 'amazon_music': return RedirectResponse(f"/amazon_stream/{rid}")
        if match['source'] == 'tidal_hund': return RedirectResponse(f"/tidal_manifest/{rid}")
        if match['source'] == 'deezer_flac': return RedirectResponse(f"/deezer_stream/{rid}")
        return RedirectResponse(f"/stream/{rid}")
    raise HTTPException(404, "Track not found")

@app.get('/resolve_metadata')
async def resolve_metadata_route(title: str = '', artist: str = '', isrc: str = None):
    """
    Retourne l'objet track complet (ID, source, image, etc.).
    Accepte title+artist et/ou isrc (ISRC est prioritaire).
    """
    # Mode Tidal-only : on résout vers Tidal (recherche par titre+artiste), jamais Qobuz
    if TIDAL_ONLY_MODE:
        match = await run_in_threadpool(_tidal_resolve_one, title, artist)
        if match: return JSONResponse(match)
        raise HTTPException(404, "Not found on Tidal")
    match = await run_in_threadpool(sync_resolve_track, title, artist, isrc)
    if match: return JSONResponse(match)
    raise HTTPException(404, "Not found")

@app.get('/track')
async def get_track_info(id: str, source: str = None):
    if source in ('tidal_hund', 'amazon_music'):
        raise HTTPException(404)
    # squid (ASIN) en pause : seulement si réactivé
    if SQUID_ENABLED and is_asin(id):
        res = await run_in_threadpool(squid.get_track_meta, id)
        if res: return JSONResponse(res)
        raise HTTPException(404)
    if QOBUZ_OFFICIAL_ENABLED and client:
        try:
            res = await run_in_threadpool(client.get_track_meta, id)
            res['source'] = 'qobuz'; fix_qobuz_title(res)
            return JSONResponse(res)
        except: pass
    # L'API alt (kennyy) n'expose pas de lookup track-by-id :
    # les liens directs /track?source=qobuz dégradent proprement (le frontend re-résout).
    raise HTTPException(404)

@app.get('/album')
async def get_album(id: str, source: str = None):
    if source == 'amazon_music':
        if SQUID_ENABLED:
            res = await run_in_threadpool(sync_get_amazon_album, id)
            if res: return JSONResponse(res)
        raise HTTPException(404)
    elif source == 'tidal_hund':
        res = await run_in_threadpool(sync_get_tidal_album, id)
        if res: return JSONResponse(res)
    elif source == 'deezer':
        def _fetch_deezer_album():
            try:
                r = requests.get(f"https://api.deezer.com/album/{id}", timeout=5)
                data = r.json()
                r_tracks = requests.get(f"https://api.deezer.com/album/{id}/tracks", params={'limit': 500}, timeout=5)
                tracks_json = r_tracks.json()
                raw_tracks = tracks_json.get('data', [])
                final_tracks = []
                for t in raw_tracks:
                    final_tracks.append({
                        'id': str(t.get('id')),
                        'title': t.get('title'),
                        'duration': t.get('duration', 0),
                        'track_number': t.get('track_position'),
                        'performer': {'name': t.get('artist', {}).get('name', data.get('artist', {}).get('name'))},
                        'album': {'title': data.get('title'), 'image': {'large': data.get('cover_xl') or data.get('cover_big')}},
                        'source': 'deezer',
                        'maximum_bit_depth': 16
                    })
                return {
                    'id': str(data.get('id')), 'title': data.get('title'), 'artist': {'name': data.get('artist', {}).get('name')},
                    'image': {'large': data.get('cover_xl') or data.get('cover_big')}, 'source': 'deezer', 'tracks': {'items': final_tracks}
                }
            except Exception as e: return None
        res = await run_in_threadpool(_fetch_deezer_album)
        if res: return JSONResponse(res)
    else:
        # Qobuz (ou source absente) : détails via l'API alt (kennyy)
        res = await run_in_threadpool(sync_get_qobuz_album, id)
        if res: return JSONResponse(res)
    raise HTTPException(404)

@app.get('/artist')
async def get_artist(id: str):
    if not client: raise HTTPException(500)
    try:
        meta = await run_in_threadpool(client.api_call, "artist/get", id=id, extra="albums", limit=50)
        albums = []
        if 'albums' in meta and 'items' in meta['albums']: albums = meta['albums']['items']
        meta['albums'] = {'items': albums}
        return JSONResponse(meta)
    except: raise HTTPException(404)

def _resolve_qobuz_url(track_id: str):
    """Résout l'URL CDN Qobuz. API officielle (getFileUrl signé) en priorité, repli kennyy."""
    if not QOBUZ_ENABLED:
        return None
    # 1. API officielle (getFileUrl signé) — qualité décroissante
    if QOBUZ_OFFICIAL_ENABLED and client:
        for fmt in [27, 7, 6, 5]:
            try:
                d = client.get_track_url(track_id, fmt)
                if d.get('url'):
                    return d['url']
            except: continue
    # 2. Repli API alt (kennyy)
    for fmt in [27, 7, 6, 5]:
        try:
            r = requests.get(
                f"{QOBUZ_ALT_API_BASE}/api/download-music",
                params={"track_id": track_id, "quality": fmt},
                timeout=15
            )
            if r.status_code == 200:
                data = r.json()
                if data.get('success') and data.get('data', {}).get('url'):
                    return data['data']['url']
        except: continue
    return None

def squid_decrypt_audio(asin: str):
    """
    Télécharge le flux chiffré squid.wtf, le déchiffre et le transcode via ffmpeg.
    Renvoie (content_bytes, media_type) ou None.
    """
    fetched = squid.fetch_encrypted_stream(asin)
    if not fetched:
        return None
    enc, key, codec = fetched
    codec = (codec or 'flac').lower()
    is_mp4 = codec in ('opus', 'eac3', 'ec-3', 'ac-3', 'atmos')

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as tf:
            tf.write(enc)
            tmp_path = tf.name

        def _run(args):
            try:
                p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            except FileNotFoundError:
                raise RuntimeError(
                    "ffmpeg introuvable : il est requis pour déchiffrer les flux squid.wtf. "
                    "Installez ffmpeg sur l'hôte (impossible sur Vercel serverless — voir Docker)."
                )
            out, err = p.communicate()
            return p.returncode, out, err

        if is_mp4:
            rc, out, err = _run(['ffmpeg', '-y', '-decryption_key', key, '-i', tmp_path,
                                 '-map', '0:a:0', '-c:a', 'copy', '-f', 'mp4',
                                 '-movflags', 'frag_keyframe+empty_moov', 'pipe:1'])
            if rc == 0 and out:
                return out, 'audio/mp4'
            logger.error(f"[Squid Decrypt] mp4 copy failed: {err[-300:].decode('utf-8','replace')}")
            return None

        # FLAC : copie du flux (rapide, lossless), repli sur ré-encodage
        rc, out, err = _run(['ffmpeg', '-y', '-decryption_key', key, '-i', tmp_path,
                             '-map', '0:a:0', '-c:a', 'copy', '-f', 'flac', 'pipe:1'])
        if rc == 0 and out:
            return out, 'audio/flac'
        rc, out, err = _run(['ffmpeg', '-y', '-decryption_key', key, '-i', tmp_path,
                             '-map', '0:a:0', '-c:a', 'flac', '-f', 'flac', 'pipe:1'])
        if rc == 0 and out:
            return out, 'audio/flac'
        logger.error(f"[Squid Decrypt] flac failed: {err[-300:].decode('utf-8','replace')}")
        return None
    except Exception as e:
        logger.error(f"[Squid Decrypt] Exception {asin}: {e}")
        return None
    finally:
        if tmp_path:
            try: os.remove(tmp_path)
            except: pass

# --- DEEZER FALLBACK (FLAC/MP3 via dzr.tabs-vs-spaces.wtf + déchiffrement Blowfish stripe) ---
# Secours quand Qobuz n'a pas le titre. Le resolver renvoie, par ISRC : l'URL CDN Deezer
# (chiffrée Blowfish stripe), la clé et le format. Le déchiffrement étant indépendant par
# bloc de 2048 o et le CDN supportant les Range, on sert n'importe quelle plage en
# déchiffrant à la volée → réponses petites (compatible Vercel) et lecture immédiate.
DEEZER_FALLBACK_ENABLED = True
DZR_RESOLVER = "https://dzr.tabs-vs-spaces.wtf/track/"
DZR_ORIGIN = "https://monochrome.tf"
DEEZER_BF_IV = bytes.fromhex("0001020304050607")
DEEZER_CHUNK = 2048
DEEZER_MAX_RANGE = 1024 * 1024  # 1 Mo max par réponse (sous la limite Vercel)
_dzr_cache: dict = {}           # isrc -> {'url','key','mt','size','ts'}
DZR_CACHE_TTL = 25 * 60

def _deezer_api(url):
    try:
        r = requests.get(url, timeout=8, headers={'User-Agent': SQUID_UA})
        return r.json()
    except Exception:
        return None

def sync_deezer_lookup(title, artist, isrc):
    """Trouve l'ISRC du titre (donné ou via recherche Deezer) → objet track
    'deezer_flac' dont l'id EST l'ISRC (clé du resolver dzr), ou None."""
    td = None
    final_isrc = isrc
    if isrc:
        d = _deezer_api(f"https://api.deezer.com/2.0/track/isrc:{isrc}")
        if d and 'error' not in d and d.get('id'):
            td = d
    if not td and title and artist:
        first_artist = artist.split(',')[0].strip()
        q = urllib.parse.quote(f'track:"{title}" artist:"{first_artist}"')
        res = _deezer_api(f"https://api.deezer.com/search?q={q}&limit=10")
        items = (res or {}).get('data', [])
        tgt_t = clean_string(title); tgt_a = clean_string(first_artist)
        best = None; best_score = 0
        for t in items:
            st = clean_string(t.get('title', '')); sa = clean_string(t.get('artist', {}).get('name', ''))
            if FUZZ_AVAILABLE:
                score = fuzz.ratio(tgt_t, st) * 0.7 + fuzz.ratio(tgt_a, sa) * 0.3
            else:
                score = 0
                if tgt_t in st or st in tgt_t: score += 70
                if tgt_a in sa or sa in tgt_a: score += 30
            if score > best_score:
                best_score = score; best = t
        if best and best_score >= 60 and best.get('id'):
            td = _deezer_api(f"https://api.deezer.com/track/{best['id']}") or best
    if td and not final_isrc:
        final_isrc = td.get('isrc')
    if not final_isrc:
        return None
    td = td or {}
    alb = td.get('album', {}) or {}
    cover = alb.get('cover_xl') or alb.get('cover_big') or alb.get('cover_medium') or ''
    return {
        'id': final_isrc,  # l'ISRC sert de clé pour /deezer_stream
        'title': td.get('title', '') or (title or ''),
        'performer': {'name': td.get('artist', {}).get('name') or (artist or 'Inconnu')},
        'album': {'title': alb.get('title', ''), 'image': {'large': cover}},
        'duration': td.get('duration', 0),
        'isrc': final_isrc,
        'maximum_bit_depth': 16,
        'source': 'deezer_flac',
    }

def _dzr_resolve(isrc):
    """Resolver dzr par ISRC → {'url','key'(bytes),'mt','size'} (mis en cache) ou None."""
    c = _dzr_cache.get(isrc)
    if c and (time.time() - c['ts']) < DZR_CACHE_TTL:
        return c
    try:
        r = requests.get(f"{DZR_RESOLVER}?isrc={urllib.parse.quote(isrc)}&format=FLAC",
                         headers={"User-Agent": SQUID_UA, "Origin": DZR_ORIGIN}, timeout=25)
        if r.status_code != 200:
            logger.warning(f"[Deezer] dzr {isrc} -> {r.status_code}")
            return None
        d = r.json()
        url = d.get('url'); kh = d.get('blowfishKey')
        if not url or not kh:
            return None
        mt = 'audio/flac' if str(d.get('format', '')).upper().startswith('FLAC') else 'audio/mpeg'
        size = 0
        try:
            h = requests.get(url, headers={'Range': 'bytes=0-0', 'User-Agent': SQUID_UA}, timeout=10)
            cr = h.headers.get('Content-Range', '')
            if '/' in cr:
                size = int(cr.rsplit('/', 1)[-1])
        except Exception:
            pass
        info = {'url': url, 'key': bytes.fromhex(kh), 'mt': mt, 'size': size, 'ts': time.time()}
        _dzr_cache[isrc] = info
        return info
    except Exception as e:
        logger.error(f"[Deezer] dzr resolve error {isrc}: {e}")
        return None

def _deezer_decrypt(key, enc, abs_start):
    """Déchiffre une plage (abs_start aligné sur 2048) — schéma Blowfish stripe Deezer."""
    from Crypto.Cipher import Blowfish
    data = bytearray(enc)
    first_chunk = abs_start // DEEZER_CHUNK
    for i in range(0, len(data), DEEZER_CHUNK):
        gi = first_chunk + i // DEEZER_CHUNK
        clen = min(DEEZER_CHUNK, len(data) - i)
        if gi % 3 == 0 and clen == DEEZER_CHUNK:
            cipher = Blowfish.new(key, Blowfish.MODE_CBC, DEEZER_BF_IV)
            data[i:i + DEEZER_CHUNK] = cipher.decrypt(bytes(data[i:i + DEEZER_CHUNK]))
    return bytes(data)

@app.get('/deezer_stream/{isrc}')
async def deezer_stream(isrc: str, request: Request):
    info = await run_in_threadpool(_dzr_resolve, isrc)
    if not info or not info.get('url'):
        raise HTTPException(404, "Deezer stream not found")
    url = info['url']; key = info['key']; mt = info['mt']; size = info.get('size') or 0

    # Plage demandée (sinon début), plafonnée pour rester sous la limite Vercel
    start = 0; end = None
    rng = request.headers.get('range') or request.headers.get('Range') or ''
    m = re.match(r'bytes=(\d+)-(\d*)', rng)
    if m:
        start = int(m.group(1))
        if m.group(2):
            end = int(m.group(2))
    if end is None:
        end = start + DEEZER_MAX_RANGE - 1
    end = min(end, start + DEEZER_MAX_RANGE - 1)
    if size:
        end = min(end, size - 1)
    if start > end:
        start = 0
        end = min(DEEZER_MAX_RANGE - 1, (size - 1) if size else DEEZER_MAX_RANGE - 1)

    # Alignement sur les blocs de 2048 pour le fetch + déchiffrement
    aligned_start = (start // DEEZER_CHUNK) * DEEZER_CHUNK
    aligned_end = ((end // DEEZER_CHUNK) + 1) * DEEZER_CHUNK - 1
    if size:
        aligned_end = min(aligned_end, size - 1)

    def _fetch_dec():
        r = requests.get(url, headers={'Range': f'bytes={aligned_start}-{aligned_end}', 'User-Agent': SQUID_UA}, timeout=40)
        dec = _deezer_decrypt(key, r.content, aligned_start)
        return dec[start - aligned_start: end - aligned_start + 1]

    body = await run_in_threadpool(_fetch_dec)
    total = str(size) if size else '*'
    return Response(
        content=body, status_code=206, media_type=mt,
        headers={
            'Content-Range': f'bytes {start}-{start + len(body) - 1}/{total}',
            'Accept-Ranges': 'bytes',
            'Content-Length': str(len(body)),
            'Cache-Control': 'no-store',
            'Access-Control-Allow-Origin': '*',
        },
    )

def _amz_parse_head(head):
    """Parse l'en-tête MP4 fragmenté Amazon → (init_end, sidx_start, sidx_end, kid_hex, dur_s, sr)."""
    moov_end = 0; sidx_start = None; sidx_end = None
    off = 0; n = len(head)
    while off + 8 <= n:
        size = struct.unpack(">I", head[off:off+4])[0]
        typ = head[off+4:off+8]
        if size < 8:
            break
        if typ == b'moov':
            moov_end = off + size
        if typ == b'sidx':
            sidx_start = off; sidx_end = off + size
            break
        off += size
    # KID dans la box tenc (par défaut)
    kid = None
    ti = head.find(b'tenc')
    if ti != -1:
        kid = head[ti+12:ti+28].hex()
    # Durée : somme des subsegment_duration de la sidx / timescale
    # (mvhd vaut 0 en MP4 fragmenté Amazon).
    dur_s = 0
    if sidx_start is not None:
        try:
            o = sidx_start + 8
            ver = head[o]; o += 4  # version+flags
            o += 4               # reference_ID
            ts = struct.unpack(">I", head[o:o+4])[0]; o += 4  # timescale
            o += 8 if ver == 0 else 16  # earliest_presentation_time + first_offset
            o += 2               # reserved
            ref_count = struct.unpack(">H", head[o:o+2])[0]; o += 2
            total = 0
            for _ in range(ref_count):
                o += 4  # reference_type + referenced_size
                total += struct.unpack(">I", head[o:o+4])[0]; o += 4  # subsegment_duration
                o += 4  # SAP
            if ts:
                dur_s = total / ts
        except Exception:
            pass
    init_end = (moov_end - 1) if moov_end else None
    return init_end, sidx_start, sidx_end, kid, dur_s

def _amz_resolve(asin):
    """Résout l'ASIN (mirror puis zarz) → infos pour le manifeste DASH ClearKey (cache)."""
    c = _amz_media_cache.get(asin)
    if c and (time.time() - c['ts']) < AMZ_MEDIA_TTL:
        return c
    url, key = _amz_resolve_source(asin)
    if not url or not key:
        return None
    bd, sr = _amz_quality_from_url(url)
    _amz_quality_cache[asin] = (bd, sr)
    # En-tête du MP4 chiffré : KID, ranges init/sidx, durée, taille totale
    try:
        h = requests.get(url, headers={'User-Agent': AMZ_APP_UA, 'Range': 'bytes=0-16383'}, timeout=20)
        head = h.content
        cr = h.headers.get('Content-Range', '')
        size = int(cr.rsplit('/', 1)[-1]) if '/' in cr else 0
    except Exception as e:
        logger.error(f"[Amazon] head {asin}: {e}")
        return None
    init_end, sidx_start, sidx_end, kid, dur_s = _amz_parse_head(head)
    if init_end is None or sidx_start is None or not kid:
        logger.warning(f"[Amazon] head parse incomplet {asin}: init={init_end} sidx={sidx_start} kid={bool(kid)}")
        return None
    info = {'url': url, 'key': key, 'kid': kid, 'init_end': init_end,
            'sidx_start': sidx_start, 'sidx_end': sidx_end, 'dur': dur_s,
            'size': size, 'sr': sr, 'bd': bd, 'ts': time.time()}
    _amz_media_cache[asin] = info
    return info

def _amz_build_mpd(info):
    """Manifeste DASH (SegmentBase) pour un MP4 fragmenté FLAC chiffré CENC."""
    kid = info['kid']
    kid_uuid = f"{kid[0:8]}-{kid[8:12]}-{kid[12:16]}-{kid[16:20]}-{kid[20:32]}"
    dur = info['dur'] or 0
    bw = int(info['size'] * 8 / dur) if dur else 320000
    sr = info['sr'] or 44100
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" xmlns:cenc="urn:mpeg:cenc:2013" '
        'profiles="urn:mpeg:dash:profile:isoff-on-demand:2011" type="static" minBufferTime="PT2S" '
        f'mediaPresentationDuration="PT{dur:.3f}S">'
        '<Period>'
        '<AdaptationSet mimeType="audio/mp4" contentType="audio" segmentAlignment="true">'
        f'<ContentProtection schemeIdUri="urn:mpeg:dash:mp4protection:2011" value="cenc" cenc:default_KID="{kid_uuid}"/>'
        '<ContentProtection schemeIdUri="urn:uuid:e2719d58-a985-b3c9-781a-b030af78d30e"/>'
        f'<Representation id="1" codecs="flac" audioSamplingRate="{sr}" bandwidth="{bw}">'
        f'<BaseURL>/amazon_proxy/{info["asin"]}</BaseURL>'
        f'<SegmentBase indexRange="{info["sidx_start"]}-{info["sidx_end"]-1}">'
        f'<Initialization range="0-{info["init_end"]}"/>'
        '</SegmentBase>'
        '</Representation>'
        '</AdaptationSet>'
        '</Period>'
        '</MPD>'
    )

@app.get('/amazon_manifest/{asin}')
async def amazon_manifest(asin: str):
    """Manifeste DASH + clé ClearKey pour la lecture Amazon (Shaka déchiffre côté client)."""
    info = await run_in_threadpool(_amz_resolve, asin)
    if not info:
        raise HTTPException(404, "Amazon stream not found")
    info = dict(info); info['asin'] = asin
    mpd = _amz_build_mpd(info)
    manifest_b64 = base64.b64encode(mpd.encode('utf-8')).decode('ascii')
    # clé ClearKey au format attendu par Shaka : { kidHex: keyHex }
    return JSONResponse({'manifest': manifest_b64, 'kid': info['kid'], 'key': info['key'],
                         'mimeType': 'application/dash+xml'})

@app.get('/amazon_proxy/{asin}')
async def amazon_proxy(asin: str, request: Request):
    """Proxy par plages des octets CHIFFRÉS Amazon (passthrough, compatible Vercel)."""
    info = await run_in_threadpool(_amz_resolve, asin)
    if not info:
        raise HTTPException(404, "Amazon stream not found")
    url = info['url']
    rng = request.headers.get('range') or request.headers.get('Range') or 'bytes=0-'

    def _fetch():
        return requests.get(url, headers={'User-Agent': AMZ_APP_UA, 'Range': rng}, timeout=40)

    r = await run_in_threadpool(_fetch)
    headers = {
        'Accept-Ranges': 'bytes',
        'Content-Length': str(len(r.content)),
        'Cache-Control': 'no-store',
        'Access-Control-Allow-Origin': '*',
    }
    cr = r.headers.get('Content-Range')
    if cr:
        headers['Content-Range'] = cr
    return Response(content=r.content, status_code=r.status_code, media_type='audio/mp4', headers=headers)

@app.get('/stream_url/{track_id}')
async def get_stream_url(track_id: str):
    """Retourne l'URL CDN Qobuz en JSON (pour le pré-fetch frontend)."""
    if TIDAL_ONLY_MODE:
        raise HTTPException(404, "Qobuz paused (Tidal-only mode)")
    if SQUID_ENABLED and is_asin(track_id):
        return JSONResponse({'url': f"/stream/{track_id}"})
    url = await run_in_threadpool(_resolve_qobuz_url, track_id)
    if url:
        return JSONResponse({'url': url})
    raise HTTPException(404, "URL not found")

@app.get('/stream/{track_id}')
async def stream_track(track_id: str):
    if TIDAL_ONLY_MODE:
        raise HTTPException(404, "Qobuz paused (Tidal-only mode)")
    # squid (Amazon Music) en pause : déchiffrement serveur uniquement si réactivé
    if SQUID_ENABLED and is_asin(track_id):
        result = await run_in_threadpool(squid_decrypt_audio, track_id)
        if not result:
            raise HTTPException(404, "Stream not found")
        content, media_type = result
        return Response(
            content=content,
            media_type=media_type,
            headers={
                'Content-Length': str(len(content)),
                'Accept-Ranges': 'bytes',
                'Cache-Control': 'public, max-age=3600',
                'Access-Control-Allow-Origin': '*',
            },
        )
    # Qobuz : redirection vers l'URL CDN directe (compatible Vercel)
    url = await run_in_threadpool(_resolve_qobuz_url, track_id)
    if url:
        return RedirectResponse(url)
    raise HTTPException(404, "URL not found")

@app.get('/amazon_stream/{asin}')
async def get_amazon_stream_route(asin: str):
    """Endpoint pour décrypter et streamer Amazon Music via byte-level CENC AES-CTR."""
    stream_data = await run_in_threadpool(get_amazon_stream_url, asin)
    if not stream_data or not stream_data.get('url'):
        raise HTTPException(404, "Amazon Music stream not found")
    
    decryption_key_hex = stream_data.get('decryptionKey')
    stream_url = stream_data['url']
    
    # If no decryption key or no crypto, just redirect to the raw URL
    if not decryption_key_hex or not CRYPTO_AVAILABLE:
        return RedirectResponse(stream_url)
    
    def _decrypt_cenc_inplace():
        """
        Pure byte-level CENC AES-CTR decryption.
        - Parses MP4 boxes manually (no pymp4)
        - Decrypts mdat samples in-place using trun/senc from moof
        - Patches enca->mp4a / encv->avc1 in stsd and removes sinf boxes
        - Returns a valid, decrypted MP4 that browsers can play directly
        """
        try:
            # Download the encrypted file
            resp = requests.get(stream_url, timeout=60)
            if resp.status_code != 200:
                logger.error(f"[Amazon Decrypt] Download failed: {resp.status_code}")
                return None
            
            data = bytearray(resp.content)
            key = bytes.fromhex(decryption_key_hex)
            logger.info(f"[Amazon Decrypt] Downloaded {len(data)} bytes, key={decryption_key_hex[:8]}...")
            
            # === MP4 Box Parsing Helpers ===
            def read_u16(buf, off):
                return struct.unpack_from(">H", buf, off)[0]
            
            def read_u32(buf, off):
                return struct.unpack_from(">I", buf, off)[0]
            
            def read_u64(buf, off):
                return struct.unpack_from(">Q", buf, off)[0]
            
            def get_box_info(buf, off):
                """Returns (box_type, header_size, total_size) for box at offset."""
                if off + 8 > len(buf):
                    return None, 0, 0
                size = read_u32(buf, off)
                box_type = bytes(buf[off+4:off+8])
                header_size = 8
                if size == 1:
                    if off + 16 > len(buf):
                        return None, 0, 0
                    size = read_u64(buf, off + 8)
                    header_size = 16
                elif size == 0:
                    size = len(buf) - off
                return box_type, header_size, size
            
            def iter_boxes(buf, start, end):
                """Iterate top-level boxes in buf[start:end], yielding (type, hdr_size, total_size, offset)."""
                off = start
                while off + 8 <= end:
                    box_type, hdr, total = get_box_info(buf, off)
                    if box_type is None or total < 8:
                        break
                    yield box_type, hdr, total, off
                    off += total
            
            def find_box(buf, start, end, target_type):
                """Find first child box of target_type, return (offset, hdr_size, total_size) or None."""
                for btype, hdr, total, off in iter_boxes(buf, start, end):
                    if btype == target_type:
                        return off, hdr, total
                return None
            
            def find_box_deep(buf, start, end, target_type):
                """Recursively find a box type inside container boxes."""
                containers = {b'moov', b'trak', b'mdia', b'minf', b'stbl', b'moof', b'traf', b'edts', b'udta', b'sinf', b'schi'}
                for btype, hdr, total, off in iter_boxes(buf, start, end):
                    if btype == target_type:
                        return off, hdr, total
                    if btype in containers:
                        result = find_box_deep(buf, off + hdr, off + total, target_type)
                        if result:
                            return result
                return None
            
            def parse_fullbox_header(buf, off):
                """Parse FullBox: returns (version, flags, data_offset)."""
                version = buf[off]
                flags = (buf[off+1] << 16) | (buf[off+2] << 8) | buf[off+3]
                return version, flags, off + 4
            
            def parse_trun(buf, box_off, box_hdr, box_total):
                """Parse trun box, return list of sample_sizes."""
                body_start = box_off + box_hdr
                version, flags, off = parse_fullbox_header(buf, body_start)
                sample_count = read_u32(buf, off); off += 4
                
                # data_offset (signed i32)
                data_offset = None
                if flags & 0x000001:
                    data_offset = struct.unpack_from(">i", buf, off)[0]; off += 4
                # first_sample_flags
                if flags & 0x000004:
                    off += 4
                
                sample_sizes = []
                for _ in range(sample_count):
                    if flags & 0x000100:  # sample_duration
                        off += 4
                    sz = 0
                    if flags & 0x000200:  # sample_size
                        sz = read_u32(buf, off); off += 4
                    sample_sizes.append(sz)
                    if flags & 0x000400:  # sample_flags
                        off += 4
                    if flags & 0x000800:  # sample_composition_time_offset
                        off += 4
                
                return sample_sizes, data_offset
            
            def parse_senc(buf, box_off, box_hdr, box_total):
                """Parse senc box, return list of (iv_bytes, subsample_list_or_None)."""
                body_start = box_off + box_hdr
                version, flags, off = parse_fullbox_header(buf, body_start)
                sample_count = read_u32(buf, off); off += 4
                has_subsamples = bool(flags & 0x02)
                
                # Determine IV size: try 8 bytes (most common for CENC)
                iv_size = 8
                
                samples = []
                for _ in range(sample_count):
                    if off + iv_size > len(buf):
                        break
                    iv = bytes(buf[off:off+iv_size]); off += iv_size
                    subs = None
                    if has_subsamples:
                        sub_count = read_u16(buf, off); off += 2
                        subs = []
                        for _ in range(sub_count):
                            clear = read_u16(buf, off); off += 2
                            encrypted = read_u32(buf, off); off += 4
                            subs.append((clear, encrypted))
                    samples.append((iv, subs))
                
                return samples
            
            def parse_tfhd(buf, box_off, box_hdr, box_total):
                """Parse tfhd, return default_sample_size (or 0)."""
                body_start = box_off + box_hdr
                version, flags, off = parse_fullbox_header(buf, body_start)
                off += 4  # track_id
                if flags & 0x000001:  # base_data_offset
                    off += 8
                if flags & 0x000002:  # sample_description_index
                    off += 4
                default_sample_duration = 0
                if flags & 0x000008:  # default_sample_duration
                    default_sample_duration = read_u32(buf, off); off += 4
                default_sample_size = 0
                if flags & 0x000010:  # default_sample_size
                    default_sample_size = read_u32(buf, off); off += 4
                return default_sample_size
            
            def make_cipher(key_bytes, iv_8bytes):
                """Create AES-CTR cipher with 8-byte IV (CENC standard: IV || counter64)."""
                ctr = CryptoCounter.new(64, prefix=iv_8bytes, initial_value=0)
                return AES.new(key_bytes, AES.MODE_CTR, counter=ctr)
            
            # === STEP 1: Patch stsd boxes (enca->mp4a, encv->avc1) and remove sinf ===
            def patch_encrypted_stsd(buf):
                """Find and patch encrypted sample entries in stsd boxes throughout the file."""
                # Find moov -> trak -> mdia -> minf -> stbl -> stsd
                moov = find_box(buf, 0, len(buf), b'moov')
                if not moov:
                    return
                moov_off, moov_hdr, moov_total = moov
                moov_body_start = moov_off + moov_hdr
                moov_body_end = moov_off + moov_total
                
                for trak_type, trak_hdr, trak_total, trak_off in iter_boxes(buf, moov_body_start, moov_body_end):
                    if trak_type != b'trak':
                        continue
                    stsd_info = find_box_deep(buf, trak_off + trak_hdr, trak_off + trak_total, b'stsd')
                    if not stsd_info:
                        continue
                    stsd_off, stsd_hdr, stsd_total = stsd_info
                    # stsd is a FullBox with entry_count
                    stsd_body = stsd_off + stsd_hdr + 4  # skip version+flags
                    entry_count = read_u32(buf, stsd_body); stsd_body += 4
                    
                    # Iterate sample entries
                    entry_off = stsd_body
                    for _ in range(entry_count):
                        if entry_off + 8 > stsd_off + stsd_total:
                            break
                        entry_type, entry_hdr_sz, entry_total = get_box_info(buf, entry_off)
                        if entry_type is None:
                            break
                        
                        entry_type_str = entry_type.decode('latin-1', errors='replace')
                        
                        if entry_type_str.startswith('enc'):
                            # Find sinf -> frma inside this entry to get original format
                            entry_body_start = entry_off + entry_hdr_sz
                            entry_body_end = entry_off + entry_total
                            
                            # For audio entries (enca), skip 28 bytes of AudioSampleEntry fields
                            # For video entries (encv), skip 78 bytes of VisualSampleEntry fields
                            if entry_type == b'enca':
                                search_start = entry_off + entry_hdr_sz + 28
                            elif entry_type == b'encv':
                                search_start = entry_off + entry_hdr_sz + 78
                            else:
                                search_start = entry_body_start + 8
                            
                            sinf_info = find_box(buf, search_start, entry_body_end, b'sinf')
                            original_format = b'mp4a'  # default
                            
                            if sinf_info:
                                sinf_off, sinf_hdr, sinf_total = sinf_info
                                frma_info = find_box(buf, sinf_off + sinf_hdr, sinf_off + sinf_total, b'frma')
                                if frma_info:
                                    frma_off, frma_hdr, frma_total = frma_info
                                    # frma is a FullBox-like: after header, 4 bytes = original format
                                    original_format = bytes(buf[frma_off + frma_hdr:frma_off + frma_hdr + 4])
                                
                                # Zero out the sinf box (replace with free box)
                                buf[sinf_off+4:sinf_off+8] = b'free'
                                # Zero out sinf contents
                                for i in range(sinf_off + 8, sinf_off + sinf_total):
                                    if i < len(buf):
                                        buf[i] = 0
                            
                            # Patch the entry type from enc* to original format
                            logger.info(f"[Amazon Decrypt] Patching {entry_type} -> {original_format} at offset {entry_off}")
                            buf[entry_off+4:entry_off+8] = original_format[:4]
                        
                        entry_off += entry_total
            
            # Apply stsd patches
            patch_encrypted_stsd(data)
            
            # === STEP 2: Decrypt mdat samples in-place ===
            decrypted_samples = 0
            
            for moof_type, moof_hdr, moof_total, moof_off in iter_boxes(data, 0, len(data)):
                if moof_type != b'moof':
                    continue
                
                moof_body_start = moof_off + moof_hdr
                moof_body_end = moof_off + moof_total
                
                # Find traf inside moof
                traf_info = find_box(data, moof_body_start, moof_body_end, b'traf')
                if not traf_info:
                    continue
                traf_off, traf_hdr, traf_total = traf_info
                traf_body_start = traf_off + traf_hdr
                traf_body_end = traf_off + traf_total
                
                # Parse tfhd for default sample size
                tfhd_info = find_box(data, traf_body_start, traf_body_end, b'tfhd')
                default_sample_size = 0
                if tfhd_info:
                    default_sample_size = parse_tfhd(data, *tfhd_info)
                
                # Parse trun
                trun_info = find_box(data, traf_body_start, traf_body_end, b'trun')
                if not trun_info:
                    continue
                sample_sizes, data_offset = parse_trun(data, *trun_info)
                
                # Fill in default sample sizes if trun didn't have them
                if default_sample_size:
                    sample_sizes = [s if s else default_sample_size for s in sample_sizes]
                
                # Parse senc
                senc_info = find_box(data, traf_body_start, traf_body_end, b'senc')
                if not senc_info:
                    continue
                senc_samples = parse_senc(data, *senc_info)
                
                if len(senc_samples) != len(sample_sizes):
                    logger.warning(f"[Amazon Decrypt] Sample count mismatch: trun={len(sample_sizes)} senc={len(senc_samples)}")
                    continue
                
                # Find the mdat that follows this moof
                mdat_off = moof_off + moof_total
                if mdat_off + 8 > len(data):
                    continue
                mdat_type, mdat_hdr, mdat_total = get_box_info(data, mdat_off)
                if mdat_type != b'mdat':
                    continue
                
                mdat_data_start = mdat_off + mdat_hdr
                
                # Decrypt each sample
                sample_pos = mdat_data_start
                for i, (sample_size) in enumerate(sample_sizes):
                    if i >= len(senc_samples):
                        break
                    iv, subs = senc_samples[i]
                    
                    if not subs:
                        # Full-sample encryption
                        if sample_size > 0 and sample_pos + sample_size <= len(data):
                            cipher = make_cipher(key, iv)
                            encrypted_chunk = bytes(data[sample_pos:sample_pos + sample_size])
                            decrypted_chunk = cipher.decrypt(encrypted_chunk)
                            data[sample_pos:sample_pos + sample_size] = decrypted_chunk
                            decrypted_samples += 1
                    else:
                        # Subsample encryption
                        offset_in_sample = 0
                        cipher = make_cipher(key, iv)
                        for clear_bytes, enc_bytes in subs:
                            offset_in_sample += clear_bytes  # skip clear bytes
                            if enc_bytes > 0:
                                abs_pos = sample_pos + offset_in_sample
                                if abs_pos + enc_bytes <= len(data):
                                    encrypted_chunk = bytes(data[abs_pos:abs_pos + enc_bytes])
                                    decrypted_chunk = cipher.decrypt(encrypted_chunk)
                                    data[abs_pos:abs_pos + enc_bytes] = decrypted_chunk
                                offset_in_sample += enc_bytes
                        decrypted_samples += 1
                    
                    sample_pos += sample_size
            
            logger.info(f"[Amazon Decrypt] Decrypted {decrypted_samples} samples, output {len(data)} bytes")
            return bytes(data)
            
        except Exception as e:
            logger.error(f"[Amazon Decrypt] Error: {e}", exc_info=True)
            return None
    
    decrypted = await run_in_threadpool(_decrypt_cenc_inplace)
    if not decrypted:
        return RedirectResponse(stream_url)
    
    # --- TRANSCODAGE EN FLAC NATIF VIA FFMPEG ---
    final_content = decrypted
    media_type = 'audio/mp4' # Par défaut (MP4 Lossless)
    
    try:
        # On remux / convertit le MP4 décrypté en flux FLAC pur
        process = subprocess.Popen(
            ['ffmpeg', '-i', 'pipe:0', '-f', 'flac', 'pipe:1'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        out, err = process.communicate(input=decrypted)
        if process.returncode == 0 and out:
            final_content = out
            media_type = 'audio/flac'
            logger.info(f"[Amazon FLAC] Transcodage réussi : {len(decrypted)} -> {len(out)} bytes")
        else:
            logger.error(f"[Amazon FLAC] FFmpeg error: {err.decode()}")
    except Exception as e:
        logger.error(f"[Amazon FLAC] Transcoding exception: {e}")

    return Response(
        content=final_content,
        media_type=media_type,
        headers={
            'Content-Length': str(len(final_content)),
            'Accept-Ranges': 'bytes',
            'Cache-Control': 'public, max-age=3600',
            'Access-Control-Allow-Origin': '*'
        }
    )

@app.get('/amazon_stream_info/{asin}')
async def get_amazon_stream_info_route(asin: str):
    """Endpoint pour récupérer les infos complètes du stream Amazon Music (URL + metadata)."""
    stream_data = await run_in_threadpool(get_amazon_stream_url, asin)
    if stream_data:
        return JSONResponse(stream_data)
    raise HTTPException(404, "Amazon Music stream not found")

# --- TIDAL (lecture via hifi-api : manifeste DASH FLAC non chiffré + proxy segments) ---
_tidal_mpd_cache: dict = {}   # track_id -> {'mpd', 'ts'}
TIDAL_MPD_TTL = 15 * 60

def _tidal_resolve_mpd(track_id):
    c = _tidal_mpd_cache.get(track_id)
    if c and (time.time() - c['ts']) < TIDAL_MPD_TTL:
        return c['mpd']
    try:
        r = requests.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/track/",
                         params={'id': track_id, 'quality': 'HI_RES_LOSSLESS'},
                         headers=_tidal_headers(), timeout=20)
        if r.status_code != 200:
            logger.warning(f"[Tidal] track {track_id} -> {r.status_code}: {r.text[:120]}")
            return None
        d = (r.json() or {}).get('data') or {}
        if 'dash' not in (d.get('manifestMimeType') or ''):
            logger.info(f"[Tidal] {track_id}: manifest {d.get('manifestMimeType')} non géré")
            return None
        mpd = base64.b64decode(d.get('manifest', '')).decode('utf-8', 'replace')
        _tidal_mpd_cache[track_id] = {'mpd': mpd, 'ts': time.time()}
        return mpd
    except Exception as e:
        logger.error(f"[Tidal] resolve {track_id}: {e}")
        return None

@app.get('/tidal_manifest/{track_id}')
async def get_tidal_manifest_route(track_id: str):
    """Manifeste DASH Tidal (FLAC non chiffré) pour Shaka. Les segments passent par /tidal_proxy."""
    mpd = await run_in_threadpool(_tidal_resolve_mpd, track_id)
    if not mpd:
        raise HTTPException(404, "Tidal stream not found")
    b64 = base64.b64encode(mpd.encode('utf-8')).decode('ascii')
    return JSONResponse({'mimeType': 'application/dash+xml', 'manifest': b64})

@app.get('/tidal_proxy')
async def tidal_proxy(url: str, request: Request):
    """Proxy par plages des segments audio Tidal (CDN sans CORS). Restreint au CDN Tidal."""
    host = (urllib.parse.urlparse(url).hostname or '').lower()
    if not (host.endswith('.audio.tidal.com') or host.endswith('.tidal.com')):
        raise HTTPException(403, "host not allowed")
    rng = request.headers.get('range') or request.headers.get('Range') or 'bytes=0-'

    def _fetch():
        return requests.get(url, headers={'User-Agent': SQUID_UA, 'Range': rng}, timeout=40)

    r = await run_in_threadpool(_fetch)
    headers = {
        'Accept-Ranges': 'bytes',
        'Content-Length': str(len(r.content)),
        'Cache-Control': 'no-store',
        'Access-Control-Allow-Origin': '*',
    }
    cr = r.headers.get('Content-Range')
    if cr:
        headers['Content-Range'] = cr
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get('Content-Type', 'audio/mp4'), headers=headers)

@app.get('/lyrics')
async def get_lyrics(artist: str, title: str, album: str = None, duration: str = None):
    yt_res = await run_in_threadpool(sync_search_yt_lyrics, title, artist)
    if yt_res and yt_res['type'] == 'synced': return JSONResponse({"type": "synced", "lyrics": yt_res['lyrics'], "source": "YouTube"})
    dur = parse_duration(duration)
    lrc_plain, lrc_synced = None, None
    try: lrc_plain, lrc_synced = await run_in_threadpool(lyrics_engine.search_lyrics, artist, title, album, dur)
    except Exception: pass
    if lrc_synced: return JSONResponse({"type": "synced", "lyrics": lrc_synced, "source": "LRCLib"})
    if yt_res and yt_res['type'] == 'plain': return JSONResponse({"type": "plain", "lyrics": yt_res['lyrics'], "source": "YouTube"})
    if lrc_plain: return JSONResponse({"type": "plain", "lyrics": lrc_plain, "source": "LRCLib"})
    raise HTTPException(404, "No lyrics")

# --- STATIC FILES ---
app.mount("/", StaticFiles(directory=PROJECT_ROOT, html=True), name="static")