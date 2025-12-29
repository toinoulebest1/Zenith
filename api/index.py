import sys
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
import json
import base64 # Nécessaire pour décoder le manifeste Tidal 16-bit
import difflib # Nécessaire pour le matching Chosic

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

# --- PATCH GLOBAL CONNEXIONS ---
requests.adapters.DEFAULT_POOLSIZE = 100
requests.adapters.DEFAULT_RETRIES = 3

# Tentative d'import pour rapidfuzz
try:
    from rapidfuzz import fuzz
    FUZZ_AVAILABLE = True
except ImportError:
    FUZZ_AVAILABLE = False

# --- CONFIGURATION ---
USER_ID = '7610812'
TOKEN = 'wTJvd-7fc8haH3zdRrZYqcULUQ1wA6wJBLNmDkn38JaMrfRtHlaGpSVLHN0205rSQ23psXhJrnQNrRmEiGS-zw' 
APP_ID = '798273057'

TIDAL_HUND_BASE = "https://tidal-api.binimum.org"

# Credentials de secours (HARDCODED FALLBACK)
# Utilisés si os.getenv ne trouve rien ET que token.json est absent/illisible
FALLBACK_TIDAL_CREDENTIALS = {
    "client_ID": "fX2JxdmntZWK0ixT",
    "client_secret": "1Nn9AfDAjxrgJFJbKNWLeAyKGVGmINuXPPLHVXAvxAg=",
    "refresh_token": "eyJraWQiOiJoUzFKYTdVMCIsImFsZyI6IkVTNTEyIn0.eyJ0eXBlIjoibzJfcmVmcmVzaCIsInVpZCI6MTg4NjEwMjE4LCJzY29wZSI6Indfc3ViIHJfdXNyIHdfdXNyIiwiY2lkIjoxMzMxOSwic1ZlciI6MCwiZ1ZlciI6MCwiaXNzIjoiaHR0cHM6Ly9hdXRoLnRpZGFsLmNvbS92MSJ9.Abink5EqzLxeKQKuEEjo_ydkkehy_wtBFNR_1i4Hy7K8-5rgdsGYSicdCKC02MGHbTWpDsdgyvv-abN2yMGk4Af-AC4BpnQc3s3VBQ3ocp3zVpI5jlW8GpG3JHQsO0uiYYvyxDouKx_71EJHnItne2yWGhGuaobirACxrrqsffcgq8bi"
}

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
    Recherche via l'API Officielle Tidal (plus fiable)
    Mais on taggue la source 'tidal_hund' pour utiliser le proxy audio plus tard.
    """
    token = tidal_auth.get_token()
    if not token:
        logger.error("[Tidal Search] Pas de token disponible. Vérifiez REFRESH_TOKEN ou token.json.")
        return []

    logger.info(f"[Tidal Search] Query: {query}")
    try:
        url = "https://api.tidal.com/v1/search/tracks"
        params = {
            "query": query,
            "limit": limit,
            "offset": 0,
            "countryCode": "US"
        }
        headers = {"Authorization": f"Bearer {token}"}
        
        res = requests.get(url, headers=headers, params=params, timeout=10)
        logger.info(f"[Tidal Search] Status: {res.status_code}")
        
        if res.status_code != 200:
            logger.error(f"[Tidal Search] Error Body: {res.text}")
            return []

        data = res.json()
        items = data.get('items', [])
        results = []
        
        for t in items:
            try:
                # Détection Hi-Res / 24 bits via les tags officiels
                bit_depth = 16
                tags = t.get('mediaMetadata', {}).get('tags', [])
                if "HIRES_LOSSLESS" in tags or "MQA" in tags:
                    bit_depth = 24
                
                track = {
                    'id': str(t['id']),
                    'title': t['title'],
                    'performer': {'name': (t.get('artist') or {}).get('name', 'Inconnu')},
                    'album': {
                        'title': (t.get('album') or {}).get('title'),
                        'image': {'large': tidal_uuid_to_url((t.get('album') or {}).get('cover'))}
                    },
                    'duration': t.get('duration', 0),
                    'maximum_bit_depth': bit_depth,
                    # IMPORTANT: On garde 'tidal_hund' pour que le frontend sache appeler /tidal_manifest
                    'source': 'tidal_hund' 
                }
                results.append(track)
            except Exception as e:
                logger.error(f"[Tidal Search] Parse Error: {e}")
                continue
                
        logger.info(f"[Tidal Search] Found {len(results)} items")
        return results

    except Exception as e:
        logger.error(f"[Tidal Search] Exception: {e}")
        return []

def sync_search_tidal_albums(query, limit=15):
    token = tidal_auth.get_token()
    if not token: return []

    try:
        url = "https://api.tidal.com/v1/search/albums"
        params = {
            "query": query,
            "limit": limit,
            "offset": 0,
            "countryCode": "US"
        }
        headers = {"Authorization": f"Bearer {token}"}
        
        res = requests.get(url, headers=headers, params=params, timeout=10)
        data = res.json()
        items = data.get('items', [])
        
        results = []
        for a in items:
            try:
                results.append({
                    'id': str(a['id']),
                    'title': a['title'],
                    'artist': {'name': (a.get('artist') or {}).get('name', 'Inconnu')},
                    'image': {'large': tidal_uuid_to_url(a.get('cover'))},
                    'source': 'tidal_hund'
                })
            except: continue
        return results
    except Exception as e:
        logger.error(f"[Tidal Album Search] Error: {e}")
        return []

def sync_get_tidal_album(album_id):
    """
    Récupère les détails d'un album Tidal (métadonnées + pistes)
    Version Sécurisée : gère les pistes imbriquées dans 'item'
    """
    token = tidal_auth.get_token()
    if not token: return None
    
    headers = {"Authorization": f"Bearer {token}"}
    
    # 1. Metadata
    try:
        r_meta = requests.get(f"https://api.tidal.com/v1/albums/{album_id}", params={"countryCode": "US"}, headers=headers, timeout=10)
        meta = r_meta.json()
        if 'error' in meta: return None
    except: return None

    # 2. Tracks
    try:
        # Le endpoint /items retourne souvent : { "items": [ { "item": { ...track... }, "type": "track" } ] }
        r_tracks = requests.get(f"https://api.tidal.com/v1/albums/{album_id}/items", params={"limit": 100, "offset": 0, "countryCode": "US"}, headers=headers, timeout=10)
        data_tracks = r_tracks.json()
        items = data_tracks.get('items', [])
    except: items = []

    formatted_tracks = []
    cover_url = tidal_uuid_to_url(meta.get('cover'))
    
    for t in items:
        if not t: continue
        try:
            # CORRECTION : On déballe l'objet si nécessaire
            # Si 'item' existe, c'est que l'info est dedans (structure wrapper)
            # Sinon on utilise t directement (structure plate)
            actual_track = t.get('item') or t
            
            # Vérification basique
            if not actual_track or 'id' not in actual_track:
                continue

            # Detect HiRes
            bit_depth = 16
            tags = (actual_track.get('mediaMetadata') or {}).get('tags', [])
            if "HIRES_LOSSLESS" in tags or "MQA" in tags:
                bit_depth = 24
                
            formatted_tracks.append({
                'id': str(actual_track['id']),
                'title': actual_track.get('title', 'Titre Inconnu'),
                'duration': actual_track.get('duration', 0),
                'track_number': actual_track.get('trackNumber'),
                'performer': {'name': (actual_track.get('artist') or {}).get('name', 'Inconnu')},
                'album': {'title': meta.get('title'), 'image': {'large': cover_url}},
                'source': 'tidal_hund', # Important pour la lecture
                'maximum_bit_depth': bit_depth
            })
        except Exception as e:
            logger.error(f"[Tidal Album] Parse track error: {e}")
            continue

    return {
        'id': str(meta.get('id')),
        'title': meta.get('title'),
        'artist': {'name': (meta.get('artist') or {}).get('name', 'Inconnu')},
        'image': {'large': cover_url},
        'source': 'tidal_hund',
        'tracks': {'items': formatted_tracks}
    }

def get_tidal_stream_manifest(track_id):
    """
    Récupère le manifeste audio pour Shaka Player (DASH) OU l'URL directe (BTS/16-bit).
    """
    # 1. Tentative HI_RES (pour Dash)
    logger.info(f"[Tidal Audio] 🎵 Requesting manifest for ID: {track_id} (Quality: HI_RES_LOSSLESS)")
    try:
        url = f"{TIDAL_HUND_BASE}/track/?id={track_id}&quality=HI_RES_LOSSLESS"
        res = requests.get(url, timeout=15)
        
        # Si erreur 401/403/etc, on retente en LOSSLESS standard
        if res.status_code != 200:
            logger.error(f"[Tidal Audio] ❌ Error {res.status_code} for HI_RES. Retrying LOSSLESS...")
            url_fallback = f"{TIDAL_HUND_BASE}/track/?id={track_id}&quality=LOSSLESS"
            res = requests.get(url_fallback, timeout=15)
            
            if res.status_code != 200:
                logger.error(f"[Tidal Audio] ❌ Fallback to LOSSLESS also failed.")
                return None

        data = res.json()
        
        if 'data' not in data:
             logger.warning(f"[Tidal Audio] ⚠️ 'data' field missing.")
             return None
             
        track_data = data['data']
        mime = track_data.get('manifestMimeType')
        
        # CAS 1: DASH (24-bit / Hi-Res) -> Utilise Shaka Player
        if mime == 'application/dash+xml':
            logger.info(f"[Tidal Audio] ✅ DASH Manifest received (24-bit/Hi-Res).")
            return {
                "manifest": track_data['manifest'],
                "mimeType": "application/dash+xml",
                "bitDepth": track_data.get('bitDepth', 24),
                "sampleRate": track_data.get('sampleRate', 44100)
            }
            
        # CAS 2: BTS (16-bit / Lossless) -> Fichier direct -> Lecteur natif
        elif mime == 'application/vnd.tidal.bts':
            logger.info(f"[Tidal Audio] ✅ BTS Manifest received (16-bit/File). Decoding...")
            try:
                # Le manifeste BTS est un JSON encodé en base64
                decoded_json = json.loads(base64.b64decode(track_data['manifest']).decode('utf-8'))
                
                # Il contient une liste 'urls', on prend la première
                if 'urls' in decoded_json and len(decoded_json['urls']) > 0:
                    audio_url = decoded_json['urls'][0]
                    logger.info(f"[Tidal Audio] -> Direct URL extracted.")
                    return {
                        "url": audio_url,
                        "mimeType": "audio/flac", # ou mp4 selon le codec indiqué dans le json
                        "bitDepth": 16,
                        "sampleRate": 44100
                    }
                else:
                    logger.error("[Tidal Audio] BTS Manifest decoded but no 'urls' found.")
                    return None
            except Exception as e:
                logger.error(f"[Tidal Audio] Error decoding BTS manifest: {e}")
                return None
                
        else:
            logger.warning(f"[Tidal Audio] ⚠️ Unexpected MIME type: {mime}")
            return None

    except Exception as e:
        logger.error(f"[Tidal Audio] 💥 CRITICAL EXCEPTION: {e}")
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
                    'maximum_bit_depth': 16
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
                    'source': 'deezer'
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
    if not client: return []
    try:
        if type == 'track':
            r = client.api_call("track/search", query=query, limit=limit)
            items = r.get('tracks', {}).get('items', [])
            for t in items: t['source'] = 'qobuz'; fix_qobuz_title(t)
            return items
        elif type == 'album':
            r = client.api_call("album/search", query=query, limit=limit)
            items = r.get('albums', {}).get('items', [])
            for a in items: a['source'] = 'qobuz'
            return items
    except: return []

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
    # 1. Tentative QOBUZ
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
                top_tracks = []
                track_search = client.api_call("track/search", query=artist['name'], limit=20)
                if 'tracks' in track_search and 'items' in track_search['tracks']:
                    for t in track_search['tracks']['items']:
                        is_related = False
                        if str(t.get('artist', {}).get('id')) == str(artist_id): is_related = True
                        elif 'performer' in t and artist['name'].lower() in t['performer']['name'].lower(): is_related = True
                        if is_related:
                            t['source'] = 'qobuz'
                            fix_qobuz_title(t)
                            top_tracks.append(t)
                    top_tracks = top_tracks[:10]
                return {
                    "id": artist['id'],
                    "name": artist['name'],
                    "image": artist.get('image', {}).get('large', '').replace('_300', '_600'),
                    "albums": albums,
                    "top_tracks": top_tracks,
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
            if FUZZ_AVAILABLE: score = fuzz.ratio(target_clean, cand_clean)
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

def sync_resolve_track(title, artist):
    """
    Recherche le titre sur Qobuz ou Tidal et retourne l'objet COMPLET (avec image)
    au lieu de juste l'ID.
    """
    target_artist = clean_string(artist)
    target_title = clean_string(title)
    
    # 1. Recherche Qobuz
    if client:
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
                        # S'assurer que l'image est bien formatée
                        if rec.get('album', {}).get('image', {}).get('large'):
                            rec['album']['image']['large'] = rec['album']['image']['large'].replace('_300', '_600')
                        return rec
        except: pass
    
    # 2. Fallback Tidal
    tidal_res = sync_search_tidal(f"{title} {artist}", limit=5)
    for t in tidal_res:
        t_artist = clean_string(t['performer']['name'])
        t_title = clean_string(t['title'])
        
        # Vérification Artiste
        match_artist = False
        if FUZZ_AVAILABLE:
            if fuzz.ratio(target_artist, t_artist) > 65: match_artist = True
        elif target_artist in t_artist or t_artist in target_artist: match_artist = True
        
        if match_artist:
            # Vérification Titre (nouveau)
            match_title = False
            if FUZZ_AVAILABLE:
                if fuzz.ratio(target_title, t_title) > 60: match_title = True
            elif target_title in t_title or t_title in target_title: match_title = True
            
            if match_title:
                # On conserve tidal_hund car c'est ce que frontend attend pour streamer
                t['source'] = 'tidal_hund'
                return t
            
    return None

# --- CHOSIC RADIO LOGIC (REMPLACEMENT YOUTUBE) ---

def recuperer_nom_artiste(track):
    """Extrait le nom de l'artiste depuis le format Chosic/Spotify"""
    art = track.get('artist')
    if isinstance(art, dict): return art.get('name', 'Inconnu')
    if isinstance(art, str) and art: return art
    
    arts_list = track.get('artists')
    if isinstance(arts_list, list) and len(arts_list) > 0:
        first = arts_list[0]
        if isinstance(first, dict): return first.get('name', 'Inconnu')
        return str(first)
    return "Artiste Inconnu"

def sync_get_radio_queue(title, artist):
    """
    Radio basée sur Chosic API (Playlist Generator).
    Retourne des résultats tagués 'spotify_lazy' pour que le frontend
    les résolve via le resolveur interne (Qobuz/Tidal) au moment de la lecture.
    """
    logger.info(f"[Radio Chosic] Recherche pour : {title} - {artist}")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:146.0) Gecko/20100101 Firefox/146.0',
        'app': 'playlist_generator',
        'X-Requested-With': 'XMLHttpRequest',
        'Referer': 'https://www.chosic.com/playlist-generator/',
        'Cookie': "pll_language=en; r_34874064=1766964698%7C1f3f97bb0aac03d5%7C7641cb741178463b39344194056e2964620e973ab7b589e0f996a97aa31fd318",
    }
    
    try:
        session = requests.Session()
        query_encoded = urllib.parse.quote(f"{title} {artist}")
        
        # 1. Recherche du morceau "Seed"
        search_url = f"https://www.chosic.com/api/tools/search?q={query_encoded}&type=track&limit=5"
        resp = session.get(search_url, headers=headers, timeout=10)
        
        tracks = resp.json().get('tracks', {}).get('items', [])
        if not tracks:
            logger.warning("[Radio Chosic] Aucun seed trouvé.")
            return []
            
        # Sélection du meilleur match
        best_match = None
        highest_score = 0
        target_str = f"{title} {artist}".lower()
        
        for t in tracks:
            t_name = t.get('name', '')
            t_artist = recuperer_nom_artiste(t)
            curr_str = f"{t_name} {t_artist}".lower()
            
            score = difflib.SequenceMatcher(None, target_str, curr_str).ratio()
            if score > highest_score:
                highest_score = score
                best_match = t
        
        if not best_match: best_match = tracks[0] # Fallback
        
        seed_id = best_match['id']
        logger.info(f"[Radio Chosic] Seed sélectionné : {best_match.get('name')} (ID: {seed_id})")
        
        # 2. Récupération des recommandations
        rec_url = f"https://www.chosic.com/api/tools/recommendations?seed_tracks={seed_id}&limit=25&target_popularity=70"
        rec_resp = session.get(rec_url, headers=headers, timeout=10)
        rec_data = rec_resp.json().get('tracks', [])
        
        final_queue = []
        for t in rec_data:
            # On ignore le titre original s'il est présent
            if t.get('name', '').lower() == title.lower(): continue
            
            art_name = recuperer_nom_artiste(t)
            
            # Extraction image
            img_url = "https://placehold.co/300x300/222/666?text=Music"
            alb = t.get('album', {})
            if isinstance(alb, dict):
                img_url = alb.get('image_large') or (alb.get('images', [{}])[0].get('url')) or img_url
                
            final_queue.append({
                "id": t['id'], # ID Spotify
                "title": t.get('name', 'Titre Inconnu'),
                "performer": { "name": art_name },
                "album": { "title": alb.get('name', 'Album'), "image": { "large": img_url } },
                "img": img_url,
                "duration": int(t.get('duration_ms', 0)) // 1000,
                "source": "spotify_lazy", # Le frontend saura qu'il doit résoudre ce titre
                "isRadio": True
            })
            
        logger.info(f"[Radio Chosic] {len(final_queue)} titres trouvés.")
        return final_queue

    except Exception as e:
        logger.error(f"[Radio Chosic] Erreur : {e}")
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
        tasks.append(run_in_threadpool(sync_search_tidal, q, 50))
    if type in ['album', 'all']:
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 15, 'album'))
        tasks.append(run_in_threadpool(sync_search_tidal_albums, q, 15))
        tasks.append(run_in_threadpool(sync_search_deezer_albums, q, 15))
    if type in ['artist', 'all']:
        tasks.append(run_in_threadpool(sync_search_deezer_artists, q, 15))

    finished = await asyncio.gather(*tasks, return_exceptions=True)
    idx = 0
    qobuz_tracks = []; tidal_tracks = []; deezer_tracks = []
    qobuz_albums = []; tidal_albums = []; deezer_albums = []
    deezer_artists = []

    if type in ['track', 'all']:
        r1 = finished[idx]; idx += 1; qobuz_tracks = r1 if isinstance(r1, list) else []
        r2 = finished[idx]; idx += 1; tidal_tracks = r2 if isinstance(r2, list) else []
        # Pas de Deezer pour les tracks
    if type in ['album', 'all']:
        r4 = finished[idx]; idx += 1; qobuz_albums = r4 if isinstance(r4, list) else []
        r5 = finished[idx]; idx += 1; tidal_albums = r5 if isinstance(r5, list) else []
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
        # 1. Nettoyage Titre (suppression parenthèses, version, etc.)
        t = track.get('title', '').lower()
        t = re.sub(r'\s*[\(\[].*?[\)\]]', '', t) # Enlève (...) et [...]
        t = re.sub(r'\s*-\s*.*', '', t) # Enlève tout après un tiret (ex: - Remaster)
        t = clean_string(t)
        
        # 2. Nettoyage Artiste
        p = track.get('performer', {}).get('name', '')
        if not p: p = track.get('artist', {}).get('name', '')
        p = clean_string(p)
        
        return f"{t}|{p}"

    # Enregistrement des signatures Qobuz
    for t in qobuz_tracks:
        sigs.add(get_dedup_sig(t))
    
    # Insertion Tidal (si pas de doublon)
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

    # Insertion Tidal Albums
    for a in tidal_albums:
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
                    'performer': {'name': t.get('artist', {}).get('name', 'Inconnu')},
                    'album': {'title': t.get('album', {}).get('title'), 'image': {'large': t.get('album', {}).get('cover_xl') or art}},
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
    # Conserve la logique de redirection pour les sources 'lazy' classiques
    match = await run_in_threadpool(sync_resolve_track, title, artist)
    if match:
        rid = match['id']
        if match['source'] == 'tidal_hund': return RedirectResponse(f"/tidal_manifest/{rid}")
        return RedirectResponse(f"/stream/{rid}")
    raise HTTPException(404, "Track not found")

@app.get('/resolve_metadata')
async def resolve_metadata_route(title: str, artist: str):
    """
    Retourne l'objet track complet (ID, source, image, etc.) pour mettre à jour l'interface
    avant la lecture.
    """
    match = await run_in_threadpool(sync_resolve_track, title, artist)
    if match: return JSONResponse(match)
    raise HTTPException(404, "Not found")

@app.get('/track')
async def get_track_info(id: str, source: str = None):
    if source == 'tidal_hund':
        # Pas d'info track spécifique implémentée pour l'instant hors search
        # On renvoie une erreur pour forcer le client à utiliser les données qu'il a déjà
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
    if source == 'tidal_hund':
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
                        'duration': t.get('duration'),
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

@app.get('/stream/{track_id}')
async def stream_track(track_id: str):
    if not client: raise HTTPException(500, "Client error")
    def _get_url():
        for fmt in [27, 7, 6, 5]:
            try:
                d = client.get_track_url(track_id, fmt)
                if 'url' in d: return d['url']
            except: continue
        return None
    url = await run_in_threadpool(_get_url)
    if url: return RedirectResponse(url)
    raise HTTPException(404, "URL not found")

@app.get('/tidal_manifest/{track_id}')
async def get_tidal_manifest_route(track_id: str):
    manifest_data = await run_in_threadpool(get_tidal_stream_manifest, track_id)
    if manifest_data:
        return JSONResponse(manifest_data)
    raise HTTPException(404, "Tidal manifest not found")

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