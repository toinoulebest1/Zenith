#!/usr/bin/env python3
"""
ytmusic_player.py — Recherche et lecture de musique via YouTube Music.

Pipeline :
  1. ytmusicapi  -> recherche, renvoie un videoId (filtré sur les vraies pistes)
  2. yt-dlp      -> résout l'URL du flux audio (download=False, 0 téléchargement)
  3. python-vlc  -> streame le flux en RAM (play / pause / stop / volume)

Dépendances :
    pip install ytmusicapi yt-dlp python-vlc
    + VLC installé sur le système (libvlc) : https://www.videolan.org/

Aucune authentification n'est requise pour la recherche.
"""

import sys

try:
    from ytmusicapi import YTMusic
except ImportError:
    sys.exit("Manque ytmusicapi  ->  pip install ytmusicapi")

try:
    import yt_dlp
except ImportError:
    sys.exit("Manque yt-dlp  ->  pip install yt-dlp")

try:
    import vlc
except ImportError:
    sys.exit("Manque python-vlc  ->  pip install python-vlc (et VLC installé)")


# videoType renvoyé par YouTube Music :
#   ATV = audio pur YT Music (vraie track)   <- ce qu'on veut
#   OMV = clip vidéo officiel
#   UGC = upload utilisateur (lives, reprises...)
ATV = "MUSIC_VIDEO_TYPE_ATV"


def fmt_duration(seconds):
    if not seconds:
        return "??:??"
    m, s = divmod(int(seconds), 60)
    return f"{m}:{s:02d}"


def _piste(r, est_clip=False):
    artistes = ", ".join(a["name"] for a in r.get("artists", []))
    return {
        "videoId": r["videoId"],
        "title": r.get("title", "Inconnu"),
        "artists": artistes or "Inconnu",
        "duration": r.get("duration_seconds"),
        "clip": est_clip,
    }


def rechercher(yt, requete, limite=15):
    """
    Renvoie en priorité les vraies pistes audio (ATV).
    Si aucune n'existe pour cette recherche, fallback sur les clips (OMV).
    """
    resultats = yt.search(requete, filter="songs", limit=limite)

    songs, clips = [], []
    for r in resultats:
        if r.get("resultType") != "song" or not r.get("videoId"):
            continue
        vtype = r.get("videoType")
        if vtype == ATV or vtype is None:
            songs.append(_piste(r))
        else:
            clips.append(_piste(r, est_clip=True))

    # priorité aux vraies pistes ; clips seulement en secours
    return songs if songs else clips


def obtenir_url_audio(video_id):
    """Résout l'URL du meilleur flux audio. download=False -> rien sur disque."""
    url = f"https://music.youtube.com/watch?v={video_id}"
    opts = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        return info["url"], info.get("title", "")


def lecteur(stream_url, titre):
    """Lecteur VLC en streaming pur : buffer réseau en RAM, 0 cache disque."""
    instance = vlc.Instance("--network-caching=3000", "--file-caching=0")
    player = instance.media_player_new()
    media = instance.media_new(stream_url)
    player.set_media(media)
    player.play()

    print(f"\n▶  Lecture : {titre}")
    print("Commandes :  [p] pause/play   [s] stop   [+]/[-] volume   [q] quitter\n")

    volume = 80
    player.audio_set_volume(volume)

    while True:
        cmd = input("> ").strip().lower()
        if cmd == "p":
            if player.is_playing():
                player.pause()
                print("⏸  pause")
            else:
                player.play()
                print("▶  lecture")
        elif cmd == "s":
            player.stop()
            print("⏹  stop")
            break
        elif cmd == "+":
            volume = min(100, volume + 10)
            player.audio_set_volume(volume)
            print(f"🔊 volume {volume}")
        elif cmd == "-":
            volume = max(0, volume - 10)
            player.audio_set_volume(volume)
            print(f"🔉 volume {volume}")
        elif cmd == "q":
            player.stop()
            print("Au revoir.")
            sys.exit(0)
        else:
            print("Commande inconnue (p/s/+/-/q)")


def main():
    yt = YTMusic()  # pas d'auth nécessaire pour la recherche

    while True:
        requete = input("\nRecherche (ou 'q' pour quitter) : ").strip()
        if requete.lower() == "q":
            break
        if not requete:
            continue

        print("Recherche en cours...")
        pistes = rechercher(yt, requete)
        if not pistes:
            print("Aucun résultat.")
            continue

        for i, p in enumerate(pistes, 1):
            tag = "  [clip]" if p["clip"] else ""
            print(f"  {i:>2}. {p['title']} — {p['artists']}  "
                  f"[{fmt_duration(p['duration'])}]{tag}")

        choix = input("\nNuméro à écouter (Entrée pour relancer) : ").strip()
        if not choix.isdigit():
            continue
        idx = int(choix) - 1
        if not (0 <= idx < len(pistes)):
            print("Numéro invalide.")
            continue

        piste = pistes[idx]
        print("Résolution du flux audio...")
        try:
            stream_url, titre = obtenir_url_audio(piste["videoId"])
        except Exception as e:
            print(f"Erreur d'extraction : {e}")
            continue

        lecteur(stream_url, titre or f"{piste['title']} — {piste['artists']}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompu.")
