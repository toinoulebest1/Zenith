// party.js - Gestion du mode Social (Party Mode)

const Party = {
    channel: null,
    roomId: null,
    isRemoteUpdate: false, // Drapeau pour éviter les boucles infinies (Broadcast -> Receive -> Play -> Broadcast...)
    isConnected: false,

    // Initialisation : Vérifie si une room est passée en URL
    init: function() {
        const params = new URLSearchParams(window.location.search);
        const roomParam = params.get('party');
        if (roomParam) {
            // Nettoyage de l'URL pour éviter de re-joindre au refresh
            const newUrl = window.location.pathname;
            window.history.replaceState({}, document.title, newUrl);
            this.joinRoom(roomParam);
        }
    },

    // Créer une nouvelle salle
    createRoom: function() {
        // Génère un ID aléatoire court (ex: A4Z9)
        const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
        let id = '';
        for (let i = 0; i < 4; i++) {
            id += chars.charAt(Math.floor(Math.random() * chars.length));
        }
        this.joinRoom(id, true);
    },

    // Rejoindre une salle existante
    joinRoom: function(id, isCreator = false) {
        if (this.isConnected) this.leaveRoom();

        this.roomId = id.toUpperCase();
        console.log(`🎉 Joining Party Room: ${this.roomId}`);

        if (!supabaseClient) {
            showToast("Erreur: Supabase non initialisé");
            return;
        }

        // Création du canal
        this.channel = supabaseClient.channel(`party_${this.roomId}`, {
            config: {
                broadcast: { self: false } // Ne pas recevoir ses propres messages
            }
        });

        // Écoute des événements
        this.channel
            .on('broadcast', { event: 'playback' }, ({ payload }) => this.handlePlaybackEvent(payload))
            .on('broadcast', { event: 'sync_request' }, () => this.handleSyncRequest())
            .on('broadcast', { event: 'sync_response' }, ({ payload }) => this.handleSyncResponse(payload))
            .subscribe((status) => {
                if (status === 'SUBSCRIBED') {
                    this.isConnected = true;
                    this.updateUI(true);
                    showToast(`Connecté à la Room ${this.roomId} 🎵`);
                    
                    // Si on vient de rejoindre, on demande l'état actuel
                    if (!isCreator) {
                        this.send('sync_request', {});
                    }
                }
            });
    },

    // Quitter la salle
    leaveRoom: function() {
        if (this.channel) {
            supabaseClient.removeChannel(this.channel);
            this.channel = null;
        }
        this.isConnected = false;
        this.roomId = null;
        this.updateUI(false);
        showToast("Party Mode déconnecté 👋");
    },

    // Envoi d'un message (Broadcast)
    send: function(type, payload) {
        if (!this.isConnected || !this.channel || this.isRemoteUpdate) return;

        this.channel.send({
            type: 'broadcast',
            event: type,
            payload: payload
        }).catch(err => console.error("Broadcast error:", err));
    },

    // --- GESTION DES ÉVÉNEMENTS REÇUS ---

    handlePlaybackEvent: function(data) {
        console.log("📡 Received Event:", data.action, data);
        this.isRemoteUpdate = true; // On active le drapeau pour ne pas re-diffuser l'action

        try {
            switch (data.action) {
                case 'play':
                    // Si le temps est très différent, on se cale
                    if (activeAudio && Math.abs(activeAudio.currentTime - data.time) > 0.5) {
                        activeAudio.currentTime = data.time;
                    }
                    if (!isPlaying && typeof play === 'function') play();
                    break;

                case 'pause':
                    if (isPlaying && typeof pause === 'function') pause();
                    break;

                case 'seek':
                    if (activeAudio) activeAudio.currentTime = data.time;
                    break;

                case 'track':
                    // Vérifier si on n'est pas déjà sur ce titre
                    if (!tracks[currentIndex] || tracks[currentIndex].id !== data.track.id) {
                        // On essaie de trouver le titre dans la liste actuelle
                        let idx = tracks.findIndex(t => t.id === data.track.id);
                        
                        if (idx !== -1) {
                            // Titre trouvé localement
                            if (typeof loadTrack === 'function') loadTrack(idx);
                        } else {
                            // Titre non trouvé, on l'ajoute brutalement (Mode Radio/Search)
                            tracks.push(data.track);
                            if (typeof loadTrack === 'function') loadTrack(tracks.length - 1);
                        }
                    }
                    break;
            }
        } catch (e) {
            console.error("Party Error:", e);
        } finally {
            // Petit délai pour s'assurer que les événements locaux déclenchés par le script ne déclenchent pas de broadcast
            setTimeout(() => { this.isRemoteUpdate = false; }, 500);
        }
    },

    // Un nouvel utilisateur demande : "On écoute quoi ?"
    handleSyncRequest: function() {
        if (!isPlaying || !tracks[currentIndex]) return;
        
        console.log("📡 Sending Sync State...");
        this.send('sync_response', {
            track: tracks[currentIndex],
            isPlaying: isPlaying,
            time: activeAudio ? activeAudio.currentTime : 0,
            timestamp: Date.now() // Pour compenser la latence réseau éventuellement
        });
    },

    // Réception de l'état initial
    handleSyncResponse: function(data) {
        console.log("📡 Received Sync State");
        this.isRemoteUpdate = true;
        
        try {
            // 1. Charger la piste
            let idx = tracks.findIndex(t => t.id === data.track.id);
            if (idx === -1) {
                tracks.push(data.track);
                idx = tracks.length - 1;
            }
            
            // Si on n'est pas sur la bonne piste, on charge
            if (currentTrackId !== data.track.id) {
                if (typeof loadTrack === 'function') loadTrack(idx, false); // false = ne pas auto-play tout de suite
            }

            // 2. Caler le temps (avec compensation minime de latence)
            const latency = (Date.now() - data.timestamp) / 1000;
            const targetTime = data.time + (data.isPlaying ? latency : 0);
            
            if (activeAudio) {
                activeAudio.currentTime = targetTime;
                // 3. État lecture
                if (data.isPlaying) {
                    play();
                } else {
                    pause();
                }
            }

        } catch(e) { console.error(e); }
        
        setTimeout(() => { this.isRemoteUpdate = false; }, 1000);
    },

    // --- INTERFACE ---

    updateUI: function(connected) {
        const btn = document.getElementById('btnPartyMenu');
        const modalStatus = document.getElementById('partyStatusText');
        const modalCode = document.getElementById('partyCodeDisplay');
        const container = document.getElementById('partyActiveContainer');
        const setup = document.getElementById('partySetupContainer');

        if (connected) {
            if(btn) btn.classList.add('active-party');
            if(modalStatus) modalStatus.innerHTML = `Connecté à la Room <b style="color:var(--primary)">${this.roomId}</b>`;
            if(modalCode) modalCode.innerText = this.roomId;
            if(container) container.style.display = 'block';
            if(setup) setup.style.display = 'none';
        } else {
            if(btn) btn.classList.remove('active-party');
            if(modalStatus) modalStatus.innerText = "Non connecté";
            if(container) container.style.display = 'none';
            if(setup) setup.style.display = 'block';
        }
    },

    copyLink: function() {
        if(!this.roomId) return;
        const url = `${window.location.origin}/?party=${this.roomId}`;
        navigator.clipboard.writeText(url).then(() => showToast("Lien copié ! 🔗"));
    }
};

// Broadcast Helpers pour index.html
function broadcastPlay() {
    if(activeAudio) Party.send('playback', { action: 'play', time: activeAudio.currentTime });
}
function broadcastPause() {
    if(activeAudio) Party.send('playback', { action: 'pause', time: activeAudio.currentTime });
}
function broadcastSeek() {
    if(activeAudio) Party.send('playback', { action: 'seek', time: activeAudio.currentTime });
}
function broadcastTrackChange(track) {
    Party.send('playback', { action: 'track', track: track });
}

document.addEventListener('DOMContentLoaded', () => {
    Party.init();
});