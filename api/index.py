from flask import Flask, jsonify, redirect, request, send_file
from flask_cors import CORS
from .qobuz_api import QobuzClient, get_app_credentials
from .lyrics_search import LyricsSearcher 
import logging
import random
import os

# --- CONFIGURATION ---
USER_ID = '7610812'
TOKEN = 'wTJvd-7fc8haH3zdRrZYqcULUQ1wA6wJBLNmDkn38JaMrfRtHlaGpSVLHN0205rSQ23psXhJrnQNrRmEiGS-zw'
APP_ID = '798273057'

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QobuzServer")

lyrics_engine = LyricsSearcher()

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
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:83.0) Gecko/20100101 Firefox/83.0",
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


@app.route('/')
def home():
    try: return send_file('../index.html')
    except: return "Erreur: index.html introuvable"

# --- MODIFICATION : RECHERCHE AVEC FILTRE ---
@app.route('/search')
def search_tracks():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    
    query = request.args.get('q')
    search_type = request.args.get('type', 'all') # 'track', 'album', ou 'all'

    tracks_results = []
    albums_results = []

    try:
        # Si on veut des titres ou tout
        if search_type in ['track', 'all']:
            limit = 75 if search_type == 'track' else 20
            tracks_resp = client.api_call("track/search", query=query, limit=limit)
            tracks_results = tracks_resp.get('tracks', {}).get('items', [])

        # Si on veut des albums ou tout
        if search_type in ['album', 'all']:
            limit = 30 if search_type == 'album' else 10
            albums_resp = client.api_call("album/search", query=query, limit=limit)
            albums_results = albums_resp.get('albums', {}).get('items', [])
        
        return jsonify({
            "tracks": tracks_results,
            "albums": albums_results
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/album')
def get_album():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    album_id = request.args.get('id')
    try:
        meta = client.get_album_meta(album_id)
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