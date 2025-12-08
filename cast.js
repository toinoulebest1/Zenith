/* FICHIER: cast.js 
   Gère l'intégration Google Cast (Chromecast) pour Zenith Ultimate
*/

const ZenithCast = {
    instance: null,
    session: null,
    isCasting: false,
    
    // ID par défaut du "Default Media Receiver" de Google. 
    // Permet de jouer de l'audio sans créer d'app spécifique sur la console Google.
    appId: 'CC1AD845', 

    init: function() {
        window['__onGCastApiAvailable'] = function(isAvailable) {
            if (isAvailable) {
                ZenithCast.initializeCastApi();
            }
        };
    },

    initializeCastApi: function() {
        cast.framework.CastContext.getInstance().setOptions({
            receiverApplicationId: this.appId,
            autoJoinPolicy: chrome.cast.AutoJoinPolicy.ORIGIN_SCOPED
        });

        this.instance = cast.framework.CastContext.getInstance();
        this.instance.addEventListener(
            cast.framework.CastContextEventType.SESSION_STATE_CHANGED,
            this.sessionStateChanged.bind(this)
        );
        
        console.log("📺 Cast System Ready");
    },

    sessionStateChanged: function(event) {
        switch (event.sessionState) {
            case cast.framework.SessionState.SESSION_STARTED:
            case cast.framework.SessionState.SESSION_RESUMED:
                this.session = event.session;
                this.isCasting = true;
                this.onCastStart();
                break;
            case cast.framework.SessionState.SESSION_ENDED:
                this.isCasting = false;
                this.session = null;
                this.onCastStop();
                break;
        }
    },

    // Appelé quand la connexion TV est établie
    onCastStart: function() {
        console.log("✅ Connected to Cast Device");
        document.getElementById('castBtnIcon').style.color = '#00d2ff'; // Allume l'icône
        // Si une musique est déjà en cours, on la lance sur la TV
        if (typeof currentIndex !== 'undefined' && tracks[currentIndex]) {
             this.loadMedia(tracks[currentIndex]);
        }
    },

    // Appelé quand on se déconnecte
    onCastStop: function() {
        console.log("❌ Disconnected from Cast");
        document.getElementById('castBtnIcon').style.color = '#888'; // Éteint l'icône
        // On pourrait reprendre la lecture locale ici si on voulait
    },

    // Fonction principale pour charger une musique sur la TV
    loadMedia: function(track) {
        if (!this.session) return;

        const audioUrl = track.source === 'subsonic' 
            ? `${window.location.origin}/stream_subsonic/${track.id}`
            : `${window.location.origin}/stream/${track.id}`;

        let imgUrl = 'https://placehold.co/300x300/1a1a1a/666666?text=Cast';
        if(track.source === 'subsonic') imgUrl = `${window.location.origin}/get_subsonic_cover/${track.album.image.large}`;
        else if(track.album && track.album.image) imgUrl = track.album.image.large.replace('_300','_600');

        const mediaInfo = new chrome.cast.media.MediaInfo(audioUrl, 'audio/mp3');
        
        // Métadonnées pour l'affichage sur la TV
        mediaInfo.metadata = new chrome.cast.media.MusicTrackMediaMetadata();
        mediaInfo.metadata.metadataType = chrome.cast.media.MetadataType.MUSIC_TRACK;
        mediaInfo.metadata.title = track.title;
        mediaInfo.metadata.artist = track.performer ? track.performer.name : 'Artiste Inconnu';
        mediaInfo.metadata.images = [new chrome.cast.Image(imgUrl)];

        const request = new chrome.cast.media.LoadRequest(mediaInfo);
        request.autoplay = true;

        this.session.loadMedia(request).then(
            function() { console.log('Load succeed'); },
            function(errorCode) { console.log('Error code: ' + errorCode); }
        );
    },

    // Ouvre le menu natif de Chrome pour choisir l'appareil
    triggerMenu: function() {
        if (this.instance) {
            this.instance.requestSession();
        }
    }
};

// Démarrage du script
ZenithCast.init();