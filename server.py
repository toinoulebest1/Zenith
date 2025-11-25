from flask import Flask, jsonify, redirect, request, send_file, Response, stream_with_context
from flask_cors import CORS
from qobuz_api import QobuzClient, get_app_credentials
from lyrics_search import LyricsSearcher 
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

app = Flask(__name__)
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

def get_hq_yt_image(url):
    """Transforme une URL de miniature YT standard en version HD Carrée."""
    if not url: return 'https://via.placeholder.com/300'
    return re.sub(r'w\d+-h\d+(-l\d+)?', 'w600-h600-l100', url)

def ms_to_lrc(ms):
    """Convertit des millisecondes en format timestamp LRC [mm:ss.xx]"""
    seconds = (ms / 1000)
    minutes = int(seconds // 60)
    rem_seconds = seconds % 60
    return f"[{minutes:02d}:{rem_seconds:05.2f}]"

def is_garbage_content(title, artist):
    """
    Détecte si le contenu est de "basse qualité" ou indésirable (TV, Cover, Slowed, Remix, Live, etc.)
    """
    t = title.lower()
    a = artist.lower()
    
    banned_terms = [
        "the voice", "got talent", "x factor", "idol", "audition", "battle", "incroyable talent",
        "c à vous", "tpmp", "quotidien", "taratata", "grand journal", "clash", "interview",
        "cover", "reprise", "tribute", "hommage", "version guitare", "piano version",
        "slowed", "reverb", "sped up", "nightcore", "chipmunk", "daycore", "bass boosted",
        "remix", "mix", "mashup", "medley", "megamix", "dj ", "edit", "lofi", "type beat",
        "react", "reaction", "review", "analise", "explication", "paroles", "lyrics", "letra",
        "guitar hero", "synthesia", "tutorial", "tuto", "lesson", "backing track", "karaoke", "instrumental",
        "8d audio", "3d audio",
        "live at", "live in", "concert", "en live", "au zénith", "stade de france", "bercy"
    ]
    
    full_str = f"{t} {a}"
    for term in banned_terms:
        if term in full_str:
            return True, term
            
    return False, None

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

# --- RECHERCHE RECOMMENDATIONS INTELLIGENTE (YT -> CHECK QOBUZ/SUBSONIC) ---
def get_yt_recommendations(title, artist, banned_artists=set()):
    """
    Algorithme Radio Amélioré :
    1. Récupère une liste de candidats depuis l'algo YouTube (Watch Playlist + Related).
    2. Pour chaque candidat :
       a. Vérifie s'il existe sur Qobuz ou Subsonic (try_resolve_track).
       b. SI OUI : Renvoie la version Qobuz/Subsonic (Meilleure qualité + Image officielle).
       c. SI NON : Passe au candidat suivant (car l'utilisateur veut prioriser Qobuz/Subsonic).
    3. Si aucun candidat n'est trouvé sur les plateformes Hi-Res après X essais, renvoie None (Fallback Qobuz).
    """
    search_query = f'"{title}" "{artist}"'
    logger.info(f"📻 Radio YT: Recherche graine '{search_query}'")
    
    try:
        # 1. Graine
        search_results = yt.search(search_query, filter='songs', limit=1)
        if not search_results: search_results = yt.search(f"{title} {artist}", filter='songs', limit=1)
        if not search_results: return None

        target_song = search_results[0]
        video_id = target_song['videoId']
        
        # 2. Candidats
        raw_candidates = []
        try:
            watch_playlist = yt.get_watch_playlist(videoId=video_id)
            raw_candidates.extend(watch_playlist.get('tracks', []))
            
            related_browse_id = watch_playlist.get('related')
            if related_browse_id:
                related_content = yt.get_song_related(related_browse_id)
                for section in related_content:
                    contents = section.get('contents')
                    if isinstance(contents, list): raw_candidates.extend(contents)
        except Exception as e:
            logger.error(f"❌ YT Radio: Erreur playlist: {e}")
            return None

        # 3. Filtrage et Vérification Qobuz/Subsonic
        seen_ids = set()
        candidates = []

        # Pré-filtrage rapide pour avoir une liste propre
        for item in raw_candidates:
            if 'videoId' in item and 'playlistId' not in item:
                r_id = item.get('videoId')
                if r_id in seen_ids: continue
                seen_ids.add(r_id)
                
                r_title = item.get('title', 'Inconnu')
                artists = item.get('artists', [])
                r_artist_name = artists[0]['name'] if artists else "Artiste inconnu"
                
                # Filtres de base
                if clean_string(r_artist_name) in banned_artists: continue
                if clean_string(r_title) == clean_string(title): continue
                is_bad, _ = is_garbage_content(r_title, r_artist_name)
                if is_bad: continue
                
                candidates.append({'title': r_title, 'artist': r_artist_name})

        # Mélange pour la variété
        random.shuffle(candidates)
        
        # 4. BOUCLE DE VÉRIFICATION (Max 5 tentatives pour ne pas être trop lent)
        max_tries = 5
        checked_count = 0
        
        logger.info(f"🔍 Radio: Vérification de l'existence sur Qobuz/Subsonic pour {len(candidates)} candidats...")

        for cand in candidates:
            if checked_count >= max_tries:
                break
            
            c_title = cand['title']
            c_artist = cand['artist']
            
            logger.info(f"👉 Test #{checked_count+1}: {c_title} - {c_artist}")
            
            # Appel à la fonction qui cherche sur Qobuz/Subsonic
            match = try_resolve_track(c_title, c_artist)
            
            if match:
                source = match['source']
                real_id = match['id']
                
                if source == 'qobuz' and client:
                    try:
                        meta = client.get_track_meta(real_id)
                        meta['source'] = 'qobuz'
                        fix_qobuz_title(meta)
                        # Pour s'assurer qu'on a l'image HD
                        if meta.get('album', {}).get('image', {}).get('large'):
                            meta['img'] = meta['album']['image']['large'].replace('_300', '_600')
                        
                        logger.info(f"✅ TROUVÉ SUR QOBUZ: {meta['title']}")
                        return meta
                    except Exception as e:
                        logger.error(f"Erreur fetch Qobuz meta: {e}")
                
                elif source == 'subsonic':
                    try:
                        song = get_subsonic_track_details(real_id)
                        if song:
                            logger.info(f"✅ TROUVÉ SUR SUBSONIC: {song['title']}")
                            return song
                    except Exception as e:
                        logger.error(f"Erreur fetch Subsonic meta: {e}")
            else:
                logger.info("❌ Non trouvé sur les plateformes Hi-Res. Suivant...")

            checked_count += 1
            
        logger.warning("❌ Radio: Aucun candidat YouTube n'existe sur Qobuz/Subsonic après vérification.")
        return None # On renvoie None pour déclencher le fallback Qobuz interne

    except Exception as e:
        logger.error(f"❌ Radio YT Error: {e}")
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
    """Tente de trouver un ID Qobuz ou Subsonic pour un titre/artiste donné"""
    search_query = f"{title} {artist}"
    
    # 1. Qobuz
    if client:
        try:
            q_resp = client.api_call("track/search", query=search_query, limit=1)
            items = q_resp.get('tracks', {}).get('items', [])
            if items:
                rec = items[0]
                
                # Logique de matching Robuste (Fonctionne SANS RapidFuzz)
                t1 = clean_string(rec['title'])
                t2 = clean_string(title)
                
                is_match = False
                if FUZZ_AVAILABLE:
                    if fuzz.ratio(t1, t2) > 50: is_match = True
                else:
                    # Fallback si rapidfuzz manque : vérification d'inclusion simple
                    if t2 in t1 or t1 in t2: is_match = True
                    
                if is_match:
                    return {'id': rec['id'], 'source': 'qobuz'}
        except Exception as e:
            logger.error(f"Resolve Qobuz error: {e}")
    
    # 2. Subsonic
    subs = fetch_subsonic_tracks(search_query, limit=1)
    if subs:
        return {'id': subs[0]['id'], 'source': 'subsonic'}
    
    return None

# --- ROUTES ---

@app.route('/recommend')
def recommend_tracks():
    original_artist = request.args.get('artist', '')
    original_title = request.args.get('title', '')
    
    recent_artists_str = request.args.get('recent_artists', '')
    recent_artists_raw = recent_artists_str.split(',') if recent_artists_str else []
    recent_artists = [clean_string(a) for a in recent_artists_raw if a]
    
    # QUOTA 2 SUR 5 POUR BAN
    banned_artists = set()
    artist_counts = {}
    for artist in recent_artists:
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
    
    for artist, count in artist_counts.items():
        if count >= 2:
            banned_artists.add(artist)

    # 1. TENTATIVE YOUTUBE AVEC VÉRIFICATION QOBUZ/SUBSONIC
    # Si get_yt_recommendations renvoie quelque chose, c'est désormais garanti d'être du Qobuz/Subsonic
    # (Ou None si rien n'a été trouvé)
    resolved_rec = get_yt_recommendations(original_title, original_artist, banned_artists)
    if resolved_rec:
        return jsonify(resolved_rec)
        
    # 2. FALLBACK QOBUZ (Si la vérification a échoué partout)
    if client:
        try:
            track_id = request.args.get('current_id')
            track_meta = client.get_track_meta(track_id)
            genre_name = track_meta.get('album', {}).get('genre', {}).get('name')
            
            original_clean = clean_string(original_artist)
            if original_clean in banned_artists:
                search_query = genre_name if genre_name else "Pop Global"
            else:
                search_query = genre_name if genre_name else original_artist
            
            resp = client.api_call("track/search", query=search_query, limit=50)
            items = resp.get('tracks', {}).get('items', [])
            random.shuffle(items)
            
            for item in items:
                if str(item['id']) == str(track_id): continue
                
                candidate_artist = item.get('performer', {}).get('name', 'Inconnu')
                candidate_clean = clean_string(candidate_artist)
                
                if candidate_clean in banned_artists: continue
                if candidate_clean == clean_string(original_artist) and clean_string(item['title']) == clean_string(original_title): continue

                item['source'] = 'qobuz'
                fix_qobuz_title(item)
                return jsonify(item)
                
        except Exception as e:
            logger.error(f"Qobuz Fallback Error: {e}")

    return jsonify({"error": "No recommendation found"}), 404

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
            sig = f"{clean_string(t['title'])}_{clean_string(t['performer']['name'])}"
            sigs.add(sig); combined_tracks.append(t)
        for t in subsonic_tracks:
            sig = f"{clean_string(t['title'])}_{clean_string(t['performer']['name'])}"
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
        album_art = 'https://via.placeholder.com/300'
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
                    formatted_tracks.append({ 'id': song['id'], 'title': song['title'], 'duration': song.get('duration', 0), 'track_number': song.get('track', 0), 'performer': {'name': song.get('artist', raw.get('artist'))}, 'album': {'title': raw.get('name'), 'image': {'large': raw.get('coverArt')}}, 'source': 'subsonic', 'tracks': {'items': formatted_tracks} })
            return jsonify({ 'id': raw['id'], 'title': raw.get('name'), 'artist': {'name': raw.get('artist')}, 'image': {'large': raw.get('coverArt')}, 'source': 'subsonic', 'tracks': {'items': formatted_tracks} })
        except: return jsonify({"error": "Error"}), 500
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
    for fmt in [27, 7, 6, 5]:
        try:
            url_data = client.get_track_url(track_id, 5)
            if 'url' in url_data: return redirect(f"{SUPABASE_PROXY_URL}?url={urllib.parse.quote(url_data['url'])}")
        except: continue
    return jsonify({"error": "No URL found"}), 404

@app.route('/get_subsonic_cover/<cover_id>')
def get_subsonic_cover(cover_id):
    url = SUBSONIC_BASE + "getCoverArt.view"; params = get_subsonic_query_params(); params['id'] = cover_id; params['size'] = 600 
    req = requests.Request('GET', url, params=params); prepared = req.prepare(); return redirect(prepared.url)

# --- ROUTE BLIND TEST ---
@app.route('/blind_test_tracks')
def get_blind_test_tracks():
    if not client: return jsonify({"error": "Client not initialized"}), 500
    try:
        resp = client.api_call("track/search", query="Global Hits", limit=30)
        items = resp.get('tracks', {}).get('items', [])
        random.shuffle(items)
        tracks_for_game = items[:10]
        normalized_tracks = []
        for track in tracks_for_game:
            normalized_tracks.append({
                'id': track['id'], 'title': track['title'],
                'artist': track.get('performer', {}).get('name', track.get('artist', {}).get('name', 'Unknown')),
                'album': track['album']['title'],
                'img': track.get('album', {}).get('image', {}).get('large', '').replace('_300', '_600'),
                'duration': track['duration']
            })
        return jsonify(normalized_tracks)
    except Exception as e:
        logger.error(f"Blind Test Error: {e}")
        return jsonify({"error": "Failed to fetch blind test tracks"}), 500

@app.route('/lyrics')
def get_lyrics():
    artist = request.args.get('artist'); title = request.args.get('title'); album = request.args.get('album')
    duration = request.args.get('duration')
    try:
        # CORRECTION : Gestion sécurisée de la durée (peut être 'undefined' ou vide)
        if duration and duration != 'undefined':
            dur_int = int(float(duration))
        else:
            dur_int = 0
    except (ValueError, TypeError):
        dur_int = 0
        
    try:
        # 1. PRIORITÉ : YouTube Music (Synced)
        yt_lyrics = fetch_yt_synced_lyrics(title, artist)
        if yt_lyrics:
            return jsonify({"type": "synced", "lyrics": yt_lyrics, "source": "YouTube"})

        # 2. SECONDAIRE : LRCLib (Synced puis Plain)
        plain, synced = lyrics_engine.search_lyrics(artist, title, album, dur_int)
        if synced: return jsonify({"type": "synced", "lyrics": synced, "source": "LRCLib"})
        
        if plain: return jsonify({"type": "plain", "lyrics": plain, "source": "LRCLib"})
        
    except Exception as e:
        logger.error(f"Lyrics Error: {e}")
        
    return jsonify({"type": "none", "lyrics": None}), 404

@app.route('/artist_bio')
def get_artist_bio(): return jsonify({})

if __name__ == '__main__':
    init_client()
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Serveur local lancé sur le port {port}")
    app.run(host='0.0.0.0', port=port)