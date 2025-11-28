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

# Imports locaux
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

# IMPORTANT : Configuration pour servir les fichiers statiques depuis PROJECT_ROOT
app = Flask(__name__, static_folder=PROJECT_ROOT, static_url_path='')
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ZenithServer")

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

def get_hq_yt_image(url):
    if not url: return 'https://placehold.co/300x300/1a1a1a/666666?text=Music'
    return re.sub(r'w\d+-h\d+(-l\d+)?', 'w600-h600-l100', url)

def ms_to_lrc(ms):
    seconds = (ms / 1000)
    minutes = int(seconds // 60)
    rem_seconds = seconds % 60
    return f"[{minutes:02d}:{rem_seconds:05.2f}]"

def is_garbage_content(title, artist):
    t = title.lower()
    a = artist.lower()
    banned_terms = ["interview", "react", "reaction", "review", "analise", "explication", "guitar hero", "synthesia", "tutorial", "tuto", "lesson"]
    full_str = f"{t} {a}"
    for term in banned_terms:
        if term in full_str: return True, term
    return False, None

def fetch_yt_synced_lyrics(title, artist):
    query = f"{title} {artist}"
    try:
        results = yt.search(query, filter="songs", limit=1)
        if not results: return None
        video_id = results[0]['videoId']
        try:
            watch = yt.get_watch_playlist(video_id)
        except: return None
        if not watch or 'lyrics' not in watch or not watch['lyrics']: return None
        lyrics_id = watch['lyrics']
        lyrics_data = None
        try: lyrics_data = yt.get_lyrics(lyrics_id, timestamps=True)
        except: pass
        if not lyrics_data:
            try: lyrics_data = yt.get_lyrics(lyrics_id)
            except: return None
        if not lyrics_data: return None
        lyrics_content = lyrics_data.get('lyrics')
        if isinstance(lyrics_content, list):
            lrc_lines = []
            for line in lyrics_content:
                try:
                    t = None; txt = ""
                    if hasattr(line, 'start_time') and hasattr(line, 'text'):
                        t = line.start_time; txt = line.text
                    elif isinstance(line, dict):
                        t = line.get('start_time', line.get('seconds', line.get('startTime'))); txt = line.get('text', line.get('line', ''))
                    if t is not None:
                         lrc_lines.append(f"{ms_to_lrc(float(t) * 1000)} {txt}")
                except: continue
            if lrc_lines: return "\n".join(lrc_lines)
        return None
    except: return None

def get_subsonic_query_params():
    salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
    token_str = SUBSONIC_PASSWORD + salt
    token = hashlib.md5(token_str.encode('utf-8')).hexdigest()
    return { 'u': SUBSONIC_USER, 's': salt, 't': token, 'v': SUBSONIC_VERSION, 'c': SUBSONIC_CLIENT, 'f': 'json' }

def get_subsonic_track_details(track_id):
    url = SUBSONIC_BASE + "getSong.view"
    params = get_subsonic_query_params(); params['id'] = track_id
    try:
        res = requests.get(url, params=params).json()
        song = res['subsonic-response']['song']
        return {
            'id': song['id'], 'title': song['title'], 'performer': {'name': song['artist']},
            'album': {'title': song.get('album', 'Album'), 'image': {'large': song.get('coverArt')}},
            'duration': song.get('duration', 0), 'source': 'subsonic', 'maximum_bit_depth': 16
        }
    except: return None

def get_yt_recommendations(title, artist, banned_artists=set()):
    search_query = f'"{title}" "{artist}"'
    try:
        search_results = yt.search(search_query, filter='songs', limit=1)
        if not search_results: search_results = yt.search(f"{title} {artist}", filter='songs', limit=1)
        if not search_results: return None
        target_song = search_results[0]; video_id = target_song['videoId']
        raw_candidates = []
        try:
            watch_playlist = yt.get_watch_playlist(videoId=video_id)
            raw_candidates.extend(watch_playlist.get('tracks', []))
            related_browse_id = watch_playlist.get('related')
            if related_browse_id:
                related_content = yt.get_song_related(related_browse_id)
                for section in related_content:
                    contents = section.get('contents'); 
                    if isinstance(contents, list): raw_candidates.extend(contents)
        except: return None
        
        seen_ids = set(); candidates = []
        for item in raw_candidates:
            if 'videoId' in item and 'playlistId' not in item:
                r_id = item.get('videoId')
                if r_id in seen_ids: continue
                seen_ids.add(r_id)
                r_title = item.get('title', 'Inconnu'); artists = item.get('artists', [])
                r_artist_name = artists[0]['name'] if artists else "Artiste inconnu"
                if 'album' not in item or not item.get('album'): continue
                if clean_string(r_artist_name) in banned_artists: continue
                if clean_string(r_title) == clean_string(title): continue
                is_bad, reason = is_garbage_content(r_title, r_artist_name)
                if is_bad: continue
                candidates.append({'title': r_title, 'artist': r_artist_name})
        random.shuffle(candidates)
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_cand = { executor.submit(try_resolve_track, c['title'], c['artist']): c for c in candidates[:10] }
            for future in as_completed(future_to_cand):
                try:
                    match = future.result()
                    if match:
                        source = match['source']; real_id = match['id']
                        if source == 'qobuz' and client:
                            meta = client.get_track_meta(real_id); meta['source'] = 'qobuz'; fix_qobuz_title(meta)
                            if meta.get('album', {}).get('image', {}).get('large'): meta['img'] = meta['album']['image']['large'].replace('_300', '_600')
                            return meta
                        elif source == 'subsonic':
                            song = get_subsonic_track_details(real_id)
                            if song: return song
                except: continue
        return None 
    except: return None

def fetch_subsonic_tracks(query: str, limit=20) -> list:
    url = SUBSONIC_BASE + "search3.view"; params = get_subsonic_query_params(); params.update({ 'query': query, 'songCount': limit, 'albumCount': 0, 'artistCount': 0 })
    try:
        response = requests.get(url, params=params, timeout=5); data = response.json()
        if data.get('subsonic-response', {}).get('status') == 'ok':
            raw_songs = data['subsonic-response'].get('searchResult3', {}).get('song', [])
            found = []
            for song in raw_songs:
                found.append({ 'id': song.get('id'), 'title': song.get('title'), 'performer': {'name': song.get('artist', 'Inconnu')}, 'album': { 'title': song.get('album', 'Album'), 'image': {'large': song.get('coverArt')}}, 'duration': song.get('duration', 0), 'maximum_bit_depth': 16, 'source': 'subsonic' })
            return found
        return []
    except: return []

def fetch_subsonic_albums(query: str, limit=15) -> list:
    url = SUBSONIC_BASE + "search3.view"; params = get_subsonic_query_params(); params.update({ 'query': query, 'songCount': 0, 'albumCount': limit, 'artistCount': 0 })
    try:
        response = requests.get(url, params=params, timeout=5); data = response.json()
        if data.get('subsonic-response', {}).get('status') == 'ok':
            raw_albums = data['subsonic-response'].get('searchResult3', {}).get('album', [])
            found = []
            for album in raw_albums:
                found.append({ 'id': album.get('id'), 'title': album.get('name', album.get('title', 'Album')), 'artist': {'name': album.get('artist', 'Inconnu')}, 'image': {'large': album.get('coverArt')}, 'source': 'subsonic' })
            return found
        return []
    except: return []

def threaded_qobuz_search(query, limit=25, type='track'):
    if not client: return []
    try:
        if type == 'track':
            r = client.api_call("track/search", query=query, limit=limit); items = r.get('tracks', {}).get('items', [])
            for t in items: t['source'] = 'qobuz'; fix_qobuz_title(t)
            return items
        elif type == 'album':
            r = client.api_call("album/search", query=query, limit=limit); items = r.get('albums', {}).get('items', [])
            for a in items: a['source'] = 'qobuz'
            return items
    except: return []

def try_resolve_track(title, artist):
    search_query = f"{title} {artist}"; target_artist_clean = clean_string(artist); target_title_clean = clean_string(title)
    if client:
        try:
            q_resp = client.api_call("track/search", query=search_query, limit=5); items = q_resp.get('tracks', {}).get('items', [])
            for rec in items:
                rec_title_clean = clean_string(rec['title']); performer_dict = rec.get('performer') or rec.get('artist') or {}; rec_artist_clean = clean_string(performer_dict.get('name', ''))
                artist_match = False
                if FUZZ_AVAILABLE:
                    if fuzz.ratio(target_artist_clean, rec_artist_clean) > 65: artist_match = True
                    elif target_artist_clean in rec_artist_clean or rec_artist_clean in target_artist_clean: artist_match = True
                else:
                    if target_artist_clean in rec_artist_clean or rec_artist_clean in target_artist_clean: artist_match = True
                if not artist_match: continue
                title_match = False
                if FUZZ_AVAILABLE:
                    if fuzz.ratio(target_title_clean, rec_title_clean) > 60: title_match = True
                else:
                    if target_title_clean in rec_title_clean or rec_title_clean in target_title_clean: title_match = True
                if not title_match: continue
                if "cover" not in target_title_clean and "cover" in rec_title_clean: continue
                if "tribute" not in target_title_clean and "tribute" in rec_title_clean: continue
                if "karaoke" not in target_title_clean and "karaoke" in rec_title_clean: continue
                return {'id': rec['id'], 'source': 'qobuz'}
        except: pass
    subs = fetch_subsonic_tracks(search_query, limit=5)
    for song in subs:
        s_artist = clean_string(song['performer']['name'])
        if target_artist_clean in s_artist or s_artist in target_artist_clean: return {'id': song['id'], 'source': 'subsonic'}
    return None

# --- ROUTES API ---

@app.route('/blind_test_tracks')
def get_blind_test_tracks():
    theme = request.args.get('theme', 'Global Hits'); logger.info(f"🎲 Blind Test: Thème demandé = {theme}")
    if not client: return jsonify({"error": "Client not initialized"}), 500
    tracks_found = []
    if theme in ['Global Hits', 'Pop Global']:
        try:
            resp = client.api_call("track/search", query=theme, limit=40); items = resp.get('tracks', {}).get('items', []); random.shuffle(items)
            for track in items[:15]:
                tracks_found.append({ 'id': track['id'], 'title': track['title'], 'artist': track.get('performer', {}).get('name', track.get('artist', {}).get('name', 'Unknown')), 'album': track['album']['title'], 'img': track.get('album', {}).get('image', {}).get('large', '').replace('_300', '_600'), 'duration': track['duration'], 'source': 'qobuz' })
            return jsonify(tracks_found)
        except Exception as e: return jsonify({"error": "Failed to fetch tracks"}), 500
    try:
        search_results = yt.search(theme, filter='playlists', limit=3)
        if not search_results: return jsonify({"error": "No playlist found"}), 404
        playlist_id = search_results[0]['browseId']; playlist_data = yt.get_playlist(playlist_id, limit=50); yt_tracks = playlist_data.get('tracks', []); random.shuffle(yt_tracks)
        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_track = {}
            for t in yt_tracks[:15]:
                title = t.get('title'); artists = t.get('artists', []); artist = artists[0]['name'] if artists else "Unknown"
                if title and artist: future_to_track[executor.submit(try_resolve_track, title, artist)] = (title, artist)
            for future in as_completed(future_to_track):
                try:
                    match = future.result()
                    if match:
                        real_id = match['id']; source = match['source']
                        if source == 'qobuz':
                             meta = client.get_track_meta(real_id)
                             tracks_found.append({ 'id': meta['id'], 'title': meta['title'], 'artist': meta.get('performer', {}).get('name', 'Unknown'), 'album': meta.get('album', {}).get('title'), 'img': meta.get('album', {}).get('image', {}).get('large', '').replace('_300', '_600'), 'duration': meta['duration'], 'source': 'qobuz' })
                        elif source == 'subsonic':
                             meta = get_subsonic_track_details(real_id)
                             if meta: tracks_found.append({ 'id': meta['id'], 'title': meta['title'], 'artist': meta['performer']['name'], 'album': meta['album']['title'], 'img': f"{API_BASE}/get_subsonic_cover/{meta['album']['image']['large']}" if meta['album']['image']['large'] else "", 'duration': meta['duration'], 'source': 'subsonic' })
                except: continue
        seen = set(); unique_tracks = []
        for t in tracks_found:
            if t['id'] not in seen: unique_tracks.append(t); seen.add(t['id'])
        return jsonify(unique_tracks[:10])
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/recommend')
def recommend_tracks():
    original_artist = request.args.get('artist', ''); original_title = request.args.get('title', '')
    recent_artists_str = request.args.get('recent_artists', '')
    recent_artists_raw = recent_artists_str.split('|') if '|' in recent_artists_str else (recent_artists_str.split(',') if recent_artists_str else [])
    recent_artists = [clean_string(a) for a in recent_artists_raw if a]
    banned_artists = set(); artist_counts = {}
    for artist in recent_artists: artist_counts[artist] = artist_counts.get(artist, 0) + 1
    for artist, count in artist_counts.items(): 
        if count >= 2: banned_artists.add(artist)
    resolved_rec = get_yt_recommendations(original_title, original_artist, banned_artists)
    if resolved_rec: return jsonify(resolved_rec)
    if client:
        try:
            track_id = request.args.get('current_id'); track_meta = client.get_track_meta(track_id); genre_name = track_meta.get('album', {}).get('genre', {}).get('name')
            original_clean = clean_string(original_artist); search_query = genre_name if (genre_name and original_clean in banned_artists) else original_artist
            resp = client.api_call("track/search", query=search_query, limit=50); items = resp.get('tracks', {}).get('items', []); random.shuffle(items)
            for item in items:
                if str(item['id']) == str(track_id): continue
                candidate_clean = clean_string(item.get('performer', {}).get('name', 'Inconnu'))
                if candidate_clean in banned_artists: continue
                if candidate_clean == clean_string(original_artist) and clean_string(item['title']) == clean_string(original_title): continue
                item['source'] = 'qobuz'; fix_qobuz_title(item); return jsonify(item)
        except: pass
    return jsonify({"error": "No recommendation found"}), 404

@app.route('/search')
def search_tracks():
    query = request.args.get('q'); search_type = request.args.get('type', 'all')
    combined_tracks = []; albums_results = []; playlists_results = []
    qobuz_tracks, subsonic_tracks, qobuz_albums, subsonic_albums = [], [], [], []
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {}
        if search_type in ['track', 'all']: futures['q_tracks'] = executor.submit(threaded_qobuz_search, query, 25, 'track'); futures['s_tracks'] = executor.submit(fetch_subsonic_tracks, query, 25)
        if search_type in ['album', 'all']: futures['q_albums'] = executor.submit(threaded_qobuz_search, query, 15, 'album'); futures['s_albums'] = executor.submit(fetch_subsonic_albums, query, 15)
        if search_type in ['playlist', 'all']:
            try:
                yt_raw = yt.search(query, filter='playlists', limit=15); yt_albums = yt.search(query, filter='albums', limit=10); all_yt = yt_raw + yt_albums
                for item in all_yt:
                    try:
                        img_url = get_hq_yt_image(item.get('thumbnails', [])[-1]['url'] if item.get('thumbnails') else '')
                        subtitle = "YouTube"
                        if 'author' in item: subtitle = item['author']
                        elif 'artists' in item: subtitle = ", ".join([a.get('name', '') for a in item['artists']])
                        playlists_results.append({ "id": item.get('browseId'), "name": item.get('title'), "title": item.get('title'), "performer": { "name": subtitle }, "type": "playlist", "source": "ytmusic", "image": img_url, "is_lazy": True })
                    except: continue
            except: pass
        if 'q_tracks' in futures: qobuz_tracks = futures['q_tracks'].result()
        if 's_tracks' in futures: subsonic_tracks = futures['s_tracks'].result()
        if 'q_albums' in futures: qobuz_albums = futures['q_albums'].result()
        if 's_albums' in futures: subsonic_albums = futures['s_albums'].result()
    if search_type in ['track', 'all']:
        sigs = set()
        for t in qobuz_tracks:
            sig = f"{clean_string(t.get('title', ''))}_{clean_string(t.get('performer', {}).get('name', ''))}"
            sigs.add(sig); combined_tracks.append(t)
        for t in subsonic_tracks:
            sig = f"{clean_string(t.get('title', ''))}_{clean_string(t.get('performer', {}).get('name', ''))}"
            if sig not in sigs: combined_tracks.append(t)
    if search_type in ['album', 'all']:
        album_sigs = set()
        for a in qobuz_albums:
            sig = f"{clean_string(a['title'])}_{clean_string(a.get('artist', {}).get('name', ''))}"
            album_sigs.add(sig); albums_results.append(a)
        for a in subsonic_albums:
            sig = f"{clean_string(a['title'])}_{clean_string(a.get('artist', {}).get('name', ''))}"
            if sig not in album_sigs: albums_results.append(a)
    return jsonify({ "tracks": combined_tracks, "albums": albums_results, "external_playlists": playlists_results })

@app.route('/yt_playlist')
def get_yt_playlist_details():
    playlist_id = request.args.get('id'); 
    if not playlist_id: return jsonify({"error": "Missing ID"}), 400
    try:
        details = yt.get_album(playlist_id) if playlist_id.startswith('MPRE') or playlist_id.startswith('OLAK') else yt.get_playlist(playlist_id, limit=100)
        formatted_tracks = []
        album_art = get_hq_yt_image(details['thumbnails'][-1]['url']) if details.get('thumbnails') else 'https://placehold.co/300x300/1a1a1a/666666?text=Music'
        for track in details.get('tracks', []):
            try:
                t_img = get_hq_yt_image(track['thumbnails'][-1]['url']) if track.get('thumbnails') else album_art
                t_artist = track.get('artists', [{'name':'Inconnu'}])[0]['name']
                formatted_tracks.append({ "id": track.get('videoId'), "title": track.get('title'), "performer": { "name": t_artist }, "album": { "title": details.get('title'), "image": { "large": t_img } }, "duration": track.get('duration_seconds', 0) or track.get('lengthSeconds', 0), "source": "yt_lazy", "type": "track", "img": t_img })
            except: continue
        return jsonify({ "id": playlist_id, "title": details.get('title'), "tracks": formatted_tracks, "image": album_art })
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/resolve_stream')
def resolve_and_stream():
    title = request.args.get('title'); artist = request.args.get('artist')
    if not title or not artist: return jsonify({"error": "Missing params"}), 400
    match = try_resolve_track(title, artist)
    if match: return redirect(f"/stream_subsonic/{match['id']}" if match['source'] == 'subsonic' else f"/stream/{match['id']}")
    return jsonify({"error": "Track not found"}), 404

@app.route('/resolve_metadata')
def resolve_metadata():
    title = request.args.get('title'); artist = request.args.get('artist')
    match = try_resolve_track(title, artist)
    if match and match['source'] == 'qobuz':
        try:
            meta = client.get_track_meta(match['id'])
            return jsonify({ 'id': match['id'], 'image': meta.get('album', {}).get('image', {}).get('large', '').replace('_300', '_600'), 'source': 'qobuz', 'album': meta.get('album', {}).get('title') })
        except: pass
    return jsonify({"error": "Not found"}), 404

@app.route('/track')
def get_track_info():
    track_id = request.args.get('id'); source = request.args.get('source')
    if source == 'subsonic':
        song = get_subsonic_track_details(track_id)
        if song: return jsonify(song)
    elif client:
        try: res = client.get_track_meta(track_id); res['source'] = 'qobuz'; fix_qobuz_title(res); return jsonify(res)
        except: pass
    return jsonify({"error": "Not found"}), 404

@app.route('/album')
def get_album():
    album_id = request.args.get('id'); source = request.args.get('source')
    if source == 'subsonic':
        url = SUBSONIC_BASE + "getAlbum.view"; params = get_subsonic_query_params(); params['id'] = album_id
        try:
            res = requests.get(url, params=params).json(); raw = res['subsonic-response']['album']; formatted_tracks = []
            if 'song' in raw:
                for song in raw['song']: formatted_tracks.append({ 'id': song['id'], 'title': song['title'], 'duration': song.get('duration', 0), 'track_number': song.get('track', 0), 'performer': {'name': song.get('artist', raw.get('artist'))}, 'album': {'title': raw.get('name'), 'image': {'large': raw.get('coverArt')}}, 'source': 'subsonic' })
            return jsonify({ 'id': raw['id'], 'title': raw.get('name'), 'artist': {'name': raw.get('artist')}, 'image': {'large': raw.get('coverArt')}, 'source': 'subsonic', 'tracks': {'items': formatted_tracks} })
        except: pass
    elif client:
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
    for fmt in [27, 7, 6, 5]:
        try:
            url_data = client.get_track_url(track_id, fmt)
            if 'url' in url_data: return redirect(f"{SUPABASE_PROXY_URL}?url={urllib.parse.quote(url_data['url'])}")
        except: continue
    return jsonify({"error": "No URL found"}), 404

@app.route('/get_subsonic_cover/<cover_id>')
def get_subsonic_cover(cover_id):
    url = SUBSONIC_BASE + "getCoverArt.view"; params = get_subsonic_query_params(); params['id'] = cover_id; params['size'] = 600 
    req = requests.Request('GET', url, params=params); prepared = req.prepare(); return redirect(prepared.url)

@app.route('/lyrics')
def get_lyrics():
    artist = request.args.get('artist'); title = request.args.get('title'); album = request.args.get('album'); duration = request.args.get('duration')
    dur_int = 0
    try: dur_int = int(float(duration)) if duration and duration != 'undefined' else 0
    except: pass
    
    yt_lyrics = fetch_yt_synced_lyrics(title, artist)
    if yt_lyrics: return jsonify({"type": "synced", "lyrics": yt_lyrics, "source": "YouTube"})
    
    plain, synced = lyrics_engine.search_lyrics(artist, title, album, dur_int)
    if synced: return jsonify({"type": "synced", "lyrics": synced, "source": "LRCLib"})
    if plain: return jsonify({"type": "plain", "lyrics": plain, "source": "LRCLib"})
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
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Serveur local lancé sur le port {port}")
    app.run(host='0.0.0.0', port=port)