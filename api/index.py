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

# Variables locales (.env) en développement ; en ligne (Vercel) elles viennent du tableau de bord
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(PROJECT_ROOT, '.env'))
except Exception:
    pass

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import JSONResponse, RedirectResponse, FileResponse, StreamingResponse
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

# Session HTTP partagée : garde les connexions TCP/TLS ouvertes (keep-alive) au lieu de
# refaire un handshake à chaque appel (~200 ms économisés par requête). Utilisée pour tous
# les appels chauds (Tidal manifest/proxy/recherche).
HTTP = requests.Session()
_http_adapter = requests.adapters.HTTPAdapter(
    pool_connections=50, pool_maxsize=100, max_retries=0, pool_block=False)
HTTP.mount('http://', _http_adapter)
HTTP.mount('https://', _http_adapter)

# --- CLIENT ASYNCHRONE (chemin audio) ---
# Le proxy audio est le point le plus sollicité : en async, une requête ne monopolise
# plus un thread (le serveur peut en tenir des centaines en parallèle) et les octets sont
# relayés AU FIL DE L'EAU (streaming) au lieu d'être chargés en mémoire.
# HTTP/2 si disponible : tous les segments passent sur UNE seule connexion (multiplexage).
try:
    import httpx as _httpx
    try:
        import h2 as _h2  # noqa: F401
        _HTTP2_OK = True
    except ImportError:
        _HTTP2_OK = False
    AHTTP = _httpx.AsyncClient(
        http2=_HTTP2_OK,
        timeout=_httpx.Timeout(20.0, connect=5.0),
        limits=_httpx.Limits(max_connections=200, max_keepalive_connections=100,
                             keepalive_expiry=120.0),
        follow_redirects=True,
    )
    ASYNC_HTTP_OK = True
except Exception:
    AHTTP = None
    _HTTP2_OK = False
    ASYNC_HTTP_OK = False

# Tentative d'import pour rapidfuzz
try:
    from rapidfuzz import fuzz
    FUZZ_AVAILABLE = True
except ImportError:
    FUZZ_AVAILABLE = False

# --- CONFIGURATION (chargée depuis .env ou variables d'environnement) ---
# Identifiants Qobuz officiels. Le token est lié à l'app_id : app_id + secret + token
# doivent être cohérents. Le token et l'ID utilisateur donnent accès au compte : ils viennent
# UNIQUEMENT de l'environnement (.env en local, tableau de bord Vercel en ligne).
APP_VERSION  = "2026-10-25 · Qobuz prioritaire, Tidal en secours"

def _env_clean(name):
    return (os.getenv(name) or '').strip().strip('"').strip("'").strip()

USER_ID      = _env_clean('QOBUZ_USER_ID')
TOKEN        = _env_clean('QOBUZ_TOKEN')
APP_ID       = os.getenv('QOBUZ_APP_ID') or '798273057'
QOBUZ_SECRET = os.getenv('QOBUZ_SECRET') or 'abb21364945c0583309667d13ca3d93a'

TIDAL_HUND_BASE = "https://api.monochrome.tf"

TIDAL_HUND_BASE = "https://api.monochrome.tf"

# API Tidal hifi-api (recherche + lecture). Instance auto-hébergée (HTTP → appelée
# uniquement côté serveur ; l'audio est proxifié, le navigateur n'y touche jamais).
TIDAL_HIFI_BASE = os.getenv('TIDAL_HIFI_BASE', "http://k9zejj70jg59p0qeo95lzpel.46.224.72.133.sslip.io")
TIDAL_HIFI_KEY = os.getenv('TIDAL_HIFI_KEY', "")
# Qualité de lecture Tidal : 24 bits hi-res par défaut.
TIDAL_QUALITY = os.getenv('TIDAL_QUALITY', 'HI_RES_LOSSLESS')

# --- ADDON ELJOHNZC (source principale : recherche + lecture Tidal) ---
# Remplace l'ancienne hifi-api (qui ne renvoie plus que des extraits de 30 s) et Qobuz (hors
# service). /search?q= → pistes (ID Tidal) ; /stream/{id}?quality= → URL d'un manifeste DASH
# non chiffré dont les segments sont sur *.audio.tidal.com (relayés par /tidal_proxy).
ELJOHNZC_BASE = (os.getenv('ELJOHNZC_BASE') or "https://eljohnzcaddon.velixir.run/c57e7b0ef2031b2f1fcd").rstrip('/')
# Qualité demandée à l'addon : hires | lossless | high | low (il descend tout seul si indisponible).
ELJOHNZC_QUALITY = os.getenv('ELJOHNZC_QUALITY') or 'hires'

# MODE LOSSLESS (par défaut) : titres, albums et artistes viennent de Qobuz (si configuré) et de
# Tidal, seules les playlists viennent de Deezer ; tout titre d'une autre source est re-cherché sur
# Qobuz puis Tidal à la lecture (sans repli Deezer). LOSSLESS_MODE=0 réactive l'ancien mélange.
LOSSLESS_MODE = os.getenv('LOSSLESS_MODE', '1') == '1'

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
# Qobuz actif dès qu'un token est fourni (QOBUZ_ENABLED=0 pour le couper).
QOBUZ_ENABLED = os.getenv('QOBUZ_ENABLED', '1' if TOKEN else '0') == '1'   # Qobuz : recherche + audio
QOBUZ_OFFICIAL_ENABLED = QOBUZ_ENABLED  # API Qobuz officielle (token+secret)
# Miroir kennyy : hors service (≈ 20 s avant d'échouer) → coupé par défaut, réactivable
QOBUZ_ALT_ENABLED = os.getenv('QOBUZ_ALT_ENABLED', '0') == '1'

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

# Compression GZip : les réponses JSON (recherche, playlists, albums) sont très
# compressibles (~-75 %) → transfert bien plus rapide, surtout en mobile/4G.
# minimum_size évite de compresser les petites réponses (coût CPU inutile).
try:
    from starlette.middleware.gzip import GZipMiddleware
    app.add_middleware(GZipMiddleware, minimum_size=800, compresslevel=5)
except Exception as _e:
    pass

# Le pool de threads d'AnyIO (utilisé par run_in_threadpool) est limité à 40 par défaut :
# c'est lui qui sert TOUS les appels bloquants (proxy audio, recherches, résolutions).
# On l'élargit pour éviter que les segments audio attendent derrière les recherches.
@app.on_event("startup")
async def _tune_threadpool():
    try:
        import anyio
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = 120
        logger.info(f"[Perf] Threadpool élargi à {limiter.total_tokens} threads")
    except Exception as e:
        logger.warning(f"[Perf] Threadpool non ajusté: {e}")
    logger.info(f"[Perf] Client async: {ASYNC_HTTP_OK} | HTTP/2: {_HTTP2_OK}")

@app.on_event("shutdown")
async def _close_async_client():
    try:
        if AHTTP is not None:
            await AHTTP.aclose()
    except Exception:
        pass

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
            r = HTTP.post(
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
        'performer': {'name': (t.get('artist') or (t.get('artists') or [{}])[0] or {}).get('name', 'Inconnu')},
        'album': {'title': alb.get('title'), 'image': {'large': tidal_uuid_to_url(alb.get('cover'))}},
        'duration': t.get('duration', 0),
        'isrc': t.get('isrc'),
        'maximum_bit_depth': bd,
        'maximum_sampling_rate': sr,
        'source': 'tidal_hund',
    }

def _addon_track_obj(t):
    """Mappe une piste de l'addon Eljohnzc → objet 'tidal_hund' (l'id est un ID Tidal)."""
    if not t or not t.get('id') or not t.get('title'):
        return None
    hires = bool(t.get('hires')) or t.get('audioQuality') == 'HI_RES_LOSSLESS'
    return {
        'id': str(t['id']),
        'title': t.get('title'),
        'performer': {'name': t.get('artist') or 'Inconnu'},
        'album': {'title': t.get('album'),
                  'image': {'large': t.get('artworkURL') or tidal_uuid_to_url(None)}},
        'duration': t.get('duration', 0),
        'maximum_bit_depth': 24 if hires else 16,
        'maximum_sampling_rate': 44.1,  # l'addon n'expose pas le sr en recherche
        'quality_label': t.get('quality'),
        'source': 'tidal_hund',
    }

def sync_search_tidal(query, limit=25):
    """Recherche Tidal via l'addon Eljohnzc → objets 'tidal_hund'. Lecture via /tidal_manifest.
    L'addon renvoie au plus 25 titres (pas de pagination). S'il ne répond pas, repli sur l'API
    Tidal de métadonnées : même catalogue, mêmes ID, donc lisibles dès que l'addon revient."""
    items = []
    try:
        r = HTTP.get(f"{ELJOHNZC_BASE}/search", params={'q': query}, timeout=12)
        if r.status_code == 200:
            items = (r.json() or {}).get('tracks', []) or []
        else:
            logger.warning(f"[Tidal Search] HTTP {r.status_code}")
    except Exception as e:
        logger.error(f"[Tidal Search] {e}")
    out = []
    for t in items:
        obj = _addon_track_obj(t)
        if obj:
            out.append(obj)
        if len(out) >= limit:
            break
    return out or _hifi_search_tracks(query, limit)

def _hifi_search_tracks(query, limit=25):
    """Recherche de titres sur l'API Tidal de métadonnées (repli de l'addon)."""
    try:
        r = HTTP.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/search/", params={'s': query, 'limit': limit},
                     headers=_tidal_headers(), timeout=12)
        items = (r.json().get('data') or {}).get('items', []) or []
    except Exception as e:
        logger.error(f"[Tidal Search hifi] {e}")
        return []
    return [o for o in (_tidal_track_obj(t) for t in items) if o][:limit]

# --- CACHE DE RÉSOLUTION (clé = isrc, sinon titre|artiste) ---
# Une piste déjà résolue (favori rejoué, radio, retour arrière, autre utilisateur…) est
# renvoyée instantanément sans aucun appel réseau. Le cache négatif évite de refaire
# 4 appels pour un titre qu'on sait introuvable.
# Pool partagé pour les résolutions : permet de lancer 2 recherches en parallèle et de
# RENDRE LA MAIN dès que la meilleure (ISRC) a répondu, sans attendre l'autre.
_RESOLVE_POOL = ThreadPoolExecutor(max_workers=12, thread_name_prefix='resolve')

_resolve_cache: dict = {}          # key -> (result_or_None, ts)
RESOLVE_CACHE_TTL = 0   # cache désactivé (résultats toujours frais)
RESOLVE_NEG_TTL = 0     # cache négatif désactivé
RESOLVE_CACHE_MAX = 500
_resolve_locks: dict = {}
_resolve_locks_guard = threading.Lock()

def _resolve_key(title, artist, isrc):
    if isrc:
        return f"isrc:{isrc.upper()}"
    return f"ta:{clean_string(title or '')}|{clean_string(artist or '')}"

def _resolve_cache_get(key):
    hit = _resolve_cache.get(key)
    if not hit:
        return (False, None)
    res, ts = hit
    ttl = RESOLVE_CACHE_TTL if res else RESOLVE_NEG_TTL
    if (time.time() - ts) < ttl:
        return (True, res)
    return (False, None)

def _resolve_cache_put(key, res):
    if len(_resolve_cache) >= RESOLVE_CACHE_MAX:
        try:
            del _resolve_cache[min(_resolve_cache, key=lambda k: _resolve_cache[k][1])]
        except Exception:
            _resolve_cache.clear()
    _resolve_cache[key] = (res, time.time())

def _match_str(s):
    """clean_string, mais garde le texte brut (minuscules) s'il n'a aucun caractère latin
    (japonais, arabe…) — sinon tout titre non latin deviendrait '' et matcherait n'importe quoi."""
    return clean_string(s) or re.sub(r'\s+', '', str(s or '')).lower()

def _base_title(s):
    """Titre sans les mentions entre parenthèses/crochets ni le suffixe ' - …' (version, feat.)."""
    s = re.sub(r'\s*[\(\[].*?[\)\]]', '', s or '')
    s = re.sub(r'\s+-\s+.*', '', s)
    return _match_str(s)

def _ratio(a, b):
    if FUZZ_AVAILABLE:
        return fuzz.ratio(a, b)
    return difflib.SequenceMatcher(None, a, b).ratio() * 100

def _artist_forms(name):
    """Formes comparables d'un artiste : nom complet + artiste principal (« Bob Marley & The
    Wailers » → « Bob Marley », « Daft Punk, Pharrell » → « Daft Punk »)."""
    return {_match_str(name), _match_str(_primary_artist(name)),
            _match_str(_primary_artist(name, aggressive=True))}

# Variantes à éviter si le titre cherché ne les mentionne pas (sinon un remix ou une
# reprise karaoké peut passer devant l'original)
_VARIANT_RE = re.compile(r'\b(remix|live|karaoke|instrumental|cover|acoustic|edit|version|mix|'
                         r'tribute|medley|sped up|slowed|nightcore|drumless|made popular|'
                         r'originally performed)\b', re.IGNORECASE)

def _tidal_pick_by_ta(title, artist):
    """Recherche addon par titre+artiste (l'addon n'a pas de recherche ISRC)."""
    return _best_match(sync_search_tidal(f"{title} {_primary_artist(artist)}".strip(), 25), title, artist)

def _best_match(candidates, title, artist):
    """Meilleur résultat titre+artiste (correspondance floue) ; à égalité, l'ordre de pertinence
    de la source départage. None si rien ne correspond vraiment."""
    t_artists = _artist_forms(artist)
    t_full = _match_str(title); t_base = _base_title(title)
    t_variants = {v.lower() for v in _VARIANT_RE.findall(title or '')}
    best, best_score = None, 0
    for rec in candidates:
        r_artists = _artist_forms((rec.get('performer') or {}).get('name') or '')
        a_score = max(_ratio(a, b) for a in t_artists for b in r_artists)
        if a_score < 75:  # « Daft Punk » ≠ « Daft Punk Experience » (61)
            continue
        t_score = _ratio(t_base, _base_title(rec['title']))
        if t_score < 70:
            continue
        score = (a_score + t_score) / 2
        if _match_str(rec['title']) == t_full:
            score += 5
        extra = {v.lower() for v in _VARIANT_RE.findall(rec['title'])} - t_variants
        score -= 10 * len(extra)
        if score > best_score:
            best, best_score = rec, score
    return best

def _tidal_resolve_one(title, artist, isrc=None):
    """Cherche un titre sur Tidal via l'addon → 'tidal_hund'. Sans titre/artiste, l'ISRC est
    d'abord converti en titre+artiste via Deezer. Résultat mis en cache (instantané ensuite)."""
    key = 'tidal:' + _resolve_key(title, artist, isrc)
    found, cached = _resolve_cache_get(key)
    if found:
        return cached
    with _resolve_locks_guard:
        lock = _resolve_locks.setdefault(key, threading.Lock())
    with lock:
        found, cached = _resolve_cache_get(key)
        if found:
            return cached
        if not (title and artist) and isrc:
            d = _deezer_api(f"https://api.deezer.com/2.0/track/isrc:{isrc}")
            if d and 'error' not in d:
                title = d.get('title_short') or d.get('title')
                artist = (d.get('artist') or {}).get('name')
        res = None
        if title and artist:
            res = _tidal_pick_by_ta(title, artist)
            if res and isrc:
                res['isrc'] = isrc
        _resolve_cache_put(key, res)
        return res

def _tidal_native_radio(title, artist, limit=25):
    """Radio native Tidal (recommandations de la piste seed). Repli si YouTube échoue."""
    try:
        r = HTTP.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/search/",
                         params={'s': f"{title} {artist}".strip(), 'limit': 1},
                         headers=_tidal_headers(), timeout=12)
        items = (r.json().get('data') or {}).get('items', []) or []
        if not items:
            return []
        seed_id = items[0].get('id')
        rr = HTTP.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/recommendations/",
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
        
    #     res = HTTP.get(url, headers=headers, params=params, timeout=10)
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

def tidal_artist_pic(uuid):
    """Photo d'artiste Tidal (format carré 750 px, différent des pochettes)."""
    if not uuid:
        return ''
    return f"https://resources.tidal.com/images/{uuid.replace('-', '/')}/750x750.jpg"

def sync_search_tidal_artists(query, limit=15):
    """Recherche d'artistes Tidal (?a=) → objets artiste 'tidal_hund'."""
    try:
        r = HTTP.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/search/",
                     params={'a': query, 'limit': limit}, headers=_tidal_headers(), timeout=12)
        items = ((r.json().get('data') or {}).get('artists') or {}).get('items') or []
    except Exception as e:
        logger.error(f"[Tidal Artists] {e}")
        return []
    out = []
    for a in items:
        if not a.get('id') or not a.get('name'):
            continue
        image = tidal_artist_pic(a.get('picture')) or (tidal_uuid_to_url(a['selectedAlbumCoverFallback']) if a.get('selectedAlbumCoverFallback') else '')
        out.append({'id': str(a['id']), 'name': a['name'], 'image': image, 'source': 'tidal_hund', 'type': 'artist'})
        if len(out) >= limit:
            break
    return out

def _tidal_find_artist_id(name):
    """ID Tidal de l'artiste portant ce nom (correspondance quasi exacte), ou None."""
    if not name:
        return None
    wanted = _artist_forms(name)
    for a in sync_search_tidal_artists(name, 10):
        if max(_ratio(w, _match_str(a['name'])) for w in wanted) >= 90:
            return a['id']
    return None

def sync_get_tidal_artist(artist_id):
    """Fiche artiste Tidal : photo, titres populaires (par popularité) et discographie."""
    base = TIDAL_HIFI_BASE.rstrip('/')
    f_info = _RESOLVE_POOL.submit(lambda: HTTP.get(f"{base}/artist/", params={'id': artist_id}, headers=_tidal_headers(), timeout=12).json())
    f_full = _RESOLVE_POOL.submit(lambda: HTTP.get(f"{base}/artist/", params={'f': artist_id}, headers=_tidal_headers(), timeout=20).json())
    try:
        info = (f_info.result() or {}).get('artist') or {}
        full = f_full.result() or {}
    except Exception as e:
        logger.error(f"[Tidal Artist] {artist_id}: {e}")
        return None
    if not info.get('name'):
        return None

    # Titres populaires : les plus écoutés, sans doublons (rééditions, versions alternatives)
    top, seen = [], set()
    for t in sorted(full.get('tracks') or [], key=lambda x: x.get('popularity') or 0, reverse=True):
        sig = _base_title(t.get('title'))
        if not sig or sig in seen:
            continue
        obj = _tidal_track_obj(t)
        if obj:
            top.append(obj); seen.add(sig)
        if len(top) >= 10:
            break

    # Discographie : de la plus récente à la plus ancienne, sans doublons de titre
    albums, seen = [], set()
    for a in sorted((full.get('albums') or {}).get('items') or [], key=lambda x: x.get('releaseDate') or '', reverse=True):
        sig = _match_str(a.get('title'))
        if not a.get('id') or sig in seen:
            continue
        seen.add(sig)
        tags = (a.get('mediaMetadata') or {}).get('tags') or []
        albums.append({
            'id': str(a['id']),
            'title': a.get('title'),
            'artist': {'name': info['name']},
            'image': {'large': tidal_uuid_to_url(a.get('cover'))},
            'release_date_original': a.get('releaseDate'),
            'maximum_bit_depth': 24 if ('HIRES_LOSSLESS' in tags or 'MQA' in tags) else 16,
            'source': 'tidal_hund',
            'type': 'album',
        })

    return {
        'id': str(info.get('id') or artist_id),
        'name': info['name'],
        'image': tidal_artist_pic(info.get('picture')),
        'albums': albums,
        'top_tracks': top,
        'bio': '',
        'source': 'tidal_hund',
    }

def sync_search_tidal_albums(query, limit=15):
    """Recherche d'albums Tidal via hifi-api (?al=) → objets album 'tidal_hund'."""
    try:
        r = HTTP.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/search/",
                         params={'al': query, 'limit': limit}, headers=_tidal_headers(), timeout=12)
        albs = ((r.json().get('data') or {}).get('albums') or {}).get('items') or []
    except Exception as e:
        logger.error(f"[Tidal Albums] {e}")
        return []
    out = []
    for a in albs:
        if not a.get('id') or not a.get('title'):
            continue
        artist = (a.get('artist') or {}).get('name') or ((a.get('artists') or [{}])[0] or {}).get('name') or 'Inconnu'
        tags = (a.get('mediaMetadata') or {}).get('tags') or []
        out.append({
            'id': str(a['id']),
            'title': a.get('title'),
            'artist': {'name': artist},
            'image': {'large': tidal_uuid_to_url(a.get('cover'))},
            'source': 'tidal_hund',
            'type': 'album',
            'maximum_bit_depth': 24 if ('HIRES_LOSSLESS' in tags or 'MQA' in tags) else 16,
            'date': a.get('releaseDate') or a.get('streamStartDate'),
        })
        if len(out) >= limit:
            break
    return out

def sync_get_tidal_album(album_id):
    """Détails d'un album Tidal via hifi-api (/album/?id=) → pistes 'tidal_hund'."""
    try:
        r = HTTP.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/album/",
                         params={'id': album_id, 'limit': 250}, headers=_tidal_headers(), timeout=20)
        d = (r.json() or {}).get('data') or {}
        if not d.get('id'):
            return None
        cover = tidal_uuid_to_url(d.get('cover'))
        tracks = []
        for it in d.get('items', []):
            t = it.get('item') or it
            obj = _tidal_track_obj(t)
            if not obj:
                continue
            if not obj['album']['image']['large'] or 'placehold' in obj['album']['image']['large']:
                obj['album']['image']['large'] = cover
            obj['track_number'] = t.get('trackNumber')
            tracks.append(obj)
        tags = (d.get('mediaMetadata') or {}).get('tags') or []
        return {
            'id': str(d.get('id')),
            'title': d.get('title'),
            'artist': {'name': (d.get('artist') or {}).get('name', 'Inconnu')},
            'image': {'large': cover},
            'source': 'tidal_hund',
            'maximum_bit_depth': 24 if ('HIRES_LOSSLESS' in tags or 'MQA' in tags) else 16,
            'tracks': {'items': tracks},
        }
    except Exception as e:
        logger.error(f"[Tidal Album] {e}")
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
        res = HTTP.get(url, params=params, timeout=15)
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
        res = HTTP.get(url, params=params, timeout=15)
        
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
        res = HTTP.get(url, params=params, timeout=15)
        
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
        res = HTTP.get(url, params=params, timeout=15)
        
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
        r = HTTP.get(url, params=params, timeout=5)
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
        r = HTTP.get("https://api.deezer.com/search",
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
        r = HTTP.get(
            f'https://rss.marketingtools.apple.com/api/v2/{cc}/music/most-played/{limit}/songs.json',
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
        r = HTTP.get(f"{AMZ_MIRROR_BASE}/api/track/{asin}",
                         headers={'X-API-Key': AMZ_MIRROR_KEY, 'User-Agent': SQUID_UA}, timeout=25)
        if r.status_code == 200:
            d = r.json()
            if d.get('streamUrl') and d.get('decryptionKey'):
                return d['streamUrl'], d['decryptionKey'].strip()
    except Exception as e:
        logger.warning(f"[Amazon] mirror {asin}: {e}")
    try:
        r = HTTP.get(f"{AMZ_ZARZ_BASE}/media", params={'asin': asin, 'codec': 'flac'},
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
        r = HTTP.get(AMZ_MUSIC_BASE + "/config.json",
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
        r = HTTP.post(AMZ_SKILL_BASE + "/showSearch",
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
        r = HTTP.get(url, params=params, timeout=5)
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
        r = HTTP.get(url, params=params, timeout=5)
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
        r = HTTP.get(url, params=params, timeout=5)
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

def sync_qobuz_search_all(query, limit_tracks=50, limit_albums=15, limit_artists=10):
    """UN appel Qobuz (catalog/search) → (titres, albums, artistes)."""
    if not (QOBUZ_ENABLED and client):
        return [], [], []
    try:
        r = client.api_call("catalog/search", query=query, limit=max(limit_tracks, limit_albums, 20), offset=0)
    except Exception as e:
        logger.error(f"[Qobuz Search all] {e}")
        return [], [], []
    tracks = (r.get('tracks') or {}).get('items', [])[:limit_tracks]
    albums = (r.get('albums') or {}).get('items', [])[:limit_albums]
    for it in tracks:
        it['source'] = 'qobuz'; fix_qobuz_title(it)
        it['date'] = it.get('release_date_original') or it.get('released_at')
    for it in albums:
        it['source'] = 'qobuz'
        it['date'] = it.get('release_date_original') or it.get('released_at')
    artists = []
    for a in (r.get('artists') or {}).get('items', [])[:limit_artists]:
        if a.get('id') and a.get('name'):
            img = a.get('image') or {}
            pic = img.get('large') or img.get('medium') or a.get('picture') or ''
            artists.append({'id': str(a['id']), 'name': a['name'], 'image': pic, 'source': 'qobuz', 'type': 'artist'})
    return tracks, albums, artists

def _track_sig(t):
    """Signature d'un titre pour repérer le même morceau sur deux sources (« feat. » ignoré,
    mais pas remix / live / version, qui restent des titres différents)."""
    title = re.sub(r'\s*[\(\[](?:feat|ft|with)\b[^\)\]]*[\)\]]', '', t.get('title') or '', flags=re.I)
    title = re.sub(r'\s+(?:-\s+)?(?:feat|ft)\.?\s.*$', '', title, flags=re.I)
    artist = (t.get('performer') or t.get('artist') or {}).get('name', '')
    return f"{_match_str(title)}|{_match_str(_primary_artist(artist, aggressive=True))}"

def _album_sig(a):
    return f"{_match_str(a.get('title'))}|{_match_str((a.get('artist') or {}).get('name'))}"

def _merge_lossless(qobuz_items, tidal_items, sig):
    """Fusionne sans doublon, Qobuz en priorité : Tidal ne fait que compléter avec ce que Qobuz
    n'a pas. Entre deux versions d'une même source, la meilleure profondeur de bits l'emporte.
    L'ordre de pertinence est conservé (Qobuz d'abord)."""
    best, order = {}, []
    for it in list(qobuz_items) + list(tidal_items):
        s_ = sig(it)
        cur = best.get(s_)
        if cur is None:
            best[s_] = it; order.append(s_)
        elif cur.get('source') == it.get('source') and (it.get('maximum_bit_depth') or 16) > (cur.get('maximum_bit_depth') or 16):
            best[s_] = it
    return [best[k] for k in order]

def _qobuz_track_from_page(t):
    """Titre de la fiche artiste Qobuz (artist/page) → objet titre du site."""
    alb = t.get('album') or {}
    title = t.get('title') or ''
    if t.get('version') and t['version'].lower() not in title.lower():
        title = f"{title} ({t['version']})"
    ai = t.get('audio_info') or {}
    return {
        'id': str(t['id']), 'title': title,
        'performer': {'name': ((t.get('artist') or {}).get('name') or {}).get('display') or 'Inconnu'},
        'album': {'title': alb.get('title'), 'image': {'large': (alb.get('image') or {}).get('large')}},
        'duration': t.get('duration', 0), 'isrc': t.get('isrc'),
        'maximum_bit_depth': ai.get('maximum_bit_depth') or 16,
        'maximum_sampling_rate': ai.get('maximum_sampling_rate') or 44.1,
        'source': 'qobuz',
    }

def _qobuz_find_artist_id(name):
    """ID Qobuz de l'artiste portant ce nom (correspondance quasi exacte), ou None."""
    if not (QOBUZ_ENABLED and client and name):
        return None
    try:
        items = (client.api_call("artist/search", query=name, limit=10).get('artists') or {}).get('items') or []
    except Exception as e:
        logger.warning(f"[Qobuz Artist search] {e}")
        return None
    wanted = _artist_forms(name)
    for a in items:
        if a.get('id') and max(_ratio(w, _match_str(a.get('name'))) for w in wanted) >= 90:
            return str(a['id'])
    return None

def sync_get_qobuz_artist(artist_id):
    """Fiche artiste Qobuz (artist/page) : portrait, bio, titres populaires et discographie."""
    if not (QOBUZ_ENABLED and client):
        return None
    try:
        d = client.api_call("artist/page", artist_id=artist_id)
    except Exception as e:
        logger.warning(f"[Qobuz Artist] {artist_id}: {e}")
        return None
    name = ((d.get('name') or {}).get('display') if isinstance(d.get('name'), dict) else d.get('name')) or ''
    if not name:
        return None
    portrait = ((d.get('images') or {}).get('portrait') or {}).get('hash')
    image = f"https://static.qobuz.com/images/artists/covers/large/{portrait}.jpg" if portrait else ''

    top, seen = [], set()
    for t in d.get('top_tracks') or []:
        if not t.get('id'):
            continue
        obj = _qobuz_track_from_page(t)
        sig = _track_sig(obj)
        if sig in seen:
            continue
        seen.add(sig); top.append(obj)
        if len(top) >= 10:
            break

    # Discographie : albums, puis EP/singles, live et compilations (récents d'abord dans chaque groupe)
    groups = {r.get('type'): r.get('items') or [] for r in d.get('releases') or [] if isinstance(r, dict)}
    albums, seen = [], set()
    for kind in ('album', 'epSingle', 'live', 'compilation'):
        for a in sorted(groups.get(kind, []), key=lambda x: (x.get('dates') or {}).get('original') or '', reverse=True):
            if not a.get('id') or a['id'] in seen:
                continue
            seen.add(a['id'])
            title = a.get('title') or ''
            if a.get('version') and a['version'].lower() not in title.lower():
                title = f"{title} ({a['version']})"
            albums.append({
                'id': str(a['id']), 'title': title,
                'artist': {'name': name},
                'image': {'large': (a.get('image') or {}).get('large')},
                'release_date_original': (a.get('dates') or {}).get('original'),
                'maximum_bit_depth': (a.get('audio_info') or {}).get('maximum_bit_depth') or 16,
                'source': 'qobuz', 'type': 'album',
            })

    return {
        'id': str(d.get('id') or artist_id), 'name': name, 'image': image,
        'albums': albums, 'top_tracks': top,
        'bio': ((d.get('biography') or {}).get('content') or '').strip(),
        'source': 'qobuz',
    }

def sync_qobuz_search_both(query, limit_tracks=50, limit_albums=15):
    """UN SEUL appel Qobuz pour les titres ET les albums (l'API catalog/search renvoie
    déjà les deux). Évite de faire deux fois la même requête à chaque recherche."""
    if QOBUZ_OFFICIAL_ENABLED and client:
        try:
            r = client.api_call("catalog/search", query=query,
                                limit=max(limit_tracks, limit_albums, 20), offset=0)
            tracks = (r.get('tracks') or {}).get('items', [])[:limit_tracks]
            albums = (r.get('albums') or {}).get('items', [])[:limit_albums]
            for it in tracks:
                it['source'] = 'qobuz'; fix_qobuz_title(it)
                it['date'] = it.get('release_date_original') or it.get('released_at')
            for it in albums:
                it['source'] = 'qobuz'
                it['date'] = it.get('release_date_original') or it.get('released_at')
            if tracks or albums:
                return tracks, albums
        except Exception as e:
            logger.error(f"[Qobuz Search both] {e}")
    # Repli : ancienne voie (2 appels séparés)
    return (sync_qobuz_search(query, limit_tracks, 'track'),
            sync_qobuz_search(query, limit_albums, 'album'))

def sync_qobuz_search(query, limit=25, type='track'):
    """Recherche Qobuz : API officielle en priorité, repli sur l'API alt (kennyy)."""
    if not QOBUZ_ENABLED:
        return []
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
    if not QOBUZ_ALT_ENABLED:
        return []
    try:
        r = HTTP.get(f"{QOBUZ_ALT_API_BASE}/api/get-music",
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
    if not QOBUZ_ENABLED:
        return None
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
    if not QOBUZ_ALT_ENABLED:
        return None
    try:
        r = HTTP.get(f"{QOBUZ_ALT_API_BASE}/api/get-album",
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
        r = HTTP.get(f"https://api.deezer.com/artist/{artist_id}", timeout=5)
        artist = r.json()
        if 'error' in artist or not artist.get('name'): return None

        r_top = HTTP.get(f"https://api.deezer.com/artist/{artist_id}/top", params={"limit": 20}, timeout=5)
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
            
        r_alb = HTTP.get(f"https://api.deezer.com/artist/{artist_id}/albums", params={"limit": 50}, timeout=5)
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
        r = HTTP.get("https://api.deezer.com/search/artist", params={"q": name, "limit": 10}, timeout=5)
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


def _qobuz_pick_by_isrc(isrc):
    """Recherche Qobuz par ISRC → 1er titre dont l'ISRC correspond exactement."""
    try:
        for rec in sync_qobuz_search(isrc, limit=10, type='track'):
            if rec.get('isrc', '').upper() == isrc.upper():
                rec['source'] = 'qobuz'
                fix_qobuz_title(rec)
                img = rec.get('album', {}).get('image', {}).get('large')
                if img:
                    rec['album']['image']['large'] = img.replace('_300', '_600')
                return rec
    except Exception as e:
        logger.warning(f"[Resolve] ISRC search failed: {e}")
    return None

def _qobuz_pick_by_ta(title, artist):
    """Recherche Qobuz par titre+artiste (même correspondance floue que pour Tidal)."""
    try:
        rec = _best_match(sync_qobuz_search(f"{title} {_primary_artist(artist)}", limit=15), title, artist)
    except Exception:
        return None
    if rec:
        rec['source'] = 'qobuz'
        img = (rec.get('album') or {}).get('image', {}).get('large')
        if img:
            rec['album']['image']['large'] = img.replace('_300', '_600')
    return rec

def _qobuz_resolve_one(title, artist, isrc=None):
    """Titre sur Qobuz : ISRC exact d'abord, sinon titre + artiste."""
    if not QOBUZ_ENABLED:
        return None
    rec = _qobuz_pick_by_isrc(isrc) if isrc else None
    if not rec and title and artist:
        rec = _qobuz_pick_by_ta(title, artist)
    return rec

def sync_resolve_track(title, artist, isrc=None):
    """Résolution rapide : cache → recherches Qobuz (ISRC + titre/artiste) EN PARALLÈLE
    → repli Deezer. L'ISRC reste prioritaire s'il matche."""
    key = _resolve_key(title, artist, isrc)
    found, cached = _resolve_cache_get(key)
    if found:
        return cached
    # Single-flight : une seule résolution réseau par clé, même si N requêtes arrivent
    with _resolve_locks_guard:
        lock = _resolve_locks.setdefault(key, threading.Lock())
    with lock:
        found, cached = _resolve_cache_get(key)
        if found:
            return cached
        res = _resolve_track_uncached(title, artist, isrc)
        _resolve_cache_put(key, res)
        return res

def _resolve_track_uncached(title, artist, isrc=None):
    has_ta = bool(title and artist)

    # 1+2. Qobuz : ISRC et titre+artiste lancés EN PARALLÈLE. On rend la main dès que l'ISRC répond
    # (le plus fiable) ; on n'attend le second QUE s'il n'a rien donné → pas de temps perdu.
    rec_isrc = rec_ta = None
    if isrc and has_ta:
        f_isrc = _RESOLVE_POOL.submit(_qobuz_pick_by_isrc, isrc)
        f_ta = _RESOLVE_POOL.submit(_qobuz_pick_by_ta, title, artist)
        try: rec_isrc = f_isrc.result()
        except Exception: pass
        if not rec_isrc:
            try: rec_ta = f_ta.result()
            except Exception: pass
    elif isrc:
        rec_isrc = _qobuz_pick_by_isrc(isrc)
    elif has_ta:
        rec_ta = _qobuz_pick_by_ta(title, artist)

    if rec_isrc:
        logger.info(f"[Resolve] ISRC match: {isrc} → qobuz:{rec_isrc['id']}")
        return rec_isrc
    if rec_ta:
        return rec_ta

    # 3. Tidal (addon Eljohnzc), si Qobuz n'a pas le titre
    rec_tidal = _tidal_resolve_one(title, artist, isrc)
    if rec_tidal:
        return rec_tidal
    if LOSSLESS_MODE:
        return None  # pas de repli Deezer en mode lossless

    # Si pas de titre/artiste : tenter Deezer par ISRC puis abandonner
    if not has_ta:
        if DEEZER_FALLBACK_ENABLED and isrc:
            try:
                dz = sync_deezer_lookup(None, None, isrc)
                if dz: return dz
            except Exception: pass
        return None

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
    if LOSSLESS_MODE:
        tracks = await run_in_threadpool(sync_get_radio_queue, title, artist)
        if not tracks: raise HTTPException(404, "No results")
        return JSONResponse(tracks)
    tracks = await run_in_threadpool(sync_get_radio_queue, title, artist)
    if not tracks: raise HTTPException(404, "No results")
    return JSONResponse(tracks)

# Cache des recherches : une même requête (ex. l'utilisateur revient en arrière, ou
# plusieurs personnes cherchent la même chose) ne retape plus Qobuz+Deezer+Tidal.
_search_cache: dict = {}
SEARCH_CACHE_TTL = 0    # cache désactivé (résultats toujours frais)
SEARCH_CACHE_MAX = 200

@app.get('/search')
async def search_tracks(q: str, type: str = 'all'):
    _ck = (q.strip().lower(), type)
    _hit = _search_cache.get(_ck)
    if _hit and (time.time() - _hit[1]) < SEARCH_CACHE_TTL:
        return JSONResponse(_hit[0])

    payload = await _do_search(q, type)
    if len(_search_cache) >= SEARCH_CACHE_MAX:
        try:
            del _search_cache[min(_search_cache, key=lambda k: _search_cache[k][1])]
        except Exception:
            _search_cache.clear()
    _search_cache[_ck] = (payload, time.time())
    return JSONResponse(payload)

async def _do_search(q: str, type: str = 'all'):
    # MODE LOSSLESS : titres + albums + artistes via Qobuz et Tidal (fusionnés sans doublon),
    # playlists via Deezer (leurs titres sont re-cherchés sur Qobuz/Tidal au clic)
    if LOSSLESS_MODE:
        want_t, want_al, want_ar = type in ['track', 'all'], type in ['album', 'all'], type in ['artist', 'all']
        subtasks, kinds = [], []
        if QOBUZ_ENABLED and (want_t or want_al or want_ar):
            subtasks.append(run_in_threadpool(sync_qobuz_search_all, q)); kinds.append('qobuz')
        if want_t:
            subtasks.append(run_in_threadpool(sync_search_tidal, q, 50)); kinds.append('tracks')
        if want_al:
            subtasks.append(run_in_threadpool(sync_search_tidal_albums, q, 15)); kinds.append('albums')
        if want_ar:
            subtasks.append(run_in_threadpool(sync_search_tidal_artists, q, 15)); kinds.append('artists')
        if type in ['playlist', 'all']:
            subtasks.append(run_in_threadpool(sync_search_deezer_playlists, q, 50)); kinds.append('playlists')
        done = await asyncio.gather(*subtasks, return_exceptions=True)
        res = {'tracks': [], 'albums': [], 'playlists': [], 'artists': [], 'qobuz': ([], [], [])}
        for k, r in zip(kinds, done):
            if k == 'qobuz':
                res[k] = r if isinstance(r, tuple) else ([], [], [])
            else:
                res[k] = r if isinstance(r, list) else []
        qz_tracks, qz_albums, qz_artists = res['qobuz']
        tracks = _merge_lossless(qz_tracks, res['tracks'], _track_sig) if want_t else []
        albums = _merge_lossless(qz_albums, res['albums'], _album_sig) if want_al else []
        artists = []
        if want_ar:
            seen = set()
            for a in qz_artists + res['artists']:  # Qobuz d'abord, Tidal complète
                k = _match_str(a['name'])
                if k not in seen:
                    seen.add(k); artists.append(a)
        return {"tracks": tracks, "albums": albums,
                "external_playlists": res['playlists'], "artists": artists[:15]}

    tasks = []
    want_tracks = type in ['track', 'all']
    want_albums = type in ['album', 'all']
    # UN SEUL appel Qobuz quand on veut titres + albums (au lieu de deux identiques)
    if want_tracks and want_albums:
        tasks.append(run_in_threadpool(sync_qobuz_search_both, q, 50, 15))
    elif want_tracks:
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 50, 'track'))
    elif want_albums:
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 15, 'album'))
    if want_tracks:
        tasks.append(run_in_threadpool(sync_search_deezer_tracks, q, 25))
        tasks.append(run_in_threadpool(sync_search_tidal, q, 25))
    if want_albums:
        tasks.append(run_in_threadpool(sync_search_deezer_albums, q, 15))
    if type in ['artist', 'all']:
        tasks.append(run_in_threadpool(sync_search_deezer_artists, q, 15))

    finished = await asyncio.gather(*tasks, return_exceptions=True)
    idx = 0
    qobuz_tracks = []; amazon_tracks = []; deezer_tracks = []; tidal_tracks = []
    qobuz_albums = []; amazon_albums = []; deezer_albums = []
    deezer_artists = []

    if want_tracks and want_albums:
        r0 = finished[idx]; idx += 1
        if isinstance(r0, tuple) and len(r0) == 2:
            qobuz_tracks = r0[0] if isinstance(r0[0], list) else []
            qobuz_albums = r0[1] if isinstance(r0[1], list) else []
    elif want_tracks:
        r0 = finished[idx]; idx += 1; qobuz_tracks = r0 if isinstance(r0, list) else []
    elif want_albums:
        r0 = finished[idx]; idx += 1; qobuz_albums = r0 if isinstance(r0, list) else []
    if want_tracks:
        r2 = finished[idx]; idx += 1; deezer_tracks = r2 if isinstance(r2, list) else []
        r3b = finished[idx]; idx += 1; tidal_tracks = r3b if isinstance(r3b, list) else []
    if want_albums:
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

    # PRIORITÉ QUALITÉ : pour les doublons, on compare Qobuz / Tidal (qualité connue dès la
    # recherche via les tags) et on affiche la source de meilleure qualité.
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
        # Si on a changé de source et que la nouvelle n'a pas de pochette, garder celle de Qobuz
        if chosen is not t and not _has_cover(chosen) and _has_cover(t):
            chosen.setdefault('album', {}).setdefault('image', {})['large'] = t['album']['image']['large']
        combined_tracks.append(chosen)
        sigs.add(s)

    # Insertion Tidal / addon Eljohnzc (titres absents ailleurs) — avant Deezer : lisible
    # directement, alors qu'un titre Deezer doit encore être résolu au clic
    for t in tidal_tracks:
        s = get_dedup_sig(t)
        if s not in sigs:
            combined_tracks.append(t)
            sigs.add(s)

    # Insertion Deezer (si pas de doublon)
    for t in deezer_tracks:
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
    
    return {
        "tracks": combined_tracks,
        "albums": combined_albums,
        "external_playlists": deezer_playlists,
        "artists": deezer_artists
    }

@app.get('/artist_bio')
async def get_artist_bio_route(name: str, id: str = None, source: str = None):
    # Qobuz d'abord (par ID si connu, sinon par nom), Tidal en secours, Deezer en dernier recours
    qobuz_id = id if (source == 'qobuz' and id) else await run_in_threadpool(_qobuz_find_artist_id, name)
    if qobuz_id:
        data = await run_in_threadpool(sync_get_qobuz_artist, qobuz_id)
        if data: return JSONResponse(data)
    tidal_id = id if (source == 'tidal_hund' and id) else await run_in_threadpool(_tidal_find_artist_id, name)
    if tidal_id:
        data = await run_in_threadpool(sync_get_tidal_artist, tidal_id)
        if data: return JSONResponse(data)
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
        # Qobuz en priorité (lecture directe), Tidal sinon
        tracks = await run_in_threadpool(sync_qobuz_search, theme, limit * 3) if QOBUZ_ENABLED else []
        if not tracks:
            tracks = await run_in_threadpool(sync_search_tidal, theme, 25)
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
                'duration': t['duration'], 'source': t.get('source', 'qobuz')
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
    
@app.get('/status')
async def status_route():
    """Diagnostic : version en ligne, Qobuz (identifiants, recherche, flux) et addon Tidal.
    N'affiche jamais le jeton."""
    def _http_code(e):
        resp = getattr(e, 'response', None)
        return f"HTTP {resp.status_code}" if resp is not None else type(e).__name__

    def _check():
        qz = {
            'jeton': 'présent' if TOKEN else 'ABSENT',
            'id_utilisateur': 'présent' if USER_ID else 'ABSENT',
            'actif': QOBUZ_ENABLED,
        }
        if QOBUZ_ENABLED and client:
            try:
                r = client.api_call("catalog/search", query="daft punk get lucky", limit=1)
                items = (r.get('tracks') or {}).get('items') or []
                qz['recherche'] = 'ok' if items else 'aucun résultat'
                if items:
                    try:
                        d = client.get_track_url(items[0]['id'], 27)
                        if d.get('url'):
                            qz['flux'] = ('extrait de 30 s seulement' if d.get('sample') else 'ok') + f" (FLAC {d.get('bit_depth')} bits / {d.get('sampling_rate')} kHz)"
                        else:
                            qz['flux'] = 'aucun lien audio'
                    except Exception as e:
                        qz['flux'] = f"erreur ({_http_code(e)})"
            except Exception as e:
                qz['recherche'] = f"erreur ({_http_code(e)})"

        try:
            r = HTTP.get(f"{ELJOHNZC_BASE}/search", params={'q': 'daft punk'}, timeout=10)
            body = r.json() if 'json' in r.headers.get('content-type', '') else {}
            tidal = 'ok' if r.status_code == 200 and body.get('tracks') else f"en panne (HTTP {r.status_code}{' : ' + body['error'] if body.get('error') else ''})"
        except Exception as e:
            tidal = f"injoignable ({type(e).__name__})"

        if not TOKEN or not USER_ID:
            conseil = "Ajoute QOBUZ_TOKEN et QOBUZ_USER_ID dans Vercel (Settings → Environment Variables, environnement Production), puis REDÉPLOIE."
        elif str(qz.get('recherche', '')).startswith('erreur (HTTP 401') or str(qz.get('flux', '')).startswith('erreur (HTTP 401'):
            conseil = "Le jeton Qobuz est refusé : il a sans doute expiré. Reconnecte-toi sur Qobuz pour en obtenir un nouveau et remplace QOBUZ_TOKEN."
        elif qz.get('recherche') == 'ok' and str(qz.get('flux', '')).startswith('ok'):
            conseil = "Qobuz fonctionne. Si le site affiche encore l'ancienne version, recharge-le avec Ctrl+Maj+R."
        else:
            conseil = "Qobuz ne répond pas correctement depuis ce serveur : voir le détail ci-dessus."
        return {'version': APP_VERSION, 'mode': 'lossless' if LOSSLESS_MODE else 'mixte',
                'qobuz': qz, 'tidal_addon': tidal, 'conseil': conseil}

    return JSONResponse(await run_in_threadpool(_check), headers={'Cache-Control': 'no-store'})

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
            r = HTTP.get(f"https://api.deezer.com/playlist/{id}", timeout=5)
            data = r.json()
            if 'error' in data: return {"error": data['error'].get('message')}
            r_tracks = HTTP.get(f"https://api.deezer.com/playlist/{id}/tracks", params={'limit': 500}, timeout=5)
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
    match = await run_in_threadpool(sync_resolve_track, title, artist)
    if match:
        rid = match['id']
        if match['source'] == 'amazon_music': return RedirectResponse(f"/amazon_stream/{rid}")
        if match['source'] == 'tidal_hund': return RedirectResponse(f"/tidal_manifest/{rid}")
        if match['source'] == 'deezer_flac': return RedirectResponse(f"/deezer_stream/{rid}")
        return RedirectResponse(f"/stream/{rid}")
    raise HTTPException(404, "Track not found")

@app.get('/resolve_metadata')
async def resolve_metadata_route(title: str = '', artist: str = '', isrc: str = None, exclude: str = None):
    """
    Retourne l'objet track complet (ID, source, image, etc.).
    Accepte title+artist et/ou isrc (ISRC est prioritaire).
    exclude=qobuz|tidal_hund : cherche le même titre sur L'AUTRE source (secours quand une
    source n'arrive pas à lire un titre).
    """
    if exclude == 'qobuz':
        match = await run_in_threadpool(_tidal_resolve_one, title, artist, isrc)
    elif exclude == 'tidal_hund':
        match = await run_in_threadpool(_qobuz_resolve_one, title, artist, isrc)
    else:
        match = await run_in_threadpool(sync_resolve_track, title, artist, isrc)
    if match: return JSONResponse(match)
    raise HTTPException(404, "Not found")

@app.get('/track')
async def get_track_info(id: str, source: str = None):
    if source == 'tidal_hund':
        # Lien partagé d'un titre Tidal : fiche via l'API Tidal de métadonnées
        def _fetch_tidal_info():
            try:
                r = HTTP.get(f"{TIDAL_HIFI_BASE.rstrip('/')}/info/", params={'id': id}, headers=_tidal_headers(), timeout=12)
                d = r.json() or {}
                return _tidal_track_obj(d.get('data') or d)
            except Exception as e:
                logger.warning(f"[Tidal Info] {id}: {e}")
                return None
        res = await run_in_threadpool(_fetch_tidal_info)
        if res: return JSONResponse(res)
        raise HTTPException(404)
    if source == 'amazon_music':
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
                r = HTTP.get(f"https://api.deezer.com/album/{id}", timeout=5)
                data = r.json()
                r_tracks = HTTP.get(f"https://api.deezer.com/album/{id}/tracks", params={'limit': 500}, timeout=5)
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
    if not QOBUZ_ALT_ENABLED:
        return None
    for fmt in [27, 7, 6, 5]:
        try:
            r = HTTP.get(
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
        r = HTTP.get(url, timeout=8, headers={'User-Agent': SQUID_UA})
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
        r = HTTP.get(f"{DZR_RESOLVER}?isrc={urllib.parse.quote(isrc)}&format=FLAC",
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
            h = HTTP.get(url, headers={'Range': 'bytes=0-0', 'User-Agent': SQUID_UA}, timeout=10)
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
        r = HTTP.get(url, headers={'Range': f'bytes={aligned_start}-{aligned_end}', 'User-Agent': SQUID_UA}, timeout=40)
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
        h = HTTP.get(url, headers={'User-Agent': AMZ_APP_UA, 'Range': 'bytes=0-16383'}, timeout=20)
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
        return HTTP.get(url, headers={'User-Agent': AMZ_APP_UA, 'Range': rng}, timeout=40)

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
    if SQUID_ENABLED and is_asin(track_id):
        return JSONResponse({'url': f"/stream/{track_id}"})
    url = await run_in_threadpool(_resolve_qobuz_url, track_id)
    if url:
        return JSONResponse({'url': url})
    raise HTTPException(404, "URL not found")

@app.get('/stream/{track_id}')
async def stream_track(track_id: str):
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
            resp = HTTP.get(stream_url, timeout=60)
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

# --- TIDAL (lecture via l'addon Eljohnzc : manifeste DASH FLAC non chiffré + proxy segments) ---
# L'ancienne hifi-api ne renvoie plus que des extraits de 30 s (PREVIEW) : remplacée par l'addon.
# L'addon descend tout seul de qualité si celle demandée n'existe pas pour le titre ; les
# qualités suivantes ne servent qu'en cas d'erreur.
def _addon_qualities():
    return list(dict.fromkeys([ELJOHNZC_QUALITY, 'lossless', 'high']))

def _addon_stream_ok(d):
    """Réponse /stream exploitable : une URL de manifeste, sans DRM."""
    return bool(d and d.get('url') and not d.get('encrypted'))

def _tidal_resolve_mpd(track_id):
    """Version synchrone (repli si httpx indisponible) : /stream de l'addon → texte du MPD."""
    for q in _addon_qualities():
        try:
            r = HTTP.get(f"{ELJOHNZC_BASE}/stream/{track_id}", params={'quality': q}, timeout=12)
            if r.status_code == 404:
                return None  # titre inconnu de l'addon : inutile d'essayer d'autres qualités
            if r.status_code != 200:
                continue
            d = r.json()
            if not _addon_stream_ok(d):
                continue
            m = HTTP.get(d['url'], timeout=12)
            if m.status_code == 200 and '<MPD' in m.text:
                return m.text
        except Exception as e:
            logger.warning(f"[Tidal] addon stream {track_id} ({q}): {e}")
    return None

@app.get('/tidal_manifest/{track_id}')
async def get_tidal_manifest_route(track_id: str):
    """Manifeste DASH Tidal pour Shaka via l'addon (100 % async, aucun cache : toujours frais)."""
    if not track_id.isdigit():
        raise HTTPException(404, "Tidal stream not found")
    mpd = None
    if ASYNC_HTTP_OK:
        for q in _addon_qualities():
            try:
                r = await AHTTP.get(f"{ELJOHNZC_BASE}/stream/{track_id}", params={'quality': q})
                if r.status_code == 404:
                    break
                if r.status_code != 200:
                    continue
                d = r.json()
                if not _addon_stream_ok(d):
                    continue
                m = await AHTTP.get(d['url'])
                if m.status_code == 200 and '<MPD' in m.text:
                    mpd = m.text
                    break
            except Exception as e:
                logger.warning(f"[Tidal] async addon stream {track_id} ({q}): {e}")
    else:
        mpd = await run_in_threadpool(_tidal_resolve_mpd, track_id)
    if not mpd:
        raise HTTPException(404, "Tidal stream not found")
    b64 = base64.b64encode(mpd.encode('utf-8')).decode('ascii')
    return JSONResponse({'mimeType': 'application/dash+xml', 'manifest': b64})

@app.get('/tidal_proxy')
async def tidal_proxy(url: str, request: Request):
    """Proxy TRANSPARENT des segments audio Tidal (le CDN refuse les requêtes du navigateur).
    100 % asynchrone et en streaming : les octets sont relayés au fil de l'eau, sans être
    stockés ni mis en cache, et sans monopoliser de thread → le serveur n'est qu'un tuyau."""
    host = (urllib.parse.urlparse(url).hostname or '').lower()
    if not (host.endswith('.audio.tidal.com') or host.endswith('.tidal.com')):
        raise HTTPException(403, "host not allowed")
    rng = request.headers.get('range') or request.headers.get('Range') or 'bytes=0-'
    # IMPORTANT : ne jamais transmettre 'Origin' (le CDN Tidal répond 403 s'il est présent)
    fwd = {'User-Agent': SQUID_UA, 'Range': rng, 'Accept-Encoding': 'identity'}

    if ASYNC_HTTP_OK:
        req = AHTTP.build_request('GET', url, headers=fwd)
        upstream = await AHTTP.send(req, stream=True)

        async def _relay():
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            except (GeneratorExit, asyncio.CancelledError):
                # Le client a zappé : on ARRÊTE immédiatement de tirer depuis le CDN
                # au lieu de télécharger un segment dont plus personne ne veut.
                raise
            finally:
                await upstream.aclose()

        h = {'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store',
             'Access-Control-Allow-Origin': '*'}
        for k in ('Content-Range', 'Content-Length'):
            if k in upstream.headers:
                h[k] = upstream.headers[k]
        return StreamingResponse(_relay(), status_code=upstream.status_code,
                                 media_type=upstream.headers.get('Content-Type', 'audio/mp4'),
                                 headers=h)

    # Repli synchrone si httpx indisponible
    def _fetch():
        return HTTP.get(url, headers=fwd, timeout=30)
    r = await run_in_threadpool(_fetch)
    headers = {'Accept-Ranges': 'bytes', 'Content-Length': str(len(r.content)),
               'Cache-Control': 'no-store', 'Access-Control-Allow-Origin': '*'}
    if r.headers.get('Content-Range'):
        headers['Content-Range'] = r.headers['Content-Range']
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get('Content-Type', 'audio/mp4'), headers=headers)

# --- LRC.RED (paroles synchronisées mot à mot, source principale) ---
# Conditions d'utilisation : s'identifier via le User-Agent (lien + e-mail de contact) et
# créditer lrc.red avec un lien partout où ses paroles sont affichées (fait côté interface).
LRCRED_BASE = "https://lrc.red"
LRCRED_SITE = os.getenv('LRCRED_SITE') or ''        # URL publique du site (ex : https://zenith.vercel.app)
LRCRED_CONTACT = os.getenv('LRCRED_CONTACT') or ''  # e-mail de contact demandé par lrc.red
LRCRED_UA = "Zenith/1.0 (" + "; ".join(x for x in ["lecteur de musique", ("+" + LRCRED_SITE) if LRCRED_SITE else "", LRCRED_CONTACT] if x) + ")"
_lrcred_cache: dict = {}        # clé -> (résultat ou None, ts)
LRCRED_TTL = 12 * 3600
LRCRED_NEG_TTL = 3600
LRCRED_CACHE_MAX = 400

_TT_NS = "{http://www.w3.org/ns/ttml}"
_TTM_NS = "{http://www.w3.org/ns/ttml#metadata}"
_LRC_NS = "{http://lrc.red/lyric-ttml-internal}"
_XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

def _lrcred_get(path, timeout=8):
    return HTTP.get(f"{LRCRED_BASE}{path}", headers={'User-Agent': LRCRED_UA}, timeout=timeout)

def _ttml_time(v):
    """'27.395' | '1:00.964' | '1:02:03.5' → secondes."""
    if not v:
        return None
    try:
        sec = 0.0
        for part in str(v).strip().split(':'):
            sec = sec * 60 + float(part)
        return round(sec, 3)
    except ValueError:
        return None

def _ttml_words(el, words, keep_bg):
    """Parcourt un élément et ajoute ses <span begin end> (mots / syllabes) dans l'ordre.
    Les espaces entre spans indiquent une fin de mot ; deux spans collés = syllabes d'un même mot."""
    for child in el:
        is_bg = child.get(_TTM_NS + 'role') == 'x-bg'
        if is_bg and not keep_bg:
            pass
        elif child.get('begin') is not None and len(child) == 0:
            words.append({'t': child.text or '', 's': _ttml_time(child.get('begin')), 'e': _ttml_time(child.get('end'))})
        else:
            _ttml_words(child, words, keep_bg)
        tail = child.tail or ''
        if words and tail:
            if tail.strip():
                words[-1]['t'] += tail.rstrip()
            if tail[-1:].isspace():
                words[-1]['sp'] = 1

def _ttml_text(el):
    """Texte d'un élément sans les chœurs (x-bg)."""
    out = [el.text or '']
    for child in el:
        if child.get(_TTM_NS + 'role') != 'x-bg':
            out.append(_ttml_text(child))
        out.append(child.tail or '')
    return ''.join(out)

def _words_text(words):
    return ''.join(w['t'] + (' ' if w.get('sp') else '') for w in words).strip()

def _parse_lrcred_ttml(xml_text):
    """TTML lrc.red → {'sync', 'lines', 'translations'} (mots, chœurs, voix, parties)."""
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text)
    timing = (root.get(_LRC_NS + 'timing') or '').lower()

    # Voix : la 2e personne d'un duo est alignée à droite dans l'interface
    persons = [a.get('{http://www.w3.org/XML/1998/namespace}id') for a in root.iter(_TTM_NS + 'agent') if a.get('type') == 'person']
    alt_agent = persons[1] if len(persons) > 1 else None

    translations = {}
    for tr in root.iter(_LRC_NS + 'translation'):
        lang = tr.get(_XML_LANG) or ''
        texts = {t.get('for'): _ttml_text(t).strip() for t in tr.iter(_LRC_NS + 'text') if t.get('for')}
        if lang and texts:
            translations[lang] = texts

    lines = []
    body = root.find(_TT_NS + 'body')
    if body is None:
        return None
    for div in body.iter(_TT_NS + 'div'):
        part = div.get(_LRC_NS + 'songPart')
        for i, pel in enumerate(div.findall(_TT_NS + 'p')):
            start = _ttml_time(pel.get('begin'))
            if start is None:
                continue
            words = []
            _ttml_words(pel, words, keep_bg=False)
            bg = []
            for bg_el in pel.iter(_TT_NS + 'span'):
                if bg_el.get(_TTM_NS + 'role') == 'x-bg':
                    _ttml_words(bg_el, bg, keep_bg=True)
            line = {
                'time': start,
                'end': _ttml_time(pel.get('end')),
                'text': _words_text(words) if words else ' '.join(_ttml_text(pel).split()),
                'key': pel.get(_LRC_NS + 'key'),
            }
            if words:
                line['words'] = words
            if bg:
                line['bg'] = bg
            if alt_agent and pel.get(_TTM_NS + 'agent') == alt_agent:
                line['alt'] = 1
            if i == 0 and part:
                line['part'] = part
            if line['text'] or bg:
                lines.append(line)
    if not lines:
        return None
    has_words = any(l.get('words') for l in lines)
    return {'sync': 'word' if (has_words or timing == 'word') else 'line', 'lines': lines, 'translations': translations}

def _lrcred_pick(hits, title, artist, duration):
    """Meilleur résultat de recherche : titre + artiste, et durée proche (évite remix / live)."""
    t_artists = _artist_forms(artist)
    t_full = _match_str(title); t_base = _base_title(title)
    t_variants = {v.lower() for v in _VARIANT_RE.findall(title or '')}
    best, best_score = None, 0
    for h in hits:
        a_score = max(_ratio(a, b) for a in t_artists for b in _artist_forms(h.get('artist') or ''))
        if a_score < 60:
            continue
        t_score = _ratio(t_base, _base_title(h.get('title') or ''))
        if t_score < 70:
            continue
        score = (a_score + t_score) / 2
        if _match_str(h.get('title')) == t_full:
            score += 5
        score -= 10 * len({v.lower() for v in _VARIANT_RE.findall(h.get('title') or '')} - t_variants)
        if duration and h.get('duration'):
            diff = abs(float(h['duration']) - float(duration))
            score += 8 if diff <= 2 else (0 if diff <= 6 else -25)
        if score > best_score:
            best, best_score = h, score
    return best

def _lrcred_fetch(isrc):
    """Fiche + TTML d'un ISRC → paroles prêtes pour l'interface, ou None."""
    meta = _lrcred_get(f"/s/{urllib.parse.quote(isrc)}.json")
    if meta.status_code != 200:
        return None
    m = meta.json()
    if not m.get('sync') or not (m.get('files') or {}).get('ttml'):
        return None  # paroles non synchronisées : on laisse LRCLib / YouTube
    r = _lrcred_get(f"/s/{urllib.parse.quote(isrc)}.ttml", timeout=10)
    if r.status_code != 200:
        return None
    parsed = _parse_lrcred_ttml(r.text)
    if not parsed:
        return None
    lrc = '\n'.join(f"[{int(l['time'] // 60):02d}:{l['time'] % 60:05.2f}]{l['text']}" for l in parsed['lines'])
    return {
        'type': 'synced', 'sync': parsed['sync'], 'lines': parsed['lines'], 'lyrics': lrc,
        'translations': parsed['translations'], 'language': m.get('language'),
        'source': 'lrc.red', 'credit_url': m.get('page') or f"{LRCRED_BASE}/s/{isrc}",
    }

def sync_lrcred_lyrics(title, artist, duration=0, isrc=None):
    """Paroles lrc.red : ISRC direct si connu, sinon recherche titre + artiste (résultat en cache)."""
    key = f"isrc:{isrc.upper()}" if isrc else f"ta:{_match_str(title)}|{_match_str(_primary_artist(artist))}|{int(duration or 0) // 5}"
    hit = _lrcred_cache.get(key)
    if hit and (time.time() - hit[1]) < (LRCRED_TTL if hit[0] else LRCRED_NEG_TTL):
        return hit[0]
    res = None
    try:
        if isrc:
            res = _lrcred_fetch(isrc)
        if not res and title and artist:
            queries = [f"{title} {_primary_artist(artist)}"]
            base = re.sub(r'\s*[\(\[].*?[\)\]]', '', title).strip()
            if base and base.lower() != title.lower():
                queries.append(f"{base} {_primary_artist(artist)}")
            for q in queries:
                r = _lrcred_get(f"/search.json?q={urllib.parse.quote(q)}")
                hits = (r.json() or {}).get('hits') or [] if r.status_code == 200 else []
                pick = _lrcred_pick(hits, title, artist, duration)
                if pick and pick.get('isrc'):
                    res = _lrcred_fetch(pick['isrc'])
                    if res:
                        break
    except Exception as e:
        logger.warning(f"[lrc.red] {title} / {artist}: {e}")
        return None  # erreur réseau : pas de cache négatif
    if len(_lrcred_cache) >= LRCRED_CACHE_MAX:
        try:
            del _lrcred_cache[min(_lrcred_cache, key=lambda k: _lrcred_cache[k][1])]
        except Exception:
            _lrcred_cache.clear()
    _lrcred_cache[key] = (res, time.time())
    return res

@app.get('/lyrics')
async def get_lyrics(artist: str, title: str, album: str = None, duration: str = None, isrc: str = None):
    # Ordre de priorité : sources LRC d'abord (lrc.red mot à mot, puis LRCLib), YouTube en dernier
    # recours. Les trois partent EN PARALLÈLE pour ne pas perdre de temps quand une source n'a pas le titre.
    dur = parse_duration(duration)
    _red_task = asyncio.create_task(run_in_threadpool(sync_lrcred_lyrics, title, artist, dur, isrc))
    _lrc_task = asyncio.create_task(run_in_threadpool(lyrics_engine.search_lyrics, artist, title, album, dur))
    _yt_task = asyncio.create_task(run_in_threadpool(sync_search_yt_lyrics, title, artist))
    try: red = await _red_task
    except Exception: red = None
    if red:
        _lrc_task.cancel(); _yt_task.cancel()
        return JSONResponse(red)
    lrc_plain, lrc_synced = None, None
    try: lrc_plain, lrc_synced = await _lrc_task
    except Exception: pass
    if lrc_synced:
        _yt_task.cancel()
        return JSONResponse({"type": "synced", "lyrics": lrc_synced, "source": "LRCLib"})
    try: yt_res = await _yt_task
    except Exception: yt_res = None
    # Une version synchronisée (même YouTube) reste préférable à un texte brut
    if yt_res and yt_res['type'] == 'synced': return JSONResponse({"type": "synced", "lyrics": yt_res['lyrics'], "source": "YouTube"})
    if lrc_plain: return JSONResponse({"type": "plain", "lyrics": lrc_plain, "source": "LRCLib"})
    if yt_res and yt_res['type'] == 'plain': return JSONResponse({"type": "plain", "lyrics": yt_res['lyrics'], "source": "YouTube"})
    raise HTTPException(404, "No lyrics")

# --- STATIC FILES ---
app.mount("/", StaticFiles(directory=PROJECT_ROOT, html=True), name="static")