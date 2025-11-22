from flask import Flask, jsonify, redirect, request, send_file
from flask_cors import CORS
from .qobuz_api import QobuzClient, get_app_credentials
from .lyrics_search import LyricsSearcher 
import logging
import random
import os
import requests
import hashlib
from pathlib import Path 
import json 

# --- CONFIGURATION (Identifiants Qobuz) ---
USER_ID = '7610812'
TOKEN = 'wTJvd-7fc8haH3zdRrZYqcULUQ1wA6wJBLNmDkn38JaMrfRtHlaGpSVLHN0205rSQ23psXhJrnQNrRmEiGS-zw' 
APP_ID = '798273057'

# --- CONFIGURATION SUBSONIC / TIDAL ---
# Clés extraites de votre URL :
SUBSONIC_BASE = "https://api.401658.xyz/rest/"
SUBSONIC_USER = "toinoulebest"
SUBSONIC_TOKEN = "EbPNO8NRaEko"
SUBSONIC_SALT = "5080c4608437cf0956d5af0e24606615" 
SUBSONIC_CLIENT = "Feishin"
SUBSONIC_VERSION = "1.13.0"

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QobuzServer")

lyrics_engine = LyricsSearcher()

# Qobuz Client
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
        import requests
        s = requests.Session()
        s.headers.update({
            "User-Agent": "Zenith Ultimate Player (Vercel Backend)",
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

# --- FONCTIONS SUBSONIC ---
def get_subsonic_auth_params():
    """Crée les paramètres d'authentification Subsonic standard."""
    return {
        'u': SUBSONIC_USER,
        's': SUBSONIC_SALT,
        't': SUBSONIC_TOKEN,
        'c': SUBSONIC_CLIENT,
        'v': SUBSONIC_VERSION,
        'f': 'json'
    }

def fetch_subsonic_tracks(query: str) -> list:
    """Récupère les pistes de Subsonic et les normalise."""
    url = SUBSONIC_BASE + "search3.view"
    params = get_subsonic_auth_params()
    
    params.update({
        'query': query,
        'songCount': 50, # Limite à 50
        'albumCount': 0, 
        'artistCount': 0
    })

    try:
        response = requests.get(url, params=params, timeout=5)
        response.raise_for_status()
        data = response.json()
        
        if data.get('subsonic-response', {}).get('status') == 'ok':
            subsonic_songs = data['subsonic-response']['searchResult3'].get('song', [])
            
            normalized_tracks = []
            for song in subsonic_songs:
                # Normalisation vers le format Qobuz/Frontend
                # Note: On utilise 'album' pour la pochette
                normalized_tracks.append({
                    'id': song['id'],
                    'title': song['title'],
                    'performer': {'name': song['artist']},
                    'album': {'title': song['album'], 'image': {'large': song.get('coverArt')}},
                    'duration': song['duration'],
                    'maximum_bit_depth': 24, # On suppose une haute qualité HiFi
                    'source': 'subsonic' # Indicateur de source
                })
            return normalized_tracks
        
        return []
        
    except Exception as e:
        logger.error(f"Erreur lors de la requête Subsonic: {e}")
        return []

# --- ROUTES ---

@app.route('/search')
def search_tracks():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    
    query = request.args.get('q')
    search_type = request.args.get('type', 'all')

    qobuz_tracks = []
    subsonic_tracks = []
    albums_results = []

    # 1. RECHERCHE QOBUZ (Tracks & Albums)
    try:
        if search_type in ['track', 'all']:
            limit = 40 if search_type == 'track' else 25 
            tracks_resp = client.api_call("track/search", query=query, limit=limit)
            qobuz_tracks = tracks_resp.get('tracks', {}).get('items', [])
            # Ajout du tag de source QOBUZ
            for track in qobuz_tracks:
                track['source'] = 'qobuz'

        if search_type in ['album', 'all']:
            limit = 40 if search_type == 'album' else 15
            albums_resp = client.api_call("album/search", query=query, limit=limit)
            albums_results = albums_resp.get('albums', {}).get('items', [])
            
    except Exception as e:
        logger.error(f"Erreur Qobuz Search: {e}")

    # 2. RECHERCHE SUBSONIC (uniquement Tracks, si nécessaire)
    if search_type in ['track', 'all']:
        subsonic_tracks = fetch_subsonic_tracks(query)

    # 3. COMBINAISON (Qobuz d'abord, Subsonic pour compléter)
    combined_tracks = qobuz_tracks + subsonic_tracks
    
    return jsonify({
        "tracks": combined_tracks,
        "albums": albums_results
    })

# --- ROUTE STREAM SUBSONIC (Ajouté) ---
@app.route('/stream_subsonic/<track_id>')
def stream_subsonic(track_id):
    """Génère l'URL de streaming pour un ID Subsonic."""
    
    url = SUBSONIC_BASE + "stream.view"
    params = get_subsonic_auth_params()
    params['id'] = track_id
    
    req = requests.Request('GET', url, params=params)
    prepared = req.prepare()
    return redirect(prepared.url, code=302)

# --- ROUTE COVER SUBSONIC (Ajouté) ---
@app.route('/get_subsonic_cover/<cover_id>')
def get_subsonic_cover(cover_id):
    """Génère l'URL de la pochette pour un ID Subsonic."""
    url = SUBSONIC_BASE + "getCoverArt.view"
    params = get_subsonic_auth_params()
    params['id'] = cover_id
    params['size'] = 600 # Taille HD
    
    req = requests.Request('GET', url, params=params)
    prepared = req.prepare()
    return redirect(prepared.url, code=302)


# --- ROUTE BLIND TEST (Générique) ---
@app.route('/blind_test_tracks')
def get_blind_test_tracks():
    # Pour le Blind Test, on ne prend que Qobuz pour simplifier l'URL de streaming
    if not client: return jsonify({"error": "Client not initialized"}), 500
    try:
        resp = client.api_call("track/search", query="Global Hits", limit=30)
        items = resp.get('tracks', {}).get('items', [])
        random.shuffle(items)
        tracks_for_game = items[:10]
        normalized_tracks = []
        for track in tracks_for_game:
            normalized_tracks.append({
                'id': track['id'],
                'title': track['title'],
                'artist': track.get('performer', {}).get('name', track.get('artist', {}).get('name', 'Unknown')),
                'album': track['album']['title'],
                'img': track.get('album', {}).get('image', {}).get('large', '').replace('_300', '_600'),
                'duration': track['duration']
            })
            
        return jsonify(normalized_tracks)
    except Exception as e:
        logger.error(f"Blind Test Error: {e}")
        return jsonify({"error": "Failed to fetch blind test tracks"}), 500

# --- ROUTES QOBUZ (Inchangées / Simplifiées) ---

@app.route('/album')
def get_album():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    album_id = request.args.get('id')
    try:
        meta = client.get_album_meta(album_id)
        return jsonify(meta)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/track')
def get_track_info():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    track_id = request.args.get('id')
    try:
        meta = client.get_track_meta(track_id)
        return jsonify(meta)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/artist')
def get_artist():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    artist_id = request.args.get('id')
    try:
        meta = client.api_call("artist/get", id=artist_id, extra="albums", limit=100)
        all_albums = []
        if 'album_last_release' in meta:
            if meta.get('album_last_release'): all_albums.append(meta['album_last_release'])
            if 'albums_without_last_release' in meta: all_albums.extend(meta['albums_without_last_release']['items'])
        elif 'albums' in meta: all_albums = meta['albums']['items']
        meta['albums'] = {'items': all_albums, 'total': len(all_albums)}
        return jsonify(meta)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/lyrics')
def get_lyrics():
    artist = request.args.get('artist')
    title = request.args.get('title')
    album = request.args.get('album')
    duration = request.args.get('duration')
    try: duration = int(float(duration)) if duration else 0
    except: duration = 0
    plain, synced = lyrics_engine.search_lyrics(artist, title, album, duration)
    if synced: return jsonify({"type": "synced", "lyrics": synced})
    elif plain: return jsonify({"type": "plain", "lyrics": plain})
    else: return jsonify({"type": "none", "lyrics": None}), 404

@app.route('/recommend')
def recommend_tracks():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    original_artist = request.args.get('artist')
    track_id = request.args.get('current_id')
    try:
        track_meta = client.get_track_meta(track_id)
        title = track_meta.get('title', '').lower()
        album = track_meta.get('album', {}).get('title', '').lower()
        genre_name = track_meta.get('album', {}).get('genre', {}).get('name')
        christmas_keywords = ['christmas', 'noël', 'noel', 'santa', 'merry', 'holiday', 'navidad', 'jingle', 'snow', 'hiver']
        search_query = genre_name if genre_name else original_artist
        is_context_mode = False
        if any(word in title for word in christmas_keywords) or any(word in album for word in christmas_keywords):
            search_query = "Christmas Music"
            is_context_mode = True
        resp = client.api_call("track/search", query=search_query, limit=100)
        items = resp.get('tracks', {}).get('items', [])
        random.shuffle(items)
        recommendation = None
        for item in items:
            if str(item['id']) == str(track_id): continue
            if not is_context_mode:
                if item['performer']['name'].lower() == original_artist.lower(): continue
            recommendation = item
            break
        if not recommendation and items: recommendation = items[0]
        if recommendation: return jsonify(recommendation)
        else: return jsonify({"error": "No recommendation found"}), 404
    except Exception as e: return jsonify({"error": str(e)}), 500

@app.route('/stream/<track_id>')
def stream_track(track_id):
    if not client: return jsonify({"error": "Client not initialized"}), 500
    try:
        url_data = client.get_track_url(track_id, 5)
        if 'url' in url_data: return redirect(url_data['url'])
        else: return jsonify({"error": "No URL found"}), 404
    except Exception as e: return jsonify({"error": str(e)}), 500
    