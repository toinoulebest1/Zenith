// party.js - Gestion du mode Social (Party Mode)

const Party = {
    channel: null,
    roomId: null,
    isRemoteUpdate: false, 
    isConnected: false,
    username: "Anonyme",

    // Initialisation
    init: function() {
        const params = new URLSearchParams(window.location.search);
        const roomParam = params.get('party');
        if (roomParam) {
            const newUrl = window.location.pathname;
            window.history.replaceState({}, document.title, newUrl);
            this.joinRoom(roomParam);
        }
        // Tentative de récupérer le pseudo
        setTimeout(() => {
            const storedName = document.getElementById('profileUsername').value;
            if(storedName) this.username = storedName;
        }, 1000);
    },

    createRoom: function() {
        const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
        let id = '';
        for (let i = 0; i < 4; i++) id += chars.charAt(Math.floor(Math.random() * chars.length));
        this.joinRoom(id, true);
    },

    joinRoom: function(id, isCreator = false) {
        if (this.isConnected) this.leaveRoom();

        this.roomId = id.toUpperCase();
        console.log(`🎉 Joining Party Room: ${this.roomId}`);

        if (!supabaseClient) {
            showToast("Erreur: Supabase non initialisé");
            return;
        }

        // Récupérer le pseudo actuel
        const inputName = document.getElementById('profileUsername').value;
        if(inputName) this.username = inputName;

        this.channel = supabaseClient.channel(`party_${this.roomId}`, {
            config: { broadcast: { self: false } }
        });

        this.channel
            .on('broadcast', { event: 'playback' }, ({ payload }) => this.handlePlaybackEvent(payload))
            .on('broadcast', { event: 'sync_request' }, () => this.handleSyncRequest())
            .on('broadcast', { event: 'sync_response' }, ({ payload }) => this.handleSyncResponse(payload))
            .on('broadcast', { event: 'chat' }, ({ payload }) => this.handleChatEvent(payload)) // NOUVEAU
            .subscribe((status) => {
                if (status === 'SUBSCRIBED') {
                    this.isConnected = true;
                    this.updateUI(true);
                    showToast(`Connecté à la Room ${this.roomId} 🎵`);
                    if (!isCreator) this.send('sync_request', {});
                    
                    // Message système local
                    this.renderChatMessage({
                        user: 'Système',
                        text: 'Bienvenue dans le chat !',
                        isSystem: true
                    });
                }
            });
    },

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

    send: function(type, payload) {
        if (!this.isConnected || !this.channel) return;
        // Pour le playback, on évite les boucles, mais pas pour le chat
        if (type === 'playback' && this.isRemoteUpdate) return;

        this.channel.send({
            type: 'broadcast',
            event: type,
            payload: payload
        }).catch(err => console.error("Broadcast error:", err));
    },

    // --- CHAT LOGIC ---

    sendChat: function() {
        const input = document.getElementById('partyChatInput');
        const text = input.value.trim();
        if (!text) return;

        const currentTrack = tracks[currentIndex];
        
        const payload = {
            user: this.username,
            text: text,
            trackTitle: currentTrack ? currentTrack.title : null,
            trackId: currentTrack ? currentTrack.id : null,
            timestamp: Date.now()
        };

        // 1. Envoyer aux autres
        this.send('chat', payload);

        // 2. Afficher pour soi-même
        this.renderChatMessage(payload, true);

        input.value = '';
    },

    handleChatEvent: function(data) {
        this.renderChatMessage(data, false);
        // Petit badge de notification si la modale est fermée ? (Optionnel)
        const btn = document.getElementById('btnPartyMenu');
        if (document.getElementById('partyModal').style.display === 'none') {
            showToast(`💬 ${data.user}: ${data.text}`);
            btn.classList.add('has-notif');
        }
    },

    renderChatMessage: function(data, isMe) {
        const list = document.getElementById('partyChatList');
        if (!list) return;

        const div = document.createElement('div');
        div.className = `chat-message ${isMe ? 'me' : 'other'} ${data.isSystem ? 'system' : ''}`;

        // Contexte Musical (Sur quelle musique le commentaire a été fait)
        let contextHTML = '';
        if (data.trackTitle) {
            // Si la musique a changé depuis, on affiche le contexte
            // Ou on l'affiche toujours pour être sûr
            contextHTML = `<div class="chat-context"><i class="fas fa-music"></i> ${data.trackTitle}</div>`;
        }

        div.innerHTML = `
            ${!isMe && !data.isSystem ? `<div class="chat-user">${data.user}</div>` : ''}
            ${contextHTML}
            <div class="chat-bubble">${data.text}</div>
        `;

        list.appendChild(div);
        list.scrollTop = list.scrollHeight; // Auto scroll vers le bas
    },

    // --- PLAYBACK EVENTS --- (Inchangé sauf nettoyage)

    handlePlaybackEvent: function(data) {
        console.log("📡 Received Event:", data.action);
        this.isRemoteUpdate = true;
        try {
            switch (data.action) {
                case 'play':
                    if (activeAudio && Math.abs(activeAudio.currentTime - data.time) > 0.5) activeAudio.currentTime = data.time;
                    if (!isPlaying && typeof play === 'function') play();
                    break;
                case 'pause':
                    if (isPlaying && typeof pause === 'function') pause();
                    break;
                case 'seek':
                    if (activeAudio) activeAudio.currentTime = data.time;
                    break;
                case 'track':
                    if (!tracks[currentIndex] || tracks[currentIndex].id !== data.track.id) {
                        let idx = tracks.findIndex(t => t.id === data.track.id);
                        if (idx !== -1) { if (typeof loadTrack === 'function') loadTrack(idx); } 
                        else { tracks.push(data.track); if (typeof loadTrack === 'function') loadTrack(tracks.length - 1); }
                    }
                    break;
            }
        } catch (e) { console.error(e); } 
        finally { setTimeout(() => { this.isRemoteUpdate = false; }, 500); }
    },

    handleSyncRequest: function() {
        if (!isPlaying || !tracks[currentIndex]) return;
        this.send('sync_response', {
            track: tracks[currentIndex],
            isPlaying: isPlaying,
            time: activeAudio ? activeAudio.currentTime : 0,
            timestamp: Date.now()
        });
    },

    handleSyncResponse: function(data) {
        this.isRemoteUpdate = true;
        try {
            let idx = tracks.findIndex(t => t.id === data.track.id);
            if (idx === -1) { tracks.push(data.track); idx = tracks.length - 1; }
            if (currentTrackId !== data.track.id) { if (typeof loadTrack === 'function') loadTrack(idx, false); }
            const latency = (Date.now() - data.timestamp) / 1000;
            const targetTime = data.time + (data.isPlaying ? latency : 0);
            if (activeAudio) { activeAudio.currentTime = targetTime; if (data.isPlaying) play(); else pause(); }
        } catch(e) { console.error(e); }
        setTimeout(() => { this.isRemoteUpdate = false; }, 1000);
    },

    // --- UI ---

    updateUI: function(connected) {
        const btn = document.getElementById('btnPartyMenu');
        const setup = document.getElementById('partySetupContainer');
        const active = document.getElementById('partyActiveContainer');
        const code = document.getElementById('partyCodeDisplay');

        if (connected) {
            btn.classList.add('active-party');
            if(code) code.innerText = this.roomId;
            if(setup) setup.style.display = 'none';
            if(active) active.style.display = 'flex'; // Flex pour layout colonne
        } else {
            btn.classList.remove('active-party');
            if(setup) setup.style.display = 'block';
            if(active) active.style.display = 'none';
            // Vider le chat à la déconnexion
            const list = document.getElementById('partyChatList');
            if(list) list.innerHTML = '';
        }
    },

    copyLink: function() {
        if(!this.roomId) return;
        const url = `${window.location.origin}/?party=${this.roomId}`;
        navigator.clipboard.writeText(url).then(() => showToast("Lien copié ! 🔗"));
    }
};

// Global Helpers
function broadcastPlay() { if(activeAudio) Party.send('playback', { action: 'play', time: activeAudio.currentTime }); }
function broadcastPause() { if(activeAudio) Party.send('playback', { action: 'pause', time: activeAudio.currentTime }); }
function broadcastSeek() { if(activeAudio) Party.send('playback', { action: 'seek', time: activeAudio.currentTime }); }
function broadcastTrackChange(track) { Party.send('playback', { action: 'track', track: track }); }

document.addEventListener('DOMContentLoaded', () => { Party.init(); });