import sys
import os
import asyncio
from concurrent.futures import ThreadPoolExecutor

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

TIDAL_HUND_BASE = "https://hund.qqdl.site"

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
logger = logging.getLogger("ZenithAsync")

lyrics_engine = LyricsSearcher()
yt = YTMusic()

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

# --- TIDAL / HUND API ---

def sync_search_tidal(query, limit=25):
    try:
        # Recherche sur Hund
        url = f"{TIDAL_HUND_BASE}/search/?s={urllib.parse.quote(query)}"
        res = requests.get(url, timeout=10)
        data = res.json()
        
        results = []
        if 'items' in data:
            # Gestion si items est un dict (ex: indexé par 0, 1, 2) ou une liste
            items = data['items']
            iterable = items.values() if isinstance(items, dict) else items
            
            for t in iterable:
                try:
                    # Détection Hi-Res / 24 bits
                    bit_depth = 16
                    tags = t.get('mediaMetadata', {}).get('tags', [])
                    # Les tags peuvent être un dict ou une liste selon l'API
                    if isinstance(tags, dict): tags = tags.values()
                    if "HIRES_LOSSLESS" in tags or "MQA" in tags:
                        bit_depth = 24
                    
                    track = {
                        'id': str(t['id']),
                        'title': t['title'],
                        'performer': {'name': t.get('artist', {}).get('name', 'Inconnu')},
                        'album': {
                            'title': t.get('album', {}).get('title'),
                            'image': {'large': tidal_uuid_to_url(t.get('album', {}).get('cover'))}
                        },
                        'duration': t.get('duration', 0),
                        'maximum_bit_depth': bit_depth,
                        'source': 'tidal_hund'
                    }
                    results.append(track)
                except Exception as e:
                    continue
        return results[:limit]
    except Exception as e:
        logger.error(f"Tidal Hund Search Error: {e}")
        return []

def get_tidal_stream_manifest(track_id):
    """Récupère le manifeste DASH pour Shaka Player"""
    try:
        url = f"{TIDAL_HUND_BASE}/track/?id={track_id}&quality=HI_RES_LOSSLESS"
        res = requests.get(url, timeout=10)
        data = res.json()
        
        if data.get('data', {}).get('manifestMimeType') == 'application/dash+xml':
            return {
                "manifest": data['data']['manifest'], # Base64 encoded
                "mimeType": "application/dash+xml",
                "bitDepth": data['data'].get('bitDepth', 16),
                "sampleRate": data['data'].get('sampleRate', 44100)
            }
        return None
    except Exception as e:
        logger.error(f"Tidal Manifest Error: {e}")
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
        if target_artist in t_artist or t_artist in target_artist:
            t['source'] = 'tidal_hund'
            return t
            
    return None

def sync_get_radio_queue(title, artist):
    query = f"{title} {artist}"
    try:
        results = yt.search(query, filter="songs", limit=1)
        if not results: return []
        vid = results[0]["videoId"]
        watch = yt.get_watch_playlist(vid, limit=25)
        if "tracks" not in watch: return []
        final = []
        for t in watch["tracks"]:
            if t.get("videoId") == vid: continue
            if not t.get("album") or not t.get("album", {}).get("name"): continue

            img = extract_thumbnail_hd(t)
            final.append({
                "id": t.get("videoId"), "title": t.get("title"),
                "performer": { "name": t.get("artists", [{}])[0].get("name", "Inconnu") },
                "album": { "title": t.get("album", {}).get("name"), "image": { "large": img } },
                "img": img, "duration": parse_duration(t.get("duration") or t.get("length")),
                "source": "yt_lazy", "isRadio": True
            })
        return final
    except Exception as e: return []

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
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 25, 'track'))
        tasks.append(run_in_threadpool(sync_search_tidal, q, 25))
        tasks.append(run_in_threadpool(sync_search_deezer, q, 25))
    if type in ['album', 'all']:
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 15, 'album'))
        tasks.append(run_in_threadpool(sync_search_deezer_albums, q, 15))
    if type in ['artist', 'all']:
        tasks.append(run_in_threadpool(sync_search_deezer_artists, q, 15))

    finished = await asyncio.gather(*tasks, return_exceptions=True)
    idx = 0
    qobuz_tracks = []; tidal_tracks = []; deezer_tracks = []
    qobuz_albums = []; deezer_albums = []
    deezer_artists = []

    if type in ['track', 'all']:
        r1 = finished[idx]; idx += 1; qobuz_tracks = r1 if isinstance(r1, list) else []
        r2 = finished[idx]; idx += 1; tidal_tracks = r2 if isinstance(r2, list) else []
        r3 = finished[idx]; idx += 1; deezer_tracks = r3 if isinstance(r3, list) else []
    if type in ['album', 'all']:
        r4 = finished[idx]; idx += 1; qobuz_albums = r4 if isinstance(r4, list) else []
        r5 = finished[idx]; idx += 1; deezer_albums = r5 if isinstance(r5, list) else []
    if type in ['artist', 'all']:
        r7 = finished[idx]; idx += 1; deezer_artists = r7 if isinstance(r7, list) else []

    deezer_playlists = []
    if type in ['playlist', 'all']:
        deezer_playlists = await run_in_threadpool(sync_search_deezer_playlists, q, 100)

    combined_tracks = qobuz_tracks
    sigs = set()
    for t in qobuz_tracks: sigs.add(f"{clean_string(t['title'])}{clean_string(t.get('performer',{}).get('name'))}")
    
    # Insertion Tidal
    for t in tidal_tracks:
        s = f"{clean_string(t['title'])}{clean_string(t['performer']['name'])}"
        if s not in sigs: combined_tracks.append(t); sigs.add(s)
        
    for t in deezer_tracks:
        s = f"{clean_string(t['title'])}{clean_string(t['performer']['name'])}"
        if s not in sigs: combined_tracks.append(t); sigs.add(s)

    combined_albums = qobuz_albums
    album_sigs = set()
    for a in qobuz_albums: album_sigs.add(f"{clean_string(a['title'])}{clean_string(a.get('artist',{}).get('name'))}")
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
    if source == 'deezer':
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