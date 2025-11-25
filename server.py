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
# En local, on peut taper directement l'URL si CORS le permet, 
# ou utiliser le proxy si on veut tester le comportement Vercel.
SUPABASE_PROXY_URL = "https://mzxfcvzqxgslyopkkaej.supabase.co/functions/v1/stream-proxy"

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ZenithServerLocal")

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
            "User-Agent": "Zenith Player Local",
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
    
    # Liste des termes interdits (Titres & Artistes)
    banned_terms = [
        # Émissions TV / Concours
        "the voice", "got talent", "x factor", "idol", "audition", "battle", "incroyable talent",
        "c à vous", "tpmp", "quotidien", "taratata", "grand journal", "clash", "interview",
        
        # Reprises / Covers
        "cover", "reprise", "tribute", "hommage", "version guitare", "piano version",
        
        # Modifications Audio / DJ / Remix non officiels
        "slowed", "reverb", "sped up", "nightcore", "chipmunk", "daycore", "bass boosted",
        "remix", "mix", "mashup", "medley", "megamix", "dj ", "edit", "lofi", "type beat",
        
        # Contenu YouTubeur / Méta
        "react", "reaction", "review", "analise", "explication", "paroles", "lyrics", "letra",
        "guitar hero", "synthesia", "tutorial", "tuto", "lesson", "backing track", "karaoke", "instrumental",
        "8d audio", "3d audio",
        
        # Live (On privilégie le studio pour la radio)
        "live at", "live in", "concert", "en live", "au zénith", "stade de france", "bercy"
    ]
    
    full_str = f"{t} {a}"
    
    for term in banned_terms:
        if term in full_str:
            # Exception: Si l'artiste est un DJ connu, le mot "mix" ou "dj" peut être légitime, 
            # mais pour la radio on veut le titre original. On filtre quand même.
            return True, term
            
    return False, None

def fetch_yt_synced_lyrics(title, artist):
    """Récupère les paroles synchronisées depuis YouTube Music avec Logs détaillés"""
    query = f"{title} {artist}"
    logger.info(f"🔍 YT Lyrics: Recherche pour '{query}'")
    
    try:
        # 1. Trouver la chanson (videoId)
        results = yt.search(query, filter="songs", limit=1)
        if not results:
            logger.warning(f"❌ YT Lyrics: Aucun résultat de recherche pour '{query}'")
            return None
        
        video_id = results[0]['videoId']
        track_title = results[0].get('title', 'Inconnu')
        logger.info(f"✅ YT Lyrics: Vidéo trouvée [{video_id}] - {track_title}")
        
        # 2. Obtenir la Watch Playlist pour avoir l'ID des paroles
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
        
        # 3. Récupérer les paroles avec timestamps
        lyrics_data = None
        
        # TENTATIVE 1 : AVEC TIMESTAMPS=TRUE
        try:
            logger.info("ℹ️ YT Lyrics: Tentative récupération avec timestamps=True...")
            lyrics_data = yt.get_lyrics(lyrics_id, timestamps=True)
        except TypeError:
            logger.warning("⚠️ YT Lyrics: L'option timestamps=True n'est pas supportée par cette version de ytmusicapi.")
        except Exception as e:
            logger.error(f"⚠️ YT Lyrics: Erreur avec timestamps=True: {e}")

        # TENTATIVE 2 : SANS OPTION (Fallback)
        if not lyrics_data:
            logger.info("ℹ️ YT Lyrics: Tentative récupération standard...")
            try:
                lyrics_data = yt.get_lyrics(lyrics_id)
            except Exception as e:
                logger.error(f"❌ YT Lyrics: Erreur fallback: {e}")
                return None
        
        if not lyrics_data:
            logger.warning("❌ YT Lyrics: Données vides retournées.")
            return None

        # 4. ANALYSE DU RÉSULTAT
        lyrics_content = lyrics_data.get('lyrics')
        logger.info(f"ℹ️ YT Lyrics: Type de données reçu: {type(lyrics_content)}")

        # CAS A : C'est une liste (Timestamps trouvés !)
        if isinstance(lyrics_content, list):
            logger.info("✅ YT Lyrics: Format LISTE détecté (Synchronisé !)")
            
            lrc_lines = []
            for i, line in enumerate(lyrics_content):
                try:
                    # On supporte les clés 'start_time' ou 'seconds' selon les versions
                    t = None
                    txt = ""
                    is_ms = False

                    # 1. Gestion Objet LyricLine (Nouveau ytmusicapi)
                    if hasattr(line, 'start_time') and hasattr(line, 'text'):
                        t = line.start_time
                        txt = line.text
                        is_ms = True # LyricLine stocke en MS
                    
                    # 2. Gestion Dictionnaire (Vieux format ou autre endpoint)
                    elif isinstance(line, dict):
                        t = line.get('start_time', line.get('seconds', line.get('startTime')))
                        txt = line.get('text', line.get('line', ''))
                        is_ms = False # Les dicts stockaient souvent en secondes
                    
                    if t is not None:
                         val = float(t)
                         # Conversion si c'était des secondes
                         final_ms = val if is_ms else (val * 1000)
                         lrc_lines.append(f"{ms_to_lrc(final_ms)} {txt}")
                except Exception as ex:
                    if i == 0: logger.error(f"❌ Erreur parsing ligne 1: {ex}")
                    continue
            
            if lrc_lines:
                return "\n".join(lrc_lines)
            else:
                logger.warning("⚠️ YT Lyrics: Liste trouvée mais pas de timestamps exploitables.")

        # CAS B : C'est une string (Plain text)
        elif isinstance(lyrics_content, str):
             logger.info("⚠️ YT Lyrics: Format STRING détecté (Non Synchronisé).")
             return None 
             
        return None
        
    except Exception as e:
        logger.error(f"💥 YT Lyrics: Exception non gérée: {e}")
        return None

# --- RECHERCHE RECOMMENDATIONS YOUTUBE (Algorithme Utilisateur) ---
def get_yt_recommendations(title, artist, banned_artists=set()):
    """
    Algorithme Radio Intelligent :
    1. Recherche la chanson 'seed' avec précision (guillemets)
    2. Combine:
       - La "Watch Playlist" (File d'attente automatique YT)
       - Les "Related Content" (Suggestions connexes)
    3. Filtre STRICTEMENT les artistes bannis et le contenu 'garbage'
    """
    # Utilisation des guillemets pour forcer la distinction Titre / Artiste
    search_query = f'"{title}" "{artist}"'
    logger.info(f"📻 Radio YT: Recherche précise pour '{search_query}'")
    logger.info(f"🚫 BLACKLIST (Quota dépassé): {banned_artists}")
    
    try:
        # 1. Rechercher la chanson
        search_results = yt.search(search_query, filter='songs', limit=1)
        
        if not search_results:
            # Fallback sans guillemets si trop strict
            logger.warning("⚠️ Recherche stricte échouée, tentative standard...")
            search_results = yt.search(f"{title} {artist}", filter='songs', limit=1)

        if not search_results:
            logger.warning("❌ YT Radio: Aucune musique trouvée pour la graine.")
            return None

        target_song = search_results[0]
        video_id = target_song['videoId']
        logger.info(f"✅ Graine trouvée: {target_song.get('title')} (ID: {video_id})")

        # 2. Obtenir la Watch Playlist ET le Related Browse ID
        try:
            watch_playlist = yt.get_watch_playlist(videoId=video_id)
            related_browse_id = watch_playlist.get('related')
            
            # SOURCE 1: La file d'attente automatique (Souvent ~20-50 titres)
            raw_candidates = watch_playlist.get('tracks', [])
            logger.info(f"ℹ️ YT Radio: {len(raw_candidates)} titres trouvés dans la Watch Playlist.")
            
        except Exception as e:
            logger.error(f"❌ YT Radio: Erreur watch_playlist: {e}")
            return None

        # SOURCE 2: Les contenus connexes (Si disponible)
        if related_browse_id:
            try:
                related_content = yt.get_song_related(related_browse_id)
                count_related = 0
                for section in related_content:
                    contents = section.get('contents')
                    if isinstance(contents, list):
                        raw_candidates.extend(contents)
                        count_related += len(contents)
                logger.info(f"ℹ️ YT Radio: {count_related} titres ajoutés depuis les contenus connexes.")
            except Exception as e:
                logger.warning(f"⚠️ YT Radio: Erreur lors de la récupération des contenus connexes: {e}")

        candidates = []
        
        # 4. Filtrage et Sélection
        # On utilise un set pour éviter les doublons car un titre peut être dans les deux listes
        seen_ids = set()

        for item in raw_candidates:
            if 'videoId' in item and 'playlistId' not in item:
                r_id = item.get('videoId')
                
                if r_id in seen_ids: continue
                seen_ids.add(r_id)

                r_title = item.get('title', 'Inconnu')
                
                artists = item.get('artists', [])
                r_artist_name = artists[0]['name'] if artists else "Artiste inconnu"
                r_artist_clean = clean_string(r_artist_name)
                
                # LOG SYSTÉMATIQUE
                log_prefix = "✅"
                rejection_reason = ""

                # A. VÉRIFICATION DU BAN (QUOTA)
                if r_artist_clean in banned_artists:
                    log_prefix = "❌"
                    rejection_reason = f" [REJET: Artiste Banni (Quota atteint)]"
                
                # B. VÉRIFICATION DOUBLON TITRE
                elif clean_string(r_title) == clean_string(title):
                    log_prefix = "❌"
                    rejection_reason = " [REJET: Doublon Titre]"
                    
                # C. VÉRIFICATION CONTENU POUBELLE (TV, COVERS, REMIX, ETC.)
                else:
                    is_bad, bad_term = is_garbage_content(r_title, r_artist_name)
                    if is_bad:
                        log_prefix = "❌"
                        rejection_reason = f" [REJET: Contenu de basse qualité ({bad_term})]"
                
                # logger.info(f"{log_prefix} Vu: {r_title} - {r_artist_name}{rejection_reason}")

                if log_prefix == "❌":
                    continue

                # Image HD (Gérer les clés variables 'thumbnail' vs 'thumbnails')
                thumbnails = item.get('thumbnails', item.get('thumbnail', []))
                r_img = get_hq_yt_image(thumbnails[-1]['url']) if thumbnails else 'https://via.placeholder.com/300'
                
                # Album
                r_album = "YouTube"
                if 'album' in item:
                    if isinstance(item['album'], dict): r_album = item['album'].get('name', 'YouTube')
                    elif isinstance(item['album'], str): r_album = item['album']

                candidates.append({
                    "id": r_id,
                    "title": r_title,
                    "performer": { "name": r_artist_name },
                    "album": { "title": r_album, "image": { "large": r_img } },
                    "source": "yt_lazy",
                    "img": r_img,
                    "type": "track"
                })
        
        if candidates:
            logger.info(f"✅ Radio YT: {len(candidates)} candidats valides (Total analysé: {len(raw_candidates)}).")
            selection = random.choice(candidates)
            logger.info(f"🎵 TITRE CHOISI : {selection['title']} - {selection['performer']['name']}")
            return selection
        else:
            logger.warning("❌ YT Radio: Aucun candidat après filtrage (Trop de répétitions ou mauvaise qualité).")
            return None

    except Exception as e:
        logger.error(f"❌ Radio YT Error: {e}")
        return None

# --- API SUBSONIC ---
def get_subsonic_query_params():
    salt = ''.join(random.choices(string.ascii_letters + string.digits, k=6))
    token_str = SUBSONIC_PASSWORD + salt
    token = hashlib.md5(token_str.encode('utf-8')).hexdigest()
    return { 'u': SUBSONIC_USER, 's': salt, 't': token, 'v': SUBSONIC_VERSION, 'c': SUBSONIC_CLIENT, 'f': 'json' }

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

# --- AJOUT FONCTIONS MANQUANTES ---

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
    try:
        q_resp = client.api_call("track/search", query=search_query, limit=1)
        items = q_resp.get('tracks', {}).get('items', [])
        if items:
            rec = items[0]
            if fuzz.ratio(clean_string(rec['title']), clean_string(title)) > 50:
                return {'id': rec['id'], 'source': 'qobuz'}
    except: pass
    
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
    
    # Récupération de l'historique des artistes
    recent_artists_str = request.args.get('recent_artists', '')
    # Nettoyage pour avoir une liste propre
    recent_artists_raw = recent_artists_str.split(',') if recent_artists_str else []
    recent_artists = [clean_string(a) for a in recent_artists_raw if a]
    
    # --- LOGIQUE DE BANNISSEMENT : QUOTA 2 SUR 5 ---
    # Si un artiste apparaît 2 fois ou plus dans les 5 derniers titres, il est banni.
    banned_artists = set()
    
    # On compte les occurrences dans l'historique récent
    artist_counts = {}
    for artist in recent_artists:
        artist_counts[artist] = artist_counts.get(artist, 0) + 1
    
    for artist, count in artist_counts.items():
        if count >= 2:
            banned_artists.add(artist)
            logger.info(f"🚫 BANNISSEMENT ACTIVÉ: '{artist}' est apparu {count} fois récemment.")

    # 1. TENTATIVE YOUTUBE MUSIC (AVEC FILTRE BANNIS + QUALITÉ)
    yt_rec = get_yt_recommendations(original_title, original_artist, banned_artists)
    if yt_rec:
        return jsonify(yt_rec)
        
    # 2. FALLBACK QOBUZ (AVEC FILTRE BANNIS)
    if client:
        try:
            track_id = request.args.get('current_id')
            track_meta = client.get_track_meta(track_id)
            genre_name = track_meta.get('album', {}).get('genre', {}).get('name')
            
            # Si l'artiste original est banni, on force la recherche par genre
            original_clean = clean_string(original_artist)
            if original_clean in banned_artists:
                search_query = genre_name if genre_name else "Pop Global"
                logger.info(f"⚠️ Fallback Qobuz: Artiste '{original_artist}' banni, recherche par Genre '{search_query}'")
            else:
                search_query = genre_name if genre_name else original_artist
            
            resp = client.api_call("track/search", query=search_query, limit=50)
            items = resp.get('tracks', {}).get('items', [])
            random.shuffle(items)
            
            for item in items:
                if str(item['id']) == str(track_id): continue
                
                candidate_artist = item.get('performer', {}).get('name', 'Inconnu')
                candidate_clean = clean_string(candidate_artist)
                
                # VERIFICATION STRICTE BAN (QUOTA)
                if candidate_clean in banned_artists:
                    logger.info(f"❌ Qobuz: Rejeté {candidate_artist} (Banni par Quota)")
                    continue
                
                # Éviter doublon immédiat titre
                if candidate_clean == clean_string(original_artist) and clean_string(item['title']) == clean_string(original_title):
                     continue

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
        url = SUBSONIC_BASE + "getSong.view"; params = get_subsonic_query_params(); params['id'] = track_id
        try:
            res = requests.get(url, params=params).json(); song = res['subsonic-response']['song']
            return jsonify({'id': song['id'], 'title': song['title'], 'performer': {'name': song['artist']}, 'album': {'title': song.get('album', 'Album'), 'image': {'large': song.get('coverArt')}}, 'duration': song['duration'], 'source': 'subsonic'})
        except: return jsonify({"error": "Not found"}), 404
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