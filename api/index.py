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

# Imports locaux (Logique métier existante)
try:
    from qobuz_api import QobuzClient, get_app_credentials
    from lyrics_search import LyricsSearcher 
except ImportError:
    from .qobuz_api import QobuzClient, get_app_credentials
    from .lyrics_search import LyricsSearcher

import logging
import random
import requests
import re
import urllib.parse
import hashlib
import string
import unicodedata 
from ytmusicapi import YTMusic

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

SUBSONIC_BASE = "https://api.401658.xyz/rest/"
SUBSONIC_USER = "toinoulebest"
SUBSONIC_PASSWORD = "EbPNO8NRaEko" 
SUBSONIC_CLIENT = "Feishin"
SUBSONIC_VERSION = "1.13.0"

SUPABASE_PROXY_URL = "https://mzxfcvzqxgslyopkkaej.supabase.co/functions/v1/stream-proxy"

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
    s = (ms / 1000); m = int(s // 60); rs = s % 60
    return f"[{m:02d}:{rs:05.2f}]"

# --- SUBSONIC ---
def get_subsonic_query_params():
    salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
    token = hashlib.md5((SUBSONIC_PASSWORD + salt).encode('utf-8')).hexdigest()
    return { 'u': SUBSONIC_USER, 's': salt, 't': token, 'v': SUBSONIC_VERSION, 'c': SUBSONIC_CLIENT, 'f': 'json' }

def fetch_subsonic_raw(endpoint, params):
    try:
        url = SUBSONIC_BASE + endpoint
        p = get_subsonic_query_params()
        p.update(params)
        res = requests.get(url, params=p, timeout=5)
        return res.json()
    except: return {}

# --- FONCTIONS SYNCHRONES (WRAPPED) ---
# Ces fonctions utilisent des lib synchrones (requests, ytmusicapi).
# On les définit normalement, et on les appellera via run_in_threadpool dans les routes.

def sync_search_yt_lyrics(title, artist):
    """
    Cherche les paroles sur YouTube Music.
    Retourne un dictionnaire {'type': 'synced'|'plain', 'lyrics': str} ou None.
    """
    query = f"{title} {artist}"
    try:
        results = yt.search(query, filter="songs", limit=1)
        if not results: return None
        video_id = results[0]['videoId']
        watch = yt.get_watch_playlist(video_id)
        if not watch or 'lyrics' not in watch or not watch['lyrics']: return None
        
        # 1. Tentative Sync (Priorité)
        try:
            lyrics_data = yt.get_lyrics(watch['lyrics'], timestamps=True)
            if lyrics_data and isinstance(lyrics_data.get('lyrics'), list):
                lines = []
                for line in lyrics_data['lyrics']:
                    txt = line.get('text', line.get('line', ''))
                    # Gestion timestamp flexible
                    t = line.get('start_time', line.get('seconds', line.get('startTime')))
                    if t is not None: lines.append(f"{ms_to_lrc(float(t)*1000)} {txt}")
                
                if lines:
                    return {"type": "synced", "lyrics": "\n".join(lines)}
        except Exception:
            pass

        # 2. Tentative Plain (Fallback si pas de timestamps)
        try:
            lyrics_data = yt.get_lyrics(watch['lyrics']) # timestamps=False par défaut
            if lyrics_data and lyrics_data.get('lyrics'):
                return {"type": "plain", "lyrics": lyrics_data['lyrics']}
        except Exception:
            pass
            
        return None
    except Exception:
        return None

def sync_search_subsonic(query, limit=20):
    data = fetch_subsonic_raw("search3.view", {'query': query, 'songCount': limit, 'albumCount': 0, 'artistCount': 0})
    if data.get('subsonic-response', {}).get('status') == 'ok':
        songs = data['subsonic-response'].get('searchResult3', {}).get('song', [])
        return [{
            'id': s.get('id'), 'title': s.get('title'), 'performer': {'name': s.get('artist', 'Inconnu')},
            'album': {'title': s.get('album'), 'image': {'large': s.get('coverArt')}},
            'duration': s.get('duration', 0), 'maximum_bit_depth': 16, 'source': 'subsonic'
        } for s in songs]
    return []

def sync_search_subsonic_albums(query, limit=15):
    data = fetch_subsonic_raw("search3.view", {'query': query, 'songCount': 0, 'albumCount': limit, 'artistCount': 0})
    if data.get('subsonic-response', {}).get('status') == 'ok':
        albums = data['subsonic-response'].get('searchResult3', {}).get('album', [])
        return [{
            'id': a.get('id'), 'title': a.get('name', a.get('title')), 
            'artist': {'name': a.get('artist')}, 'image': {'large': a.get('coverArt')}, 'source': 'subsonic'
        } for a in albums]
    return []

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

def sync_search_artist_full(name):
    """Recherche un artiste sur Qobuz et retourne ses infos + albums + top tracks"""
    if not client: return None
    try:
        # 1. Rechercher l'artiste pour avoir son ID
        r = client.api_call("artist/search", query=name, limit=1)
        items = r.get('artists', {}).get('items', [])
        if not items: return None
        
        artist = items[0]
        artist_id = artist['id']
        
        # 2. Récupérer les détails (albums + bio)
        # Ajout de 'biography' dans les extras
        details = client.api_call("artist/get", id=artist_id, extra="albums,biography", limit=50)
        
        # Extraction de la biographie (nettoyage HTML basique si nécessaire)
        bio = ""
        if 'biography' in details and details['biography']:
            # Qobuz renvoie souvent un dict { 'content': '...', 'language': '...' }
            # ou parfois juste le texte selon la version de l'API
            raw_bio = details['biography'].get('content', '')
            # Nettoyage simple des balises HTML
            bio = re.sub(r'<[^>]+>', '', raw_bio).strip()
        
        albums = []
        if 'albums' in details and 'items' in details['albums']:
            for a in details['albums']['items']:
                # FILTRAGE STRICT : On ne garde que les albums où l'artiste est l'artiste principal
                if str(a.get('artist', {}).get('id')) == str(artist_id):
                    a['source'] = 'qobuz'
                    albums.append(a)
                
        # 3. Récupérer les Top Tracks (Simulé par recherche)
        top_tracks = []
        track_search = client.api_call("track/search", query=artist['name'], limit=20)
        if 'tracks' in track_search and 'items' in track_search['tracks']:
            for t in track_search['tracks']['items']:
                # Filtrage : l'artiste doit être listé dans 'performer' ou 'artist'
                # On vérifie si l'ID de l'artiste apparaît dans les métadonnées
                is_related = False
                
                # Vérif artiste principal
                if str(t.get('artist', {}).get('id')) == str(artist_id):
                    is_related = True
                # Vérif performer (plus complexe car texte)
                elif 'performer' in t and artist['name'].lower() in t['performer']['name'].lower():
                    is_related = True
                
                if is_related:
                    t['source'] = 'qobuz'
                    fix_qobuz_title(t)
                    top_tracks.append(t)
            
            # On limite à 10 après filtrage
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
        logger.error(f"Artist Full Search Error: {e}")
        return None

def sync_resolve_track(title, artist):
    target_artist = clean_string(artist)
    target_title = clean_string(title)
    
    # 1. Qobuz
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
                    
                    if match_title: return {'id': rec['id'], 'source': 'qobuz'}
        except: pass
        
    # 2. Subsonic
    subs = sync_search_subsonic(f"{title} {artist}", limit=5)
    for s in subs:
        s_artist = clean_string(s['performer']['name'])
        if target_artist in s_artist or s_artist in target_artist:
            return {'id': s['id'], 'source': 'subsonic'}
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
            img = extract_thumbnail_hd(t)
            final.append({
                "id": t.get("videoId"), "title": t.get("title"),
                "performer": { "name": t.get("artists", [{}])[0].get("name", "Inconnu") },
                "album": { "title": t.get("album", {}).get("name"), "image": { "large": img } },
                "img": img, "duration": parse_duration(t.get("duration") or t.get("length")),
                "source": "yt_lazy", "isRadio": True
            })
        return final
    except Exception as e:
        logger.error(f"Radio Error: {e}")
        return []

# --- ROUTES ASYNCHRONES ---

@app.get('/radio_queue')
async def get_radio_queue(artist: str, title: str):
    if not artist or not title: raise HTTPException(400, "Missing params")
    # Exécution dans un threadpool pour ne pas bloquer
    tracks = await run_in_threadpool(sync_get_radio_queue, title, artist)
    if not tracks: raise HTTPException(404, "No results")
    return JSONResponse(tracks)

@app.get('/search')
async def search_tracks(q: str, type: str = 'all'):
    results = { "tracks": [], "albums": [], "external_playlists": [] }
    
    # Lancement parallèle des recherches
    tasks = []
    
    if type in ['track', 'all']:
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 25, 'track'))
        tasks.append(run_in_threadpool(sync_search_subsonic, q, 25))
    
    if type in ['album', 'all']:
        tasks.append(run_in_threadpool(sync_qobuz_search, q, 15, 'album'))
        tasks.append(run_in_threadpool(sync_search_subsonic_albums, q, 15))
        
    # On attend tout
    finished = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Traitement des résultats (Mapping un peu brut selon l'ordre)
    idx = 0
    qobuz_tracks = []
    subsonic_tracks = []
    qobuz_albums = []
    subsonic_albums = []

    if type in ['track', 'all']:
        r1 = finished[idx]; idx += 1
        if isinstance(r1, list): qobuz_tracks = r1
        r2 = finished[idx]; idx += 1
        if isinstance(r2, list): subsonic_tracks = r2
        
    if type in ['album', 'all']:
        r3 = finished[idx]; idx += 1
        if isinstance(r3, list): qobuz_albums = r3
        r4 = finished[idx]; idx += 1
        if isinstance(r4, list): subsonic_albums = r4

    # Recherche YouTube Playlists (Séparée car moins critique)
    yt_playlists = []
    if type in ['playlist', 'all']:
        try:
            yt_res = await run_in_threadpool(yt.search, q, filter='playlists', limit=10)
            for item in yt_res:
                try:
                    img = extract_thumbnail_hd(item)
                    yt_playlists.append({
                        "id": item.get('browseId'), "name": item.get('title'),
                        "title": item.get('title'), "performer": { "name": item.get('author', 'YouTube') },
                        "type": "playlist", "source": "ytmusic", "image": img, "is_lazy": True
                    })
                except: continue
        except: pass

    # Fusion intelligente
    combined_tracks = qobuz_tracks
    sigs = set(f"{clean_string(t['title'])}{clean_string(t.get('performer',{}).get('name'))}" for t in qobuz_tracks)
    
    for t in subsonic_tracks:
        s = f"{clean_string(t['title'])}{clean_string(t['performer']['name'])}"
        if s not in sigs: combined_tracks.append(t)
        
    combined_albums = qobuz_albums + subsonic_albums
    
    return JSONResponse({
        "tracks": combined_tracks,
        "albums": combined_albums,
        "external_playlists": yt_playlists
    })

@app.get('/blind_test_tracks')
async def get_blind_test_tracks(theme: str = 'Global Hits', limit: int = 5):
    # Logique simplifiée pour la performance : Qobuz direct
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
    except:
        raise HTTPException(500, "Error")

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
                        "id": t.get('videoId'), "title": t.get('title'),
                        "performer": { "name": t.get('artists', [{'name':'Inconnu'}])[0]['name'] },
                        "album": { "title": details.get('title'), "image": { "large": img } },
                        "duration": parse_duration(t.get('duration') or t.get('lengthSeconds')),
                        "source": "yt_lazy", "type": "track", "img": img
                    })
                except: continue
            return { "id": id, "title": details.get('title'), "tracks": tracks, "image": art }
        except Exception as e: return {"error": str(e)}

    res = await run_in_threadpool(_fetch)
    if "error" in res: raise HTTPException(500, res["error"])
    return JSONResponse(res)

@app.get('/resolve_stream')
async def resolve_and_stream(title: str, artist: str):
    match = await run_in_threadpool(sync_resolve_track, title, artist)
    if match:
        rid = match['id']
        if match['source'] == 'subsonic': return RedirectResponse(f"/stream_subsonic/{rid}")
        return RedirectResponse(f"/stream/{rid}")
    raise HTTPException(404, "Track not found")

@app.get('/track')
async def get_track_info(id: str, source: str = None):
    if source == 'subsonic':
        # Subsonic fetch
        data = await run_in_threadpool(fetch_subsonic_raw, "getSong.view", {'id': id})
        s = data.get('subsonic-response', {}).get('song')
        if s:
            return JSONResponse({
                'id': s['id'], 'title': s['title'], 'performer': {'name': s['artist']},
                'album': {'title': s.get('album'), 'image': {'large': s.get('coverArt')}},
                'duration': s.get('duration'), 'source': 'subsonic', 'maximum_bit_depth': 16
            })
    elif client:
        try:
            res = await run_in_threadpool(client.get_track_meta, id)
            res['source'] = 'qobuz'; fix_qobuz_title(res)
            return JSONResponse(res)
        except: pass
    raise HTTPException(404)

@app.get('/album')
async def get_album(id: str, source: str = None):
    if source == 'subsonic':
        data = await run_in_threadpool(fetch_subsonic_raw, "getAlbum.view", {'id': id})
        raw = data.get('subsonic-response', {}).get('album')
        if raw:
            tracks = []
            for s in raw.get('song', []):
                tracks.append({ 'id': s['id'], 'title': s['title'], 'duration': s.get('duration'), 'track_number': s.get('track'), 'performer': {'name': s.get('artist', raw.get('artist'))}, 'album': {'title': raw.get('name'), 'image': {'large': raw.get('coverArt')}}, 'source': 'subsonic' })
            return JSONResponse({ 'id': raw['id'], 'title': raw.get('name'), 'artist': {'name': raw.get('artist')}, 'image': {'large': raw.get('coverArt')}, 'source': 'subsonic', 'tracks': {'items': tracks} })
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
        # On utilise api_call directement car get_artist n'est pas standardisé
        meta = await run_in_threadpool(client.api_call, "artist/get", id=id, extra="albums", limit=50)
        # Normalisation légère
        albums = []
        if 'albums' in meta and 'items' in meta['albums']: albums = meta['albums']['items']
        meta['albums'] = {'items': albums}
        return JSONResponse(meta)
    except: raise HTTPException(404)

@app.get('/artist_bio')
async def get_artist_bio_route(name: str):
    data = await run_in_threadpool(sync_search_artist_full, name)
    if data:
        return JSONResponse(data)
    else:
        # Fallback si non trouvé
        return JSONResponse({"bio": f"Artiste non trouvé : {name}", "image": "", "nb_fans": 0, "top_tracks": [], "albums": []})

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
    if url: return RedirectResponse(f"{SUPABASE_PROXY_URL}?url={urllib.parse.quote(url)}")
    raise HTTPException(404, "URL not found")

@app.get('/stream_subsonic/{track_id}')
async def stream_subsonic(track_id: str):
    p = get_subsonic_query_params()
    p.update({'id': track_id, 'format': 'mp3', 'maxBitRate': '320'})
    # Construction manuelle de l'URL
    query = urllib.parse.urlencode(p)
    full_url = f"{SUBSONIC_BASE}stream.view?{query}"
    return RedirectResponse(f"{SUPABASE_PROXY_URL}?url={urllib.parse.quote(full_url)}")

@app.get('/get_subsonic_cover/{cover_id}')
async def get_subsonic_cover(cover_id: str):
    p = get_subsonic_query_params()
    p.update({'id': cover_id, 'size': 600})
    query = urllib.parse.urlencode(p)
    return RedirectResponse(f"{SUBSONIC_BASE}getCoverArt.view?{query}")

@app.get('/lyrics')
async def get_lyrics(artist: str, title: str, album: str = None, duration: str = None):
    # 1. YouTube (Synced ou Plain)
    # On récupère d'abord YouTube, car l'utilisateur les préfère
    yt_res = await run_in_threadpool(sync_search_yt_lyrics, title, artist)
    
    # Si YouTube a du synchronisé, c'est le jackpot, on renvoie direct
    if yt_res and yt_res['type'] == 'synced':
        return JSONResponse({"type": "synced", "lyrics": yt_res['lyrics'], "source": "YouTube"})
    
    # 2. LRCLib (Synced)
    # Si YouTube n'a pas de sync, on regarde si LRCLib en a (car sync > plain)
    dur = parse_duration(duration)
    lrc_plain, lrc_synced = None, None
    try:
        lrc_plain, lrc_synced = await run_in_threadpool(lyrics_engine.search_lyrics, artist, title, album, dur)
    except Exception:
        pass
    
    if lrc_synced:
        return JSONResponse({"type": "synced", "lyrics": lrc_synced, "source": "LRCLib"})
        
    # 3. Fallback Plain (YouTube vs LRCLib)
    # Si aucun n'a de sync, on préfère le texte simple de YouTube
    if yt_res and yt_res['type'] == 'plain':
         return JSONResponse({"type": "plain", "lyrics": yt_res['lyrics'], "source": "YouTube"})
         
    # Sinon texte simple de LRCLib
    if lrc_plain:
        return JSONResponse({"type": "plain", "lyrics": lrc_plain, "source": "LRCLib"})
    
    raise HTTPException(404, "No lyrics")

# --- STATIC FILES ---
# Le frontend
app.mount("/", StaticFiles(directory=PROJECT_ROOT, html=True), name="static")

# Pas de "if __name__ == '__main__'" car lancé par uvicorn via server.py ou Vercel