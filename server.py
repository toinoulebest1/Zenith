from flask import Flask, jsonify, redirect, request, send_file
from flask_cors import CORS
from qobuz_api import QobuzClient, get_app_credentials
# On importe ton module de recherche de paroles
from lyrics_search import LyricsSearcher 
import logging
import random
import os  # <--- C'EST CETTE LIGNE QUI MANQUAIT !

# --- CONFIGURATION ---
USER_ID = '7610812'
TOKEN = 'wTJvd-7fc8haH3zdRrZYqcULUQ1wA6wJBLNmDkn38JaMrfRtHlaGpSVLHN0205rSQ23psXhJrnQNrRmEiGS-zw'
APP_ID = '798273057'

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("QobuzServer")

# Initialisation du chercheur de paroles
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

def init_client():
    global client
    try:
        logger.info("Init Qobuz...")
        fetched_app_id, secrets = get_app_credentials()
        client = TokenQobuzClient(APP_ID, secrets, TOKEN)
        logger.info("Ready.")
    except Exception as e:
        logger.error(f"Init Error: {e}")

@app.route('/')
def home():
    return send_file('index.html')

@app.route('/search')
def search_tracks():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    query = request.args.get('q')
    try:
        resp = client.api_call("track/search", query=query, limit=30)
        return jsonify(resp)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/lyrics')
def get_lyrics():
    artist = request.args.get('artist')
    title = request.args.get('title')
    album = request.args.get('album')
    duration = request.args.get('duration')
    
    try:
        duration = int(float(duration)) if duration else 0
    except:
        duration = 0

    logger.info(f"Recherche paroles pour : {artist} - {title}")
    
    plain, synced = lyrics_engine.search_lyrics(artist, title, album, duration)
    
    if synced:
        return jsonify({"type": "synced", "lyrics": synced})
    elif plain:
        return jsonify({"type": "plain", "lyrics": plain})
    else:
        return jsonify({"type": "none", "lyrics": None}), 404

@app.route('/recommend')
def recommend_tracks():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    
    original_artist = request.args.get('artist')
    track_id = request.args.get('current_id')
    
    logger.info(f"Analyse intelligente pour : {original_artist}")
    
    try:
        track_meta = client.get_track_meta(track_id)
        title = track_meta.get('title', '').lower()
        album = track_meta.get('album', {}).get('title', '').lower()
        genre_name = None
        if 'album' in track_meta and 'genre' in track_meta['album']:
            genre_name = track_meta['album']['genre']['name']

        # LISTE MOTS-CLES AMBIANCE
        christmas_keywords = ['christmas', 'noël', 'noel', 'santa', 'merry', 'holiday', 'navidad', 'jingle', 'snow', 'hiver']
        
        search_query = ""
        is_context_mode = False

        if any(word in title for word in christmas_keywords) or any(word in album for word in christmas_keywords):
            search_query = "Christmas Music"
            is_context_mode = True
        else:
            search_query = genre_name if genre_name else original_artist

        resp = client.api_call("track/search", query=search_query, limit=100)
        items = resp.get('tracks', {}).get('items', [])
        random.shuffle(items)
        
        recommendation = None
        for item in items:
            found_artist = item['performer']['name']
            
            if str(item['id']) == str(track_id):
                continue

            if not is_context_mode:
                if found_artist.lower() == original_artist.lower():
                    continue

            recommendation = item
            break
        
        if not recommendation and items:
            for item in items:
                if str(item['id']) != str(track_id):
                    recommendation = item
                    break

        if recommendation:
            return jsonify(recommendation)
        else:
            return jsonify({"error": "No recommendation found"}), 404

    except Exception as e:
        logger.error(f"Rec Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/stream/<track_id>')
def stream_track(track_id):
    if not client: return jsonify({"error": "Client not initialized"}), 500
    try:
        fmt_id = 5 
        url_data = client.get_track_url(track_id, fmt_id)
        if 'url' in url_data:
            return redirect(url_data['url'])
        else:
            return jsonify({"error": "No URL found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    init_client()
    # Configuration pour Render : on récupère le PORT dynamique
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Serveur lancé sur le port {port}")
    app.run(host='0.0.0.0', port=port)