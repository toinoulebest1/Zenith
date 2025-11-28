// party.js - Gestion du mode Social (Party Mode)

const Party = {
    channel: null,
    roomId: null,
    isRemoteUpdate: false, 
    isConnected: false,
    username: "Anonyme",
    unreadCount: 0,

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
            config: { broadcast: { self: false }, presence: { key: this.username } }
        });

        this.channel
            .on('broadcast', { event: 'playback' }, ({ payload }) => this.handlePlaybackEvent(payload))
            .on('broadcast', { event: 'sync_request' }, () => this.handleSyncRequest())
            .on('broadcast', { event: 'sync_response' }, ({ payload }) => this.handleSyncResponse(payload))
            .on('broadcast', { event: 'chat' }, ({ payload }) => this.handleChatEvent(payload))
            
            // GESTION PRÉSENCE (Départs)
            .on('presence', { event: 'leave' }, ({ leftPresences }) => {
                leftPresences.forEach(p => {
                    this.renderChatMessage({
                        user: 'Système',
                        text: `${p.user || 'Un utilisateur'} a quitté la party.`,
                        isSystem: true
                    });
                });
            })
            
            .subscribe((status) => {
                if (status === 'SUBSCRIBED') {
                    this.isConnected = true;
                    
                    // On s'enregistre dans la présence
                    this.channel.track({ user: this.username, online_at: new Date().toISOString() });
                    
                    this.updateUI(true);
                    this.openModal(); // Ouvre directement la modale quand on rejoint
                    
                    if (!isCreator) this.send('sync_request', {});
                    
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

        this.send('chat', payload);
        this.renderChatMessage(payload, true);
        input.value = '';
    },

    handleChatEvent: function(data) {
        this.renderChatMessage(data, false);
        
        // Gestion du compteur
        const modal = document.getElementById('partyModal');
        if (modal.style.display === 'none') {
            this.unreadCount++;
            this.updateBadge();
            showToast(`💬 ${data.user}: ${data.text}`);
        }
    },

    renderChatMessage: function(data, isMe) {
        const list = document.getElementById('partyChatList');
        if (!list) return;

        const div = document.createElement('div');
        div.className = `chat-message ${isMe ? 'me' : 'other'} ${data.isSystem ? 'system' : ''}`;

        let contextHTML = '';
        if (data.trackTitle) {
            contextHTML = `<div class="chat-context"><i class="fas fa-music"></i> ${data.trackTitle}</div>`;
        }

        div.innerHTML = `
            ${!isMe && !data.isSystem ? `<div class="chat-user">${data.user}</div>` : ''}
            ${contextHTML}
            <div class="chat-bubble">${data.text}</div>
        `;

        list.appendChild(div);
        list.scrollTop = list.scrollHeight;
    },

    // --- UI HELPERS ---

    openModal: function() {
        document.getElementById('partyModal').style.display = 'flex';
        this.unreadCount = 0;
        this.updateBadge();
    },

    closeModal: function() {
        document.getElementById('partyModal').style.display = 'none';
    },

    updateBadge: function() {
        const badge = document.getElementById('partyBadge');
        if (!badge) return;
        
        if (this.unreadCount > 0) {
            badge.style.display = 'flex';
            badge.innerText = this.unreadCount > 9 ? '9+' : this.unreadCount;
        } else {
            badge.style.display = 'none';
        }
    },

    updateUI: function(connected) {
        const btn = document.getElementById('btnPartyMenu');
        const setup = document.getElementById('partySetupContainer');
        const active = document.getElementById('partyActiveContainer');
        const code = document.getElementById('partyCodeDisplay');

        if (connected) {
            btn.classList.add('active-party');
            if(code) code.innerText = this.roomId;
            if(setup) setup.style.display = 'none';
            if(active) active.style.display = 'flex';
        } else {
            btn.classList.remove('active-party');
            if(setup) setup.style.display = 'block';
            if(active) active.style.display = 'none';
            const list = document.getElementById('partyChatList');
            if(list) list.innerHTML = '';
            this.unreadCount = 0;
            this.updateBadge();
        }
    },

    // --- AUDIO HANDLING ---

    // Nettoie les métadonnées pour éviter que le récepteur ne pense que c'est SA musique Spotify
    cleanTrack: function(track) {
        if (!track) return null;
        const clean = { ...track };
        // On supprime l'origine de l'import pour débloquer le like/dislike local
        delete clean.imported_from;
        delete clean.added_at;
        // On garde 'source' (ex: spotify_lazy) car le backend en a besoin pour résoudre l'URL
        return clean;
    },

    handlePlaybackEvent: function(data) {
        this.isRemoteUpdate = true;
        try {
            switch (data.action) {
                case 'play': if (activeAudio && Math.abs(activeAudio.currentTime - data.time) > 0.5) activeAudio.currentTime = data.time; if (!isPlaying && typeof play === 'function') play(); break;
                case 'pause': if (isPlaying && typeof pause === 'function') pause(); break;
                case 'seek': if (activeAudio) activeAudio.currentTime = data.time; break;
                case 'track':
                    const safeTrack = this.cleanTrack(data.track);
                    if (!tracks[currentIndex] || tracks[currentIndex].id !== safeTrack.id) {
                        let idx = tracks.findIndex(t => t.id === safeTrack.id);
                        if (idx !== -1) { 
                            if (typeof loadTrack === 'function') loadTrack(idx); 
                        } else { 
                            tracks.push(safeTrack); 
                            if (typeof loadTrack === 'function') loadTrack(tracks.length - 1); 
                        }
                    }
                    break;
            }
        } catch (e) { console.error(e); } 
        finally { setTimeout(() => { this.isRemoteUpdate = false; }, 500); }
    },

    handleSyncRequest: function() { 
        if (!isPlaying || !tracks[currentIndex]) return; 
        this.send('sync_response', { 
            track: tracks[currentIndex], // Le nettoyage se fera à la réception
            isPlaying: isPlaying, 
            time: activeAudio ? activeAudio.currentTime : 0, 
            timestamp: Date.now() 
        }); 
    },

    handleSyncResponse: function(data) { 
        this.isRemoteUpdate = true; 
        try { 
            const safeTrack = this.cleanTrack(data.track);
            let idx = tracks.findIndex(t => t.id === safeTrack.id); 
            if (idx === -1) { 
                tracks.push(safeTrack); 
                idx = tracks.length - 1; 
            } 
            
            if (currentTrackId !== safeTrack.id) { 
                if (typeof loadTrack === 'function') loadTrack(idx, false); 
            } 
            
            const latency = (Date.now() - data.timestamp) / 1000; 
            const targetTime = data.time + (data.isPlaying ? latency : 0); 
            
            if (activeAudio) { 
                activeAudio.currentTime = targetTime; 
                if (data.isPlaying) play(); else pause(); 
            } 
        } catch(e) {} 
        setTimeout(() => { this.isRemoteUpdate = false; }, 1000); 
    },

    copyLink: function() { if(!this.roomId) return; const url = `${window.location.origin}/?party=${this.roomId}`; navigator.clipboard.writeText(url).then(() => showToast("Lien copié ! 🔗")); }
};

function broadcastPlay() { if(activeAudio) Party.send('playback', { action: 'play', time: activeAudio.currentTime }); }
function broadcastPause() { if(activeAudio) Party.send('playback', { action: 'pause', time: activeAudio.currentTime }); }
function broadcastSeek() { if(activeAudio) Party.send('playback', { action: 'seek', time: activeAudio.currentTime }); }
function broadcastTrackChange(track) { Party.send('playback', { action: 'track', track: track }); }

document.addEventListener('DOMContentLoaded', () => { Party.init(); });