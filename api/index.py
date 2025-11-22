from flask import Flask, jsonify, redirect, request, send_file, Response, stream_with_context
from flask_cors import CORS
from .qobuz_api import QobuzClient, get_app_credentials
from .lyrics_search import LyricsSearcher 
import logging
import random
import os
import requests
import json 
from pathlib import Path 
import re
import urllib.parse
import hashlib
import string
from concurrent.futures import ThreadPoolExecutor, as_completed

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

LASTFM_API_KEY = "6af670652a5f6b390d1f5315ca201319" 

# --- CONFIGURATION SUPABASE PROXY ---
SUPABASE_PROXY_URL = "https://mzxfcvzqxgslyopkkaej.supabase.co/functions/v1/stream-proxy"

# --- RESERVE GLOBALE ---
SUGGESTION_RESERVE = []

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ZenithServer")

lyrics_engine = LyricsSearcher()

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
    s = str(s).lower().strip()
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

def clean_title_for_search(title):
    if not title: return ""
    title = re.sub(r" ?[\(\[].*?[\)\]]", "", title)
    title = re.split(r" feat\.| ft\.| with ", title, flags=re.IGNORECASE)[0]
    return title.strip()

def fix_qobuz_title(track):
    """Ajoute la version (Remix, Live, etc.) au titre si elle est dans un champ séparé"""
    try:
        if 'version' in track and track['version']:
            ver = str(track['version']).strip()
            # On ignore les versions génériques qui n'apportent rien à l'affichage
            if ver.lower() not in ['album version', 'original version', 'standard version']:
                # On évite les doublons si le titre contient déjà la version
                if ver.lower() not in track['title'].lower():
                    track['title'] = f"{track['title']} ({ver})"
    except: pass
    return track

# --- LAST.FM API ---
def fetch_lastfm_recommendations(artist, track_title):
    url = "http://ws.audioscrobbler.com/2.0/"
    clean_title = clean_title_for_search(track_title)
    
    params = {
        'method': 'track.getsimilar',
        'artist': artist,
        'track': clean_title,
        'api_key': LASTFM_API_KEY,
        'format': 'json',
        'limit': 50, 
        'autocorrect': 1
    }
    try:
        resp = requests.get(url, params=params, timeout=6)
        data = resp.json()
        recommendations = []
        if 'similartracks' in data and 'track' in data['similartracks']:
            for t in data['similartracks']['track']:
                if 'name' in t and 'artist' in t:
                    recommendations.append((t['name'], t['artist']['name']))
            return recommendations
    except Exception as e:
        logger.error(f"LastFM Error: {e}")
    return []

# --- API SUBSONIC DYNAMIQUE ---
def get_subsonic_query_params():
    salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
    token_str = SUBSONIC_PASSWORD + salt
    token = hashlib.md5(token_str.encode('utf-8')).hexdigest()
    
    return {
        'u': SUBSONIC_USER,
        's': salt,
        't': token,
        'v': SUBSONIC_VERSION,
        'c': SUBSONIC_CLIENT,
        'f': 'json'
    }

def fetch_subsonic_tracks(query: str, limit=20) -> list:
    url = SUBSONIC_BASE + "search3.view"
    params = get_subsonic_query_params()
    params.update({
        'query': query,
        'songCount': limit,
        'albumCount': 0,
        'artistCount': 0
    })
    
    try:
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        
        if data.get('subsonic-response', {}).get('status') == 'ok':
            raw_songs = data['subsonic-response'].get('searchResult3', {}).get('song', [])
            found = []
            for song in raw_songs:
                try:
                    album_title = song.get('album', 'Album Inconnu')
                    found.append({
                        'id': song.get('id'),
                        'title': song.get('title'),
                        'performer': {'name': song.get('artist', 'Inconnu')},
                        'album': {
                            'title': album_title, 
                            'image': {'large': song.get('coverArt')} 
                        },
                        'duration': song.get('duration', 0),
                        'maximum_bit_depth': 16, 
                        'source': 'subsonic'
                    })
                except: continue
            return found
        return []
    except Exception as e:
        logger.error(f"Subsonic Search Error: {e}")
        return []

def fetch_subsonic_albums(query: str, limit=15) -> list:
    url = SUBSONIC_BASE + "search3.view"
    params = get_subsonic_query_params()
    params.update({
        'query': query,
        'songCount': 0,
        'albumCount': limit,
        'artistCount': 0
    })
    
    try:
        response = requests.get(url, params=params, timeout=5)
        data = response.json()
        
        if data.get('subsonic-response', {}).get('status') == 'ok':
            raw_albums = data['subsonic-response'].get('searchResult3', {}).get('album', [])
            found = []
            for album in raw_albums:
                try:
                    album_title = album.get('name', album.get('title', 'Album Inconnu'))
                    found.append({
                        'id': album.get('id'),
                        'title': album_title,
                        'artist': {'name': album.get('artist', 'Inconnu')},
                        'image': {'large': album.get('coverArt')},
                        'source': 'subsonic'
                    })
                except: continue
            return found
        return []
    except Exception as e:
        logger.error(f"Subsonic Album Search Error: {e}")
        return []

# --- WRAPPERS POUR THREADING ---
def threaded_qobuz_search(query, limit=25, type='track'):
    if not client: return []
    try:
        if type == 'track':
            r = client.api_call("track/search", query=query, limit=limit)
            items = r.get('tracks', {}).get('items', [])
            for t in items: 
                t['source'] = 'qobuz'
                fix_qobuz_title(t)
            return items
        elif type == 'album':
            r = client.api_call("album/search", query=query, limit=limit)
            items = r.get('albums', {}).get('items', [])
            for a in items: a['source'] = 'qobuz'
            return items
    except Exception as e:
        logger.error(f"Qobuz Thread Error: {e}")
        return []

def resolve_single_track(t, default_img):
    """Fonction helper pour résoudre une seule piste Deezer vers Qobuz/Subsonic"""
    track_title = t['title']
    track_artist = t['artist']['name']
    
    # Image par défaut (Deezer)
    final_img = t.get('album', {}).get('cover_xl') or t.get('album', {}).get('cover_medium') or default_img
    
    search_q = f"{track_title} {track_artist}"
    found_track = None
    
    # 1. Essai Qobuz
    if client:
        try:
            qs = client.api_call("track/search", query=search_q, limit=1)
            q_items = qs.get('tracks', {}).get('items', [])
            if q_items:
                found_track = q_items[0]
                found_track['source'] = 'qobuz'
                fix_qobuz_title(found_track)
        except: pass
    
    # 2. Essai Subsonic si Qobuz échoue
    if not found_track:
        ts = fetch_subsonic_tracks(search_q, limit=1)
        if ts: found_track = ts[0]

    # 3. Formatage
    if found_track:
        if found_track['source'] == 'qobuz':
            if found_track.get('album', {}).get('image', {}).get('large'):
                final_img = found_track['album']['image']['large'].replace('_300', '_600')
        elif found_track['source'] == 'subsonic':
            if found_track.get('album', {}).get('image', {}).get('large'):
                cover_id = found_track['album']['image']['large']
                final_img = f"/get_subsonic_cover/{cover_id}"

        return {
            'id': found_track['id'],
            'title': found_track['title'],
            'artist': track_artist,
            'image': final_img,
            'source': found_track['source'],
            'maximum_bit_depth': found_track.get('maximum_bit_depth', 16),
            'duration': found_track.get('duration', 0)
        }
    return None

# --- ROUTES ---

@app.route('/search')
def search_tracks():
    query = request.args.get('q')
    search_type = request.args.get('type', 'all')
    
    combined_tracks = []
    albums_results = []
    
    qobuz_tracks = []
    subsonic_tracks = []
    qobuz_albums = []
    subsonic_albums = []

    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {}
        if search_type in ['track', 'all']:
            futures['q_tracks'] = executor.submit(threaded_qobuz_search, query, 25, 'track')
            futures['s_tracks'] = executor.submit(fetch_subsonic_tracks, query, 25)
        if search_type in ['album', 'all']:
            futures['q_albums'] = executor.submit(threaded_qobuz_search, query, 15, 'album')
            futures['s_albums'] = executor.submit(fetch_subsonic_albums, query, 15)
            
        if 'q_tracks' in futures: qobuz_tracks = futures['q_tracks'].result()
        if 's_tracks' in futures: subsonic_tracks = futures['s_tracks'].result()
        if 'q_albums' in futures: qobuz_albums = futures['q_albums'].result()
        if 's_albums' in futures: subsonic_albums = futures['s_albums'].result()

    if search_type in ['track', 'all']:
        sigs = set()
        for t in qobuz_tracks:
            sig = f"{clean_string(t['title'])}_{clean_string(t['performer']['name'])}"
            sigs.add(sig)
            combined_tracks.append(t)
        for t in subsonic_tracks:
            sig = f"{clean_string(t['title'])}_{clean_string(t['performer']['name'])}"
            if sig not in sigs: combined_tracks.append(t)

    if search_type in ['album', 'all']:
        album_sigs = set()
        for a in qobuz_albums:
            artist_name = a.get('artist', {}).get('name', '')
            sig = f"{clean_string(a['title'])}_{clean_string(artist_name)}"
            album_sigs.add(sig)
            albums_results.append(a)
        for a in subsonic_albums:
            artist_name = a.get('artist', {}).get('name', '')
            sig = f"{clean_string(a['title'])}_{clean_string(artist_name)}"
            if sig not in album_sigs:
                albums_results.append(a)

    return jsonify({"tracks": combined_tracks, "albums": albums_results})

@app.route('/track')
def get_track_info():
    track_id = request.args.get('id')
    source = request.args.get('source')
    
    if source == 'subsonic':
        url = SUBSONIC_BASE + "getSong.view"
        params = get_subsonic_query_params()
        params['id'] = track_id
        try:
            res = requests.get(url, params=params).json()
            song = res['subsonic-response']['song']
            album_title = song.get('album', 'Album Inconnu')
            return jsonify({
                'id': song['id'],
                'title': song['title'],
                'performer': {'name': song['artist']},
                'album': {'title': album_title, 'image': {'large': song.get('coverArt')}},
                'duration': song['duration'],
                'source': 'subsonic'
            })
        except: return jsonify({"error": "Not found"}), 404

    if not client: return jsonify({"error": "Init error"}), 500
    try:
        res = client.get_track_meta(track_id)
        if res and 'id' in res: 
            res['source'] = 'qobuz'
            fix_qobuz_title(res)
            return jsonify(res)
    except: pass
    
    return jsonify({"error": "Not found"}), 404

@app.route('/album')
def get_album():
    album_id = request.args.get('id')
    source = request.args.get('source')
    
    if source == 'subsonic':
        url = SUBSONIC_BASE + "getAlbum.view"
        params = get_subsonic_query_params()
        params['id'] = album_id
        try:
            res = requests.get(url, params=params).json()
            if 'subsonic-response' in res and 'album' in res['subsonic-response']:
                raw = res['subsonic-response']['album']
                formatted_tracks = []
                if 'song' in raw:
                    for song in raw['song']:
                        formatted_tracks.append({
                            'id': song['id'],
                            'title': song['title'],
                            'duration': song.get('duration', 0),
                            'track_number': song.get('track', 0),
                            'performer': {'name': song.get('artist', raw.get('artist'))},
                            'album': {'title': raw.get('name'), 'image': {'large': raw.get('coverArt')}},
                            'source': 'subsonic'
                        })
                return jsonify({
                    'id': raw['id'],
                    'title': raw.get('name', raw.get('title', 'Album')),
                    'artist': {'name': raw.get('artist')},
                    'image': {'large': raw.get('coverArt')},
                    'source': 'subsonic',
                    'tracks': {'items': formatted_tracks}
                })
        except Exception as e:
            return jsonify({"error": "Subsonic Album Error"}), 500

    if client:
        try:
            res = client.get_album_meta(album_id)
            if res and 'id' in res: 
                res['source'] = 'qobuz'
                if 'tracks' in res and 'items' in res['tracks']:
                    for t in res['tracks']['items']:
                        fix_qobuz_title(t)
                return jsonify(res)
        except: pass
        
    return jsonify({"error": "Not found"}), 404

@app.route('/recommend')
def recommend_tracks():
    if not client: return jsonify({"error": "Init error"}), 500
    
    current_artist = request.args.get('artist')
    current_title = request.args.get('title')
    current_id = request.args.get('current_id')
    
    signatures_str = request.args.get('signatures', '')
    banned_signatures = signatures_str.split(',') if signatures_str else []
    current_sig = f"{clean_string(current_title)}_{clean_string(current_artist)}"
    banned_signatures.append(current_sig)
    
    def try_resolve_track(title, artist):
        search_query = f"{title} {artist}"
        try:
            q_resp = client.api_call("track/search", query=search_query, limit=1)
            items = q_resp.get('tracks', {}).get('items', [])
            if items:
                rec = items[0]
                rec['source'] = 'qobuz'
                fix_qobuz_title(rec)
                if str(rec['id']) != str(current_id): return rec
        except: pass
        
        subs = fetch_subsonic_tracks(search_query, limit=1)
        if subs:
            rec = subs[0]
            if str(rec['id']) != str(current_id): return rec
            
        return None

    try:
        global SUGGESTION_RESERVE
        suggestions = fetch_lastfm_recommendations(current_artist, current_title)
        
        if suggestions:
            random.shuffle(suggestions)
            valid_candidates = []
            for rec_title, rec_artist in suggestions:
                if clean_string(rec_artist) == clean_string(current_artist): continue
                rec_sig = f"{clean_string(rec_title)}_{clean_string(rec_artist)}"
                if rec_sig in banned_signatures: continue
                valid_candidates.append((rec_title, rec_artist))
            
            if valid_candidates:
                main_title, main_artist = valid_candidates[0]
                for i in range(1, min(3, len(valid_candidates))):
                    SUGGESTION_RESERVE.append(valid_candidates[i])
                
                if len(SUGGESTION_RESERVE) > 10: SUGGESTION_RESERVE = SUGGESTION_RESERVE[-10:]
                res = try_resolve_track(clean_title_for_search(main_title), main_artist)
                if res: return jsonify(res)

        if SUGGESTION_RESERVE:
            random.shuffle(SUGGESTION_RESERVE)
            while SUGGESTION_RESERVE:
                res_title, res_artist = SUGGESTION_RESERVE.pop(0)
                if f"{clean_string(res_title)}_{clean_string(res_artist)}" in banned_signatures: continue
                res = try_resolve_track(clean_title_for_search(res_title), res_artist)
                if res: return jsonify(res)

        fallback_resp = client.api_call("track/search", query=current_artist, limit=50)
        fallback_items = fallback_resp.get('tracks', {}).get('items', [])
        random.shuffle(fallback_items)
        if fallback_items:
            item = fallback_items[0]
            item['source'] = 'qobuz'
            fix_qobuz_title(item)
            return jsonify(item)

        return jsonify({"error": "None"}), 404

    except Exception as e:
        logger.error(f"Recommend Error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/artist_bio')
def get_artist_bio():
    artist_name = request.args.get('name')
    if not artist_name: return jsonify({"error": "No name"}), 400
    
    data = {"name": artist_name, "bio": "", "image": "", "top_tracks": [], "nb_fans": 0}
    
    # Helpers pour threads
    def fetch_deezer_info():
        try:
            dz_search = requests.get(f'https://api.deezer.com/search/artist?q={urllib.parse.quote(artist_name)}', timeout=4).json()
            if dz_search.get('data'):
                return dz_search['data'][0]
        except: pass
        return None

    def fetch_wikipedia():
        try:
            wiki_name = urllib.parse.quote(artist_name.replace(' ', '_'))
            wiki_url = f"https://fr.wikipedia.org/api/rest_v1/page/summary/{wiki_name}"
            headers = {'User-Agent': 'ZenithPlayer/1.0'}
            resp = requests.get(wiki_url, headers=headers, timeout=4)
            if resp.status_code == 200:
                return resp.json().get('extract')
        except: pass
        return None

    def fetch_qobuz_image():
        if client:
            try:
                q_search = client.api_call("artist/search", query=artist_name, limit=1)
                if q_search.get('artists', {}).get('items'):
                    q_artist = q_search['artists']['items'][0]
                    if clean_string(q_artist['name']) == clean_string(artist_name):
                         if 'image' in q_artist and q_artist['image']:
                            return q_artist['image'].get('large', '').replace('_300', '_600')
                         elif 'picture' in q_artist and q_artist['picture']:
                            return q_artist['picture'].get('large', '').replace('_300', '_600')
            except: pass
        return None

    # Etape 1 : Récupération des infos globales en parallèle
    deezer_artist = None
    with ThreadPoolExecutor(max_workers=3) as executor:
        f_dz = executor.submit(fetch_deezer_info)
        f_wiki = executor.submit(fetch_wikipedia)
        f_img = executor.submit(fetch_qobuz_image)
        
        deezer_artist = f_dz.result()
        data['bio'] = f_wiki.result() or ""
        q_image = f_img.result()

    # Gestion de l'image (Priorité Qobuz > Deezer)
    if q_image:
        data['image'] = q_image
    elif deezer_artist:
        data['image'] = deezer_artist.get('picture_xl') or deezer_artist.get('picture_medium') or ""
    
    if deezer_artist:
        data['nb_fans'] = deezer_artist.get('nb_fan', 0)
        
        # Etape 2 : Récupération du Top Tracks Deezer
        deezer_id = deezer_artist['id']
        try:
            dz_top = requests.get(f'https://api.deezer.com/artist/{deezer_id}/top?limit=10', timeout=4).json()
            
            # Etape 3 : Résolution massive des pistes en parallèle
            if 'data' in dz_top:
                tasks = []
                with ThreadPoolExecutor(max_workers=10) as executor:
                    for t in dz_top['data']:
                        tasks.append(executor.submit(resolve_single_track, t, data['image']))
                    
                    for future in as_completed(tasks):
                        res = future.result()
                        if res:
                            data['top_tracks'].append(res)
                            
        except Exception as e:
            logger.error(f"Artist Top Tracks Error: {e}")

    return jsonify(data)

# --- MISE A JOUR : REDIRECTION VERS PROXY SUPABASE ---

@app.route('/stream_subsonic/<track_id>')
def stream_subsonic(track_id):
    # On génère l'URL Subsonic
    url = SUBSONIC_BASE + "stream.view"
    params = get_subsonic_query_params()
    params['id'] = track_id
    params['format'] = 'mp3' 
    params['maxBitRate'] = '320'
    params['estimateContentLength'] = 'true'
    req = requests.Request('GET', url, params=params)
    prepared = req.prepare()
    
    # On redirige vers Supabase en passant l'URL en paramètre
    target = urllib.parse.quote(prepared.url)
    return redirect(f"{SUPABASE_PROXY_URL}?url={target}")

@app.route('/stream/<track_id>')
def stream_track(track_id):
    if not client: return jsonify({"error": "Init error"}), 500
    formats_to_try = [27, 7, 6, 5]
    for fmt in formats_to_try:
        try:
            # On récupère l'URL signée Qobuz
            url_data = client.get_track_url(track_id, fmt)
            if 'url' in url_data:
                # On redirige vers Supabase pour le proxying
                target = urllib.parse.quote(url_data['url'])
                return redirect(f"{SUPABASE_PROXY_URL}?url={target}")
        except Exception: continue
    return jsonify({"error": "No URL found"}), 404

@app.route('/get_subsonic_cover/<cover_id>')
def get_subsonic_cover(cover_id):
    url = SUBSONIC_BASE + "getCoverArt.view"
    params = get_subsonic_query_params()
    params['id'] = cover_id
    params['size'] = 600 
    req = requests.Request('GET', url, params=params)
    prepared = req.prepare()
    return redirect(prepared.url)

@app.route('/')
def home():
    try: return send_file('../index.html')
    except: return "Index not found"

@app.route('/artist')
def get_artist():
    if not client: return jsonify({"error": "Init error"}), 500
    try: return jsonify(client.api_call("artist/get", id=request.args.get('id'), extra="albums", limit=20))
    except: return jsonify({})

@app.route('/lyrics')
def get_lyrics():
    try:
        plain, synced = lyrics_engine.search_lyrics(request.args.get('artist'), request.args.get('title'), request.args.get('album'), int(float(request.args.get('duration', 0))))
        if synced: return jsonify({"type": "synced", "lyrics": synced})
        elif plain: return jsonify({"type": "plain", "lyrics": plain})
        else: return jsonify({"type": "none", "lyrics": None}), 404
    except: return jsonify({"type": "none", "lyrics": None}), 404

@app.route('/blind_test_tracks')
def get_blind_test_tracks():
    if not client: return jsonify({"error": "Init error"}), 500
    try:
        resp = client.api_call("track/search", query="Hits 2024", limit=30)
        items = resp.get('tracks', {}).get('items', [])
        
        # Ajout des versions pour le blind test
        for t in items:
            fix_qobuz_title(t)
            
        random.shuffle(items)
        return jsonify(items[:10])
    except: return jsonify([])

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)