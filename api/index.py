secondes).">
import sys
import os

# 1. Configuration des chemins pour Docker/Coolify
# On ajoute le dossier courant au path pour que les imports (qobuz_api, etc.) fonctionnent
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

# On définit la racine du projet (un niveau au-dessus de api/) pour trouver index.html
PROJECT_ROOT = os.path.abspath(os.path.join(current_dir, '..'))

from flask import Flask, jsonify, redirect, request, send_file, Response, stream_with_context, send_from_directory
from flask_cors import CORS

# Imports locaux (maintenant que le sys.path est configuré)
from qobuz_api import QobuzClient, get_app_credentials
from lyrics_search import LyricsSearcher 

import logging
import random
import requests
import json 
from pathlib import Path 
import re
import urllib.parse
import hashlib
import string
import unicodedata 
from concurrent.futures import ThreadPoolExecutor, as_completed
from ytmusicapi import YTMusic

# Tentative d'import pour la comparaison floue (Fuzzy Matching)
try:
    from rapidfuzz import fuzz
    FUZZ_AVAILABLE = True
except ImportError:
    FUZZ_AVAILABLE = False

# --- CONFIGURATION ---
USER_ID = '7610812'
TOKEN = 'wTJvd-7fc8haH3zdRrZYqcULUQ1wA6wJBLNmDkn38JaMrfRtHlaGpSVLHN0205rSQ23psXhJrnQNrRmEiGS-zw' 
APP_ID = '798273057'

# --- CONFIGURATION SUBSONIC ---
SUBSONIC_BASE = "https://api.401658.xyz/rest/"
SUBSONIC_USER = "toinoulebest"
SUBSONIC_PASSWORD = "EbPNO8NRaEko" 
SUBSONIC_CLIENT = "Feishin"
SUBSONIC_VERSION = "1.13.0"

# --- CONFIGURATION SUPABASE PROXY ---
SUPABASE_PROXY_URL = "https://mzxfcvzqxgslyopkkaej.supabase.co/functions/v1/stream-proxy"

app = Flask(__name__, static_folder=PROJECT_ROOT, static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ZenithServer")

lyrics_engine = LyricsSearcher()

# --- INITIALISATION YTMUSIC ---
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
            "User-Agent": "Zenith Player",
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

def clean_title_for_search(title):
    if not title: return ""
    title = re.sub(r" ?[\(\[].*?[\)\]]", "", title)
    title = re.split(r" feat\.| ft\.| with ", title, flags=re.IGNORECASE)[0]
    return title.strip()

def fix_qobuz_title(track):
    try:
        if 'version' in track and track['version']:
            ver = str(track['version']).strip()
            if ver.lower() not in ['album version', 'original version', 'standard version', 'remastered', 'remaster', 'single version']:
                if ver.lower() not in track['title'].lower():
                    track['title'] = f"{track['title']} ({ver})"
    except: pass
    return track

def extract_thumbnail_hd(track):
    """
    Récupère la miniature de manière robuste (supporte thumbnail, thumbnails, liste ou dict)
    et force la HD.
    """
    thumbs = []
    
    # Recherche dans les clés possibles
    for key in ["thumbnails", "thumbnail"]:
        if key in track and track[key]:
            data = track[key]
            if isinstance(data, list):
                thumbs = data
                break
            elif isinstance(data, dict):
                # Parfois c'est imbriqué
                if "thumbnails" in data:
                    thumbs = data["thumbnails"]
                    break
                else:
                    # Ou c'est un dict unique
                    thumbs = [data]
                    break
            elif isinstance(data, str):
                # C'est une URL directe
                return get_hq_yt_image(data)

    if not thumbs:
        return 'https://placehold.co/300x300/1a1a1a/666666?text=Music'

    # Tri pour avoir la plus grande image
    try:
        thumbs.sort(key=lambda x: x.get("width", 0))
    except: pass 

    # Récupération de l'URL la plus grande
    best_url = thumbs[-1].get("url")
    if not best_url:
        return 'https://placehold.co/300x300/1a1a1a/666666?text=Music'

    return get_hq_yt_image(best_url)

def get_hq_yt_image(url):
    """Force la haute résolution sur une URL YouTube/Google"""
    if not url: return 'https://placehold.co/300x300/1a1a1a/666666?text=Music'
    
    # Remplacement standard pour les URL googleusercontent
    # Remplace w<N>-h<N> par w1200-h1200
    if '=w' in url:
        return re.sub(r'=w\d+-h\d+', '=w1200-h1200', url)
    
    # Format alternatif
    return re.sub(r'w\d+-h\d+(-l\d+)?', 'w1200-h1200-l100', url)

def parse_duration(d):
    """Convertit une durée 'MM:SS' ou int en secondes"""
    if not d: return 0
    if isinstance(d, int) or isinstance(d, float): return int(d)
    if isinstance(d, str):
        if ':' in d:
            try:
                parts = d.split(':')
                if len(parts) == 2:
                    return int(parts[0]) * 60 + int(parts[1])
                elif len(parts) == 3:
                    return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            except: pass
    return 0

def ms_to_lrc(ms):
    """Convertit des millisecondes en format timestamp LRC [mm:ss.xx]"""
    seconds = (ms / 1000)
    minutes = int(seconds // 60)
    rem_seconds = seconds % 60
    return f"[{minutes:02d}:{rem_seconds:05.2f}]"

def fetch_yt_synced_lyrics(title, artist):
    """Récupère les paroles synchronisées depuis YouTube Music avec Logs détaillés"""
    query = f"{title} {artist}"
    logger.info(f"🔍 YT Lyrics: Recherche pour '{query}'")
    
    try:
        results = yt.search(query, filter="songs", limit=1)
        if not results:
            logger.warning(f"❌ YT Lyrics: Aucun résultat de recherche pour '{query}'")
            return None
        
        video_id = results[0]['videoId']
        track_title = results[0].get('title', 'Inconnu')
        logger.info(f"✅ YT Lyrics: Vidéo trouvée [{video_id}] - {track_title}")
        
        try:
            watch = yt.get_watch_playlist(video_id)
        except Exception as e:
            logger.error(f"❌ YT Lyrics: Erreur lors de get_watch_playlist: {e}")
            return None

        if not watch or 'lyrics' not in watch or not watch['lyrics']:
            logger.warning(f"❌ YT Lyrics: Pas d'ID de paroles disponible pour cette vidéo.")
            return None
            
        lyrics_id = watch['lyrics']
        logger.info(f"✅ YT Lyrics: ID Paroles trouvé [{lyrics_id}]")
        
        lyrics_data = None
        try:
            lyrics_data = yt.get_lyrics(lyrics_id, timestamps=True)
        except TypeError:
            logger.warning("⚠️ YT Lyrics: L'option timestamps=True n'est pas supportée.")
        except Exception as e:
            logger.error(f"⚠️ YT Lyrics: Erreur avec timestamps=True: {e}")

        if not lyrics_data:
            try:
                lyrics_data = yt.get_lyrics(lyrics_id)
            except Exception as e:
                logger.error(f"❌ YT Lyrics: Erreur fallback: {e}")
                return None
        
        if not lyrics_data: return None

        lyrics_content = lyrics_data.get('lyrics')

        if isinstance(lyrics_content, list):
            lrc_lines = []
            for i, line in enumerate(lyrics_content):
                try:
                    t = None
                    txt = ""
                    is_ms = False
                    if hasattr(line, 'start_time') and hasattr(line, 'text'):
                        t = line.start_time; txt = line.text; is_ms = True 
                    elif isinstance(line, dict):
                        t = line.get('start_time', line.get('seconds', line.get('startTime')))
                        txt = line.get('text', line.get('line', ''))
                        is_ms = False 
                    
                    if t is not None:
                         val = float(t)
                         final_ms = val if is_ms else (val * 1000)
                         lrc_lines.append(f"{ms_to_lrc(final_ms)} {txt}")
                except Exception as ex: continue
            
            if lrc_lines: return "\n".join(lrc_lines)

        elif isinstance(lyrics_content, str): return None 
        return None
        
    except Exception as e:
        logger.error(f"💥 YT Lyrics: Exception non gérée: {e}")
        return None

# --- HELPERS SUBSONIC ---
def get_subsonic_query_params():
    salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
    token_str = SUBSONIC_PASSWORD + salt
    token = hashlib.md5(token_str.encode('utf-8')).hexdigest()
    return { 'u': SUBSONIC_USER, 's': salt, 't': token, 'v': SUBSONIC_VERSION, 'c': SUBSONIC_CLIENT, 'f': 'json' }

def get_subsonic_track_details(track_id):
    """Récupère les détails complets d'une musique Subsonic par ID"""
    url = SUBSONIC_BASE + "getSong.view"
    params = get_subsonic_query_params()
    params['id'] = track_id
    try:
        res = requests.get(url, params=params).json()
        song = res['subsonic-response']['song']
        return {
            'id': song['id'],
            'title': song['title'],
            'performer': {'name': song['artist']},
            'album': {'title': song.get('album', 'Album'), 'image': {'large': song.get('coverArt')}},
            'duration': song.get('duration', 0),
            'source': 'subsonic',
            'maximum_bit_depth': 16
        }
    except Exception as e:
        logger.error(f"Subsonic Details Error: {e}")
        return None

# --- API SUBSONIC ---
def fetch_subsonic_tracks(query: str, limit=20) -> list:
    url = SUBSONIC_BASE + "search3.view"
    params = get_subsonic_query_params()
    params.update({ 'query': query, 'songCount': limit, 'albumCount': 0, 'artistCount': 0 })
    try:
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        if data.get('subsonic-response', {}).get('status') == 'ok':
            raw_songs = data['subsonic-response'].get('searchResult3', {}).get('song', [])
            found = []
            for song in raw_songs:
                try:
                    found.append({
                        'id': song.get('id'),
                        'title': song.get('title'),
                        'performer': {'name': song.get('artist', 'Inconnu')},
                        'album': { 'title': song.get('album', 'Album'), 'image': {'large': song.get('coverArt')}},
                        'duration': song.get('duration', 0),
                        'maximum_bit_depth': 16, 
                        'source': 'subsonic'
                    })
                except: continue
            return found
        return []
    except: return []

def fetch_subsonic_albums(query: str, limit=15) -> list:
    url = SUBSONIC_BASE + "search3.view"
    params = get_subsonic_query_params()
    params.update({ 'query': query, 'songCount': 0, 'albumCount': limit, 'artistCount': 0 })
    try:
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        if data.get('subsonic-response', {}).get('status') == 'ok':
            raw_albums = data['subsonic-response'].get('searchResult3', {}).get('album', [])
            found = []
            for album in raw_albums:
                try:
                    found.append({
                        'id': album.get('id'),
                        'title': album.get('name', album.get('title', 'Album')),
                        'artist': {'name': album.get('artist', 'Inconnu')},
                        'image': {'large': album.get('coverArt')},
                        'source': 'subsonic'
                    })
                except: continue
            return found
        return []
    except: return []

def threaded_qobuz_search(query, limit=25, type='track'):
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

def try_resolve_track(title, artist):
    """Tente de trouver un ID Qobuz ou Subsonic avec vérification stricte de l'artiste"""
    search_query = f"{title} {artist}"
    target_artist_clean = clean_string(artist)
    target_title_clean = clean_string(title)
    
    # 1. Qobuz
    if client:
        try:
            # On demande plus de résultats (5) pour avoir le choix
            q_resp = client.api_call("track/search", query=search_query, limit=5)
            items = q_resp.get('tracks', {}).get('items', [])
            
            for rec in items:
                rec_title_clean = clean_string(rec['title'])
                performer_dict = rec.get('performer') or rec.get('artist') or {}
                rec_artist_clean = clean_string(performer_dict.get('name', ''))
                
                # A. Vérification Artiste (Strict ou Fuzzy)
                artist_match = False
                if FUZZ_AVAILABLE:
                    if fuzz.ratio(target_artist_clean, rec_artist_clean) > 65: artist_match = True
                    elif target_artist_clean in rec_artist_clean or rec_artist_clean in target_artist_clean: artist_match = True
                else:
                    if target_artist_clean in rec_artist_clean or rec_artist_clean in target_artist_clean: artist_match = True
                
                if not artist_match: continue

                # B. Vérification Titre
                title_match = False
                if FUZZ_AVAILABLE:
                    if fuzz.ratio(target_title_clean, rec_title_clean) > 60: title_match = True
                else:
                    if target_title_clean in rec_title_clean or rec_title_clean in target_title_clean: title_match = True
                
                if not title_match: continue

                # C. Filtre Anti-Cover (Si le mot cover n'est pas dans la requête)
                if "cover" not in target_title_clean and "cover" in rec_title_clean: continue
                if "tribute" not in target_title_clean and "tribute" in rec_title_clean: continue
                if "karaoke" not in target_title_clean and "karaoke" in rec_title_clean: continue

                # Si tout est bon, on prend celui-là
                return {'id': rec['id'], 'source': 'qobuz'}

        except Exception as e:
            logger.error(f"Resolve Qobuz error: {e}")
    
    # 2. Subsonic
    subs = fetch_subsonic_tracks(search_query, limit=5)
    for song in subs:
        s_artist = clean_string(song['performer']['name'])
        if target_artist_clean in s_artist or s_artist in target_artist_clean:
             return {'id': song['id'], 'source': 'subsonic'}
    
    return None

# --- ROUTES ---

@app.route('/radio_queue')
def get_radio_queue():
    """
    Récupère la file d'attente complète "Watch Next" de YouTube Music avec Images HD.
    """
    artist = request.args.get('artist')
    title = request.args.get('title')
    
    if not artist or not title:
        return jsonify({"error": "Missing artist or title"}), 400
        
    query = f"{title} {artist}"
    logger.info(f"📻 [RADIO QUEUE] Recherche de la graine : {query}")
    
    try:
        # 1. Recherche du titre sur YT Music pour avoir le VideoID
        results = yt.search(query, filter="songs", limit=1)
        
        if not results:
            return jsonify({"error": "Track not found on YT"}), 404
            
        first_track = results[0]
        video_id = first_track["videoId"]
        logger.info(f"✅ [RADIO QUEUE] Seed trouvée : {video_id} - {first_track.get('title')}")
        
        # 2. Récupération de la playlist "Watch Next"
        watch = yt.get_watch_playlist(video_id, limit=25)
        
        if "tracks" not in watch or not watch["tracks"]:
            return jsonify({"error": "No recommendations found"}), 404
            
        follow_tracks = []
        
        for t in watch["tracks"]:
            # On ignore la première piste car c'est celle qu'on écoute déjà
            if t.get("videoId") == video_id:
                continue
                
            artists = t.get("artists", [{}])
            artist_name = artists[0].get("name") if artists else "Inconnu"
            album_name = t.get("album", {}).get("name") if t.get("album") else None
            
            # UTILISATION DE LA FONCTION HD
            img_url = extract_thumbnail_hd(t)
            
            # Gestion de la durée (YT renvoie parfois une string "3:45", parfois des secondes)
            duration_raw = t.get("duration") or t.get("length")
            duration_sec = parse_duration(duration_raw)
                
            follow_tracks.append({
                "id": t.get("videoId"),
                "title": t.get("title"),
                "performer": { "name": artist_name },
                "album": { "title": album_name, "image": { "large": img_url } },
                "img": img_url,
                "thumbnail": img_url,
                "duration": duration_sec,
                "source": "yt_lazy",
                "isRadio": True
            })
            
        logger.info(f"✅ [RADIO QUEUE] {len(follow_tracks)} titres récupérés.")
        return jsonify(follow_tracks)
        
    except Exception as e:
        logger.error(f"❌ [RADIO QUEUE] Erreur: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/blind_test_tracks')
def get_blind_test_tracks():
    theme = request.args.get('theme', 'Global Hits')
    try:
        limit = int(request.args.get('limit', 5))
        if limit < 1: limit = 1
        if limit > 20: limit = 20
    except ValueError:
        limit = 5
        
    logger.info(f"🎲 Blind Test: Thème '{theme}' pour {limit} titres.")

    if not client: 
        return jsonify({"error": "Client not initialized"}), 500

    tracks_found = []

    if theme in ['Global Hits', 'Pop Global']:
        try:
            resp = client.api_call("track/search", query=theme, limit=max(20, limit * 3))
            items = resp.get('tracks', {}).get('items', [])
            random.shuffle(items)
            candidates = items[:limit + 5]
            for track in candidates:
                tracks_found.append({
                    'id': track['id'],
                    'title': track['title'],
                    'artist': track.get('performer', {}).get('name', track.get('artist', {}).get('name', 'Unknown')),
                    'album': track['album']['title'],
                    'img': track.get('album', {}).get('image', {}).get('large', '').replace('_300', '_600'),
                    'duration': track['duration'],
                    'source': 'qobuz'
                })
            return jsonify(tracks_found[:limit])
        except Exception as e:
            logger.error(f"Blind Test Classic Error: {e}")
            return jsonify({"error": "Failed to fetch tracks"}), 500

    try:
        search_results = yt.search(theme, filter='playlists', limit=3)
        if not search_results:
            return jsonify({"error": "No playlist found"}), 404
            
        target_playlist = search_results[0]
        playlist_id = target_playlist['browseId']
        logger.info(f"🎲 Blind Test: Playlist YT trouvée = {target_playlist.get('title')} ({playlist_id})")
        
        playlist_data = yt.get_playlist(playlist_id, limit=50)
        yt_tracks = playlist_data.get('tracks', [])
        random.shuffle(yt_tracks)
        
        candidates_to_resolve = yt_tracks[:limit * 2]
        
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_track = {}
            for t in candidates_to_resolve:
                title = t.get('title')
                artists = t.get('artists', [])
                artist = artists[0]['name'] if artists else "Unknown"
                if title and artist:
                    future_to_track[executor.submit(try_resolve_track, title, artist)] = (title, artist)

            for future in as_completed(future_to_track):
                orig_title, orig_artist = future_to_track[future]
                try:
                    match = future.result()
                    if match:
                        real_id = match['id']
                        source = match['source']
                        final_track = None
                        if source == 'qobuz':
                             meta = client.get_track_meta(real_id)
                             final_track = {
                                'id': meta['id'],
                                'title': meta['title'],
                                'artist': meta.get('performer', {}).get('name', 'Unknown'),
                                'album': meta.get('album', {}).get('title'),
                                'img': meta.get('album', {}).get('image', {}).get('large', '').replace('_300', '_600'),
                                'duration': meta['duration'],
                                'source': 'qobuz'
                             }
                        elif source == 'subsonic':
                             meta = get_subsonic_track_details(real_id)
                             if meta:
                                 final_track = {
                                    'id': meta['id'],
                                    'title': meta['title'],
                                    'artist': meta['performer']['name'],
                                    'album': meta['album']['title'],
                                    'img': f"{API_BASE}/get_subsonic_cover/{meta['album']['image']['large']}" if meta['album']['image']['large'] else "",
                                    'duration': meta['duration'],
                                    'source': 'subsonic'
                                 }
                        if final_track:
                            tracks_found.append(final_track)
                except Exception as e:
                    logger.error(f"Resolution error for {orig_title}: {e}")

        if len(tracks_found) < limit:
            try:
                q_resp = client.api_call("track/search", query=theme, limit=limit * 2)
                items = q_resp.get('tracks', {}).get('items', [])
                for track in items:
                     if len(tracks_found) >= limit: break
                     tracks_found.append({
                        'id': track['id'],
                        'title': track['title'],
                        'artist': track.get('performer', {}).get('name', 'Unknown'),
                        'album': track['album']['title'],
                        'img': track.get('album', {}).get('image', {}).get('large', '').replace('_300', '_600'),
                        'duration': track['duration'],
                        'source': 'qobuz'
                    })
            except: pass
        
        seen = set()
        unique_tracks = []
        for t in tracks_found:
            if t['id'] not in seen:
                unique_tracks.append(t)
                seen.add(t['id'])

        return jsonify(unique_tracks[:limit])

    except Exception as e:
        logger.error(f"Blind Test YT Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/recommend')
def recommend_tracks():
    # Ancienne route maintenue pour compatibilité, mais simplifiée
    # On délègue au nouveau système si possible, sinon fallback Qobuz
    return jsonify({"error": "Deprecated, use /radio_queue"}), 404

@app.route('/search')
def search_tracks():
    query = request.args.get('q')
    search_type = request.args.get('type', 'all')
    
    combined_tracks = []
    albums_results = []
    playlists_results = []
    
    qobuz_tracks, subsonic_tracks, qobuz_albums, subsonic_albums = [], [], [], []

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        
        if search_type in ['track', 'all']:
            futures['q_tracks'] = executor.submit(threaded_qobuz_search, query, 25, 'track')
            futures['s_tracks'] = executor.submit(fetch_subsonic_tracks, query, 25)
            
        if search_type in ['album', 'all']:
            futures['q_albums'] = executor.submit(threaded_qobuz_search, query, 15, 'album')
            futures['s_albums'] = executor.submit(fetch_subsonic_albums, query, 15)
            
        if search_type in ['playlist', 'all']:
            try:
                yt_raw = yt.search(query, filter='playlists', limit=15)
                yt_albums = yt.search(query, filter='albums', limit=10)
                all_yt = yt_raw + yt_albums
                
                for item in all_yt:
                    try:
                        thumbnails = item.get('thumbnails', [])
                        if not thumbnails: continue
                        best_thumb = thumbnails[-1]
                        w = best_thumb.get('width', 0); h = best_thumb.get('height', 0)
                        if w > 0 and h > 0:
                            ratio = w / h
                            if ratio < 0.9 or ratio > 1.1: continue
                        
                        img_url = get_hq_yt_image(best_thumb['url'])
                        subtitle = "YouTube"
                        if 'author' in item: subtitle = item['author']
                        elif 'artists' in item: subtitle = ", ".join([a.get('name', '') for a in item['artists']])
                        elif 'year' in item: subtitle = str(item['year'])

                        playlists_results.append({
                            "id": item.get('browseId'),
                            "name": item.get('title'),
                            "title": item.get('title'),
                            "performer": { "name": subtitle },
                            "type": "playlist",
                            "source": "ytmusic",
                            "image": img_url,
                            "is_lazy": True 
                        })
                    except: continue
            except Exception as e: logger.error(f"YT Search Error: {e}")

        if 'q_tracks' in futures: qobuz_tracks = futures['q_tracks'].result()
        if 's_tracks' in futures: subsonic_tracks = futures['s_tracks'].result()
        if 'q_albums' in futures: qobuz_albums = futures['q_albums'].result()
        if 's_albums' in futures: subsonic_albums = futures['s_albums'].result()

    if search_type in ['track', 'all']:
        sigs = set()
        for t in qobuz_tracks:
            # SECURISATION DU PERFORER (CORRECTIF CRASH)
            artist_dict = t.get('performer') or t.get('artist') or {}
            artist_name = artist_dict.get('name', 'Inconnu')
            
            # Normalisation pour le frontend
            if 'performer' not in t or not t['performer']:
                t['performer'] = {'name': artist_name}
                
            sig = f"{clean_string(t.get('title', ''))}_{clean_string(artist_name)}"
            sigs.add(sig); combined_tracks.append(t)
            
        for t in subsonic_tracks:
            artist_name = t.get('performer', {}).get('name', 'Inconnu')
            sig = f"{clean_string(t.get('title', ''))}_{clean_string(artist_name)}"
            if sig not in sigs: combined_tracks.append(t)

    if search_type in ['album', 'all']:
        album_sigs = set()
        for a in qobuz_albums:
            artist_name = a.get('artist', {}).get('name', '')
            sig = f"{clean_string(a['title'])}_{clean_string(artist_name)}"
            album_sigs.add(sig); albums_results.append(a)
        for a in subsonic_albums:
            artist_name = a.get('artist', {}).get('name', '')
            sig = f"{clean_string(a['title'])}_{clean_string(artist_name)}"
            if sig not in album_sigs: albums_results.append(a)

    return jsonify({ "tracks": combined_tracks, "albums": albums_results, "external_playlists": playlists_results })

@app.route('/yt_playlist')
def get_yt_playlist_details():
    playlist_id = request.args.get('id')
    if not playlist_id: return jsonify({"error": "Missing ID"}), 400
    try:
        if playlist_id.startswith('MPRE') or playlist_id.startswith('OLAK'): details = yt.get_album(playlist_id)
        else: details = yt.get_playlist(playlist_id, limit=100) 
        
        formatted_tracks = []
        album_art = 'https://placehold.co/300x300/1a1a1a/666666?text=Music'
        if details.get('thumbnails'): album_art = get_hq_yt_image(details['thumbnails'][-1]['url'])
        
        tracks_source = details.get('tracks', [])
        for track in tracks_source:
            try:
                thumbnails = track.get('thumbnails', [])
                if thumbnails:
                    best_thumb = thumbnails[-1]
                    w = best_thumb.get('width', 0); h = best_thumb.get('height', 0)
                    if w > 0 and h > 0:
                        ratio = w / h
                        if ratio < 0.9 or ratio > 1.1: continue
                
                t_title = track.get('title'); t_id = track.get('videoId')
                artists_list = track.get('artists', []); t_artist = artists_list[0]['name'] if artists_list else (details.get('artists', [{'name':'Inconnu'}])[0]['name'])
                t_img = album_art
                if track.get('thumbnails'): t_img = get_hq_yt_image(track['thumbnails'][-1]['url'])
                
                if t_id and t_title:
                    formatted_tracks.append({
                        "id": t_id, "title": t_title, "performer": { "name": t_artist },
                        "album": { "title": details.get('title'), "image": { "large": t_img } },
                        "duration": track.get('duration_seconds', 0) or track.get('lengthSeconds', 0),
                        "source": "yt_lazy", "type": "track", "img": t_img
                    })
            except: continue
        return jsonify({ "id": playlist_id, "title": details.get('title'), "tracks": formatted_tracks, "image": album_art })
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/resolve_stream')
def resolve_and_stream():
    title = request.args.get('title'); artist = request.args.get('artist')
    if not title or not artist: return jsonify({"error": "Missing params"}), 400
    match = try_resolve_track(title, artist)
    if match:
        real_id = match['id']; source = match['source']
        if source == 'subsonic': return redirect(f"/stream_subsonic/{real_id}")
        else: return redirect(f"/stream/{real_id}")
    return jsonify({"error": "Track not found"}), 404

@app.route('/resolve_metadata')
def resolve_metadata():
    title = request.args.get('title'); artist = request.args.get('artist')
    if not title or not artist: return jsonify({"error": "Missing params"}), 400
    match = try_resolve_track(title, artist)
    if match:
        try:
            if match['source'] == 'qobuz':
                meta = client.get_track_meta(match['id'])
                img = meta.get('album', {}).get('image', {}).get('large', '').replace('_300', '_600')
                return jsonify({ 'id': match['id'], 'image': img, 'source': 'qobuz', 'album': meta.get('album', {}).get('title') })
            elif match['source'] == 'subsonic': return jsonify({'source': 'subsonic'}) 
        except: pass
    return jsonify({"error": "Not found"}), 404

@app.route('/track')
def get_track_info():
    track_id = request.args.get('id'); source = request.args.get('source')
    if source == 'subsonic':
        song = get_subsonic_track_details(track_id)
        if song: return jsonify(song)
        return jsonify({"error": "Not found"}), 404
    if not client: return jsonify({"error": "Init error"}), 500
    try: res = client.get_track_meta(track_id); res['source'] = 'qobuz'; fix_qobuz_title(res); return jsonify(res)
    except: return jsonify({"error": "Not found"}), 404

@app.route('/album')
def get_album():
    album_id = request.args.get('id'); source = request.args.get('source')
    if source == 'subsonic':
        url = SUBSONIC_BASE + "getAlbum.view"; params = get_subsonic_query_params(); params['id'] = album_id
        try:
            res = requests.get(url, params=params).json(); raw = res['subsonic-response']['album']; formatted_tracks = []
            if 'song' in raw:
                for song in raw['song']:
                    formatted_tracks.append({ 'id': song['id'], 'title': song['title'], 'duration': song.get('duration', 0), 'track_number': song.get('track', 0), 'performer': {'name': song.get('artist', raw.get('artist'))}, 'album': {'title': raw.get('name'), 'image': {'large': raw.get('coverArt')}}, 'source': 'subsonic' })
            return jsonify({ 'id': raw['id'], 'title': raw.get('name'), 'artist': {'name': raw.get('artist')}, 'image': {'large': raw.get('coverArt')}, 'source': 'subsonic', 'tracks': {'items': formatted_tracks} })
        except Exception as e: return jsonify({"error": str(e)}), 500
    if client:
        try: res = client.get_album_meta(album_id); res['source'] = 'qobuz'; return jsonify(res)
        except: pass
    return jsonify({"error": "Not found"}), 404

@app.route('/stream_subsonic/<track_id>')
def stream_subsonic(track_id):
    url = SUBSONIC_BASE + "stream.view"; params = get_subsonic_query_params(); params['id'] = track_id; params['format'] = 'mp3'; params['maxBitRate'] = '320'
    req = requests.Request('GET', url, params=params); prepared = req.prepare()
    return redirect(f"{SUPABASE_PROXY_URL}?url={urllib.parse.quote(prepared.url)}")

@app.route('/stream/<track_id>')
def stream_track(track_id):
    if not client: return jsonify({"error": "Init error"}), 500
    for fmt in [27, 7, 6, 5]: # 27: Hi-Res, 7: CD, 6: MP3 320, 5: MP3 192
        try:
            url_data = client.get_track_url(track_id, fmt) # <-- Correction ici
            if 'url' in url_data: return redirect(f"{SUPABASE_PROXY_URL}?url={urllib.parse.quote(url_data['url'])}")
        except Exception as e:
            logger.warning(f"Failed to get stream for track {track_id} with format {fmt}: {e}")
            continue
    return jsonify({"error": "No URL found"}), 404

@app.route('/get_subsonic_cover/<cover_id>')
def get_subsonic_cover(cover_id):
    url = SUBSONIC_BASE + "getCoverArt.view"; params = get_subsonic_query_params(); params['id'] = cover_id; params['size'] = 600 
    req = requests.Request('GET', url, params=params); prepared = req.prepare(); return redirect(prepared.url)

@app.route('/lyrics')
def get_lyrics():
    artist = request.args.get('artist'); title = request.args.get('title'); album = request.args.get('album')
    duration = request.args.get('duration')
    try:
        if duration and duration != 'undefined':
            dur_int = int(float(duration))
        else:
            dur_int = 0
    except (ValueError, TypeError):
        dur_int = 0
        
    try:
        yt_lyrics = fetch_yt_synced_lyrics(title, artist)
        if yt_lyrics:
            return jsonify({"type": "synced", "lyrics": yt_lyrics, "source": "YouTube"})

        plain, synced = lyrics_engine.search_lyrics(artist, title, album, dur_int)
        if synced: return jsonify({"type": "synced", "lyrics": synced, "source": "LRCLib"})
        
        if plain: return jsonify({"type": "plain", "lyrics": plain, "source": "LRCLib"})
        
    except Exception as e:
        logger.error(f"Lyrics Error: {e}")
        
    return jsonify({"type": "none", "lyrics": None}), 404

@app.route('/artist_bio')
def get_artist_bio(): return jsonify({})

# --- ROUTES STATIQUES (FRONTEND) ---
@app.route('/')
def serve_index():
    return send_from_directory(PROJECT_ROOT, 'index.html')

@app.route('/<path:path>')
def serve_static(path):
    return send_from_directory(PROJECT_ROOT, path)

if __name__ == '__main__':
    # init_client() n'est pas défini dans ce scope, retiré pour éviter une erreur locale
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Serveur local lancé sur le port {port}")
    app.run(host='0.0.0.0', port=port)