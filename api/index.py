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
USER_ID = os.getenv('QOBUZ_USER_ID', '')
TOKEN   = os.getenv('QOBUZ_TOKEN', '')
APP_ID  = os.getenv('QOBUZ_APP_ID', '')

TIDAL_HUND_BASE = "https://api.monochrome.tf"

# Credentials Tidal chargés uniquement depuis les variables d'environnement ou token.json
FALLBACK_TIDAL_CREDENTIALS = {
    "client_ID":      os.getenv('CLIENT_ID', ''),
    "client_secret":  os.getenv('CLIENT_SECRET', ''),
    "refresh_token":  os.getenv('REFRESH_TOKEN', '')
}

# --- AMAZON MUSIC API ---
AMAZON_MUSIC_API_BASE = "https://t2tunes.site/api/amazon-music"

# --- QOBUZ ALTERNATIVE API ---
QOBUZ_ALT_API_BASE = "https://trypt-hifi-dl-456461932686.us-west1.run.app"

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

# --- CLIENT QOBUZ ---
class TokenQobuzClient(QobuzClient):
    def __init__(self, app_id, secrets, token):
        self.secrets = secrets
        self.id = str(app_id)
        self.session = self._make_session()
        self.base = "https://www.qobuz.com/api.json/0.2/"
        self.sec = None
        self.uat = token
        self.session.headers.update({"X-User-Auth-Token": self.uat})
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
try:
    logger.info("Init Qobuz...")
    fetched_app_id, secrets = get_app_credentials()
    client = TokenQobuzClient(APP_ID, secrets, TOKEN)
    logger.info("Ready.")
except Exception as e:
    logger.error(f"Init Error: {e}")

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

def sync_search_tidal(query, limit=50):
    """
    DISABLED: Tidal search is paused. Returns empty list.
    Original code kept below for re-activation.
    """
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
    try:
        url = f"{QOBUZ_ALT_API_BASE}/api/get-music"
        params = {'q': query, 'offset': 0}
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        if not data.get('success'):
            return []
        if type == 'track':
            items = data.get('data', {}).get('tracks', {}).get('items', [])
            items = items[:limit]
            for t in items:
                t['source'] = 'qobuz'
                fix_qobuz_title(t)
                t['date'] = t.get('release_date_original') or t.get('released_at')
            return items
        elif type == 'album':
            items = data.get('data', {}).get('albums', {}).get('items', [])
            items = items[:limit]
            for a in items:
                a['source'] = 'qobuz'
                a['date'] = a.get('release_date_original') or a.get('released_at')
            return items
    except Exception as e:
        logger.error(f"[Alt Qobuz Search] Error: {e}")
        return []

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
    Recherche le titre sur Qobuz (alt API) ou Amazon Music.
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

    # Si pas de titre/artiste on abandonne ici
    if not title or not artist:
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

    # 3. Fallback Amazon Music
    amazon_res = sync_search_amazon(f"{title} {artist}", limit=5)
    for t in amazon_res:
        t_artist = clean_string(t['performer']['name'])
        t_title = clean_string(t['title'])

        match_artist = False
        if FUZZ_AVAILABLE:
            if fuzz.ratio(target_artist, t_artist) > 65: match_artist = True
        elif target_artist in t_artist or t_artist in target_artist: match_artist = True

        if match_artist:
            match_title = False
            if FUZZ_AVAILABLE:
                if fuzz.ratio(target_title, t_title) > 60: match_title = True
            elif target_title in t_title or t_title in target_title: match_title = True

            if match_title:
                t['source'] = 'amazon_music'
                return t

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
    tracks = await run_in_threadpool(sync_get_radio_queue, title, artist)
    if not tracks: raise HTTPException(404, "No results")
    return JSONResponse(tracks)

@app.get('/search')
async def search_tracks(q: str, type: str = 'all'):
    tasks = []
    if type in ['track', 'all']:
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 50, 'track'))
        tasks.append(run_in_threadpool(sync_search_amazon, q, 50))
    if type in ['album', 'all']:
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 15, 'album'))
        tasks.append(run_in_threadpool(sync_search_amazon_albums, q, 15))
        tasks.append(run_in_threadpool(sync_search_deezer_albums, q, 15))
    if type in ['artist', 'all']:
        tasks.append(run_in_threadpool(sync_search_deezer_artists, q, 15))

    finished = await asyncio.gather(*tasks, return_exceptions=True)
    idx = 0
    qobuz_tracks = []; amazon_tracks = []; deezer_tracks = []
    qobuz_albums = []; amazon_albums = []; deezer_albums = []
    deezer_artists = []

    if type in ['track', 'all']:
        r1 = finished[idx]; idx += 1; qobuz_tracks = r1 if isinstance(r1, list) else []
        r2 = finished[idx]; idx += 1; amazon_tracks = r2 if isinstance(r2, list) else []
    if type in ['album', 'all']:
        r4 = finished[idx]; idx += 1; qobuz_albums = r4 if isinstance(r4, list) else []
        r5 = finished[idx]; idx += 1; amazon_albums = r5 if isinstance(r5, list) else []
        r6 = finished[idx]; idx += 1; deezer_albums = r6 if isinstance(r6, list) else []
    if type in ['artist', 'all']:
        r7 = finished[idx]; idx += 1; deezer_artists = r7 if isinstance(r7, list) else []

    deezer_playlists = []
    if type in ['playlist', 'all']:
        deezer_playlists = await run_in_threadpool(sync_search_deezer_playlists, q, 100)

    # DEDUPLICATION AVANCÉE POUR LES TITRES
    combined_tracks = []
    combined_tracks.extend(qobuz_tracks)
    
    sigs = set()
    
    def get_dedup_sig(track):
        t = track.get('title', '').lower()
        t = re.sub(r'\s*[\(\[].*?[\)\]]', '', t)
        t = re.sub(r'\s*-\s*.*', '', t)
        t = clean_string(t)
        
        p = track.get('performer', {}).get('name', '')
        if not p: p = track.get('artist', {}).get('name', '')
        p = clean_string(p)
        
        return f"{t}|{p}"

    for t in qobuz_tracks:
        sigs.add(get_dedup_sig(t))
    
    # Insertion Amazon Music (si pas de doublon)
    for t in amazon_tracks:
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
                final_tracks.append({
                    'id': str(t.get('id')),
                    'title': t.get('title'),
                    'performer': {'name': t.get('artist', {}).get('name', data.get('artist', {}).get('name'))},
                    'album': {'title': data.get('title'), 'image': {'large': data.get('cover_xl') or data.get('cover_big')}},
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
        return RedirectResponse(f"/stream/{rid}")
    raise HTTPException(404, "Track not found")

@app.get('/resolve_metadata')
async def resolve_metadata_route(title: str = '', artist: str = '', isrc: str = None):
    """
    Retourne l'objet track complet (ID, source, image, etc.).
    Accepte title+artist et/ou isrc (ISRC est prioritaire).
    """
    match = await run_in_threadpool(sync_resolve_track, title, artist, isrc)
    if match: return JSONResponse(match)
    raise HTTPException(404, "Not found")

@app.get('/track')
async def get_track_info(id: str, source: str = None):
    if source == 'tidal_hund' or source == 'amazon_music':
        raise HTTPException(404)
    elif client:
        try:
            res = await run_in_threadpool(client.get_track_meta, id)
            res['source'] = 'qobuz'; fix_qobuz_title(res)
            return JSONResponse(res)
        except: pass
    raise HTTPException(404)

@app.get('/album')
async def get_album(id: str, source: str = None):
    if source == 'amazon_music':
        res = await run_in_threadpool(sync_get_amazon_album, id)
        if res: return JSONResponse(res)
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
    elif client:
        try:
            res = await run_in_threadpool(client.get_album_meta, id)
            res['source'] = 'qobuz'
            return JSONResponse(res)
        except: pass
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
    if client:
        for fmt in [27, 7, 6, 5]:
            try:
                d = client.get_track_url(track_id, fmt)
                if 'url' in d: return d['url']
            except: continue
    return None

@app.get('/stream_url/{track_id}')
async def get_stream_url(track_id: str):
    """Retourne l'URL de stream Qobuz en JSON (pour le pré-fetch frontend)."""
    cached = _stream_url_cache.get(track_id)
    if cached and (time.time() - cached['ts']) < STREAM_CACHE_TTL:
        return JSONResponse({'url': cached['url']})
    url = await run_in_threadpool(_resolve_qobuz_url, track_id)
    if url:
        _stream_url_cache[track_id] = {'url': url, 'ts': time.time()}
        return JSONResponse({'url': url})
    raise HTTPException(404, "URL not found")

@app.get('/stream/{track_id}')
async def stream_track(track_id: str):
    cached = _stream_url_cache.get(track_id)
    if cached and (time.time() - cached['ts']) < STREAM_CACHE_TTL:
        return RedirectResponse(cached['url'])
    url = await run_in_threadpool(_resolve_qobuz_url, track_id)
    if url:
        _stream_url_cache[track_id] = {'url': url, 'ts': time.time()}
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

@app.get('/tidal_manifest/{track_id}')
async def get_tidal_manifest_route(track_id: str):
    # DISABLED: Tidal streaming is paused
    raise HTTPException(404, "Tidal streaming is currently disabled")

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