// script.js

// Variables globales
let tracks = [];
let currentIndex = 0;
let audio = new Audio();
audio.crossOrigin = "anonymous"; // Important pour le streaming
let isPlaying = false;

// CONFIGURATION SERVEUR LOCAL
// C'est l'adresse du script Python server.py
const API_BASE = ''; // Laissez vide pour Vercel, ou 'http://127.0.0.1:5000' pour le local

// Éléments du DOM
const els = {
    grid: document.getElementById('trackGrid'),
    xlArt: document.getElementById('xlArt'),
    xlTitle: document.getElementById('xlTitle'),
    xlArtist: document.getElementById('xlArtist'),
    capArt: document.getElementById('capArt'),
    capTitle: document.getElementById('capTitle'),
    capArtist: document.getElementById('capArtist'),
    playIcon: document.getElementById('playIcon'),
    progressFill: document.getElementById('progressFill'),
    progressWrapper: document.querySelector('.progress-wrapper')
};

// --- 1. Initialisation & API ---

async function init() {
    try {
        // On appelle notre serveur Python local
        // Cette partie est maintenant gérée par goHome() et loadUserFavorites() après l'authentification
        // La page d'accueil affichera un message de bienvenue avant la recherche.
    } catch (e) {
        console.error(e);
        // els.grid.innerHTML = "<p style='padding:20px'>Erreur: Le serveur Python (server.py) n'est pas lancé !</p>";
    }
}

function processTracks(items) {
    tracks = items.map(item => ({
        id: item.id, // ID Important pour demander le stream
        title: item.title,
        artist: item.performer.name,
        album: item.album.title,
        img: (item.album.image.large || '').replace('_600', '_300'),
        hires: item.maximum_bit_depth > 16
    }));

    renderGrid();
}

// --- 2. Rendu Visuel ---

function renderGrid(items, q=false, emptyMsg="Aucun résultat.") {
    currentGridItems = items || [];
    const g=document.getElementById('trackGrid'); g.innerHTML=''; 
    if(!items||items.length===0){ g.innerHTML=`<div style="grid-column:1/-1;text-align:center;padding:50px;color:#555;"><i class="far fa-folder-open" style="font-size:30px;margin-bottom:10px;"></i><p>${emptyMsg}</p></div>`; return; } 
    items.forEach(it=>{ 
        const isAlb=it.type==='album'; const isPl=it.type==='playlist'; const src=it.source; 
        let mediaElement = '';
        if (isPl) { if (typeof getPlaylistCoverHTML === 'function' && src !== 'ytmusic') { mediaElement = getPlaylistCoverHTML(it); } else { let imgUrl = it.image || 'https://via.placeholder.com/300'; mediaElement = `<img src="${imgUrl}">`; } } else { let u = ''; if(src==='subsonic'){ u=`${API_BASE}/get_subsonic_cover/${isAlb?it.image.large:it.album.image.large}`; } else { u=(isAlb?it.image.large:it.album.image.large)||''; u=u.replace('_300','_600'); } if(!u)u='https://via.placeholder.com/300'; mediaElement = `<img src="${u}">`; } 
        const d=document.createElement('div'); d.className='track-card'; if(it.type==='artist') d.classList.add('artist-card'); d.dataset.id=it.id; 
        let b = ''; let rankBadgeHTML = ''; 
        if(isAlb) b='<div class="type-badge badge-album">ALBUM</div>'; else if(isPl) b='<div class="type-badge badge-hires" style="background:#ff0055">PLAYLIST</div>'; 
        if(src!=='subsonic' && src!=='ytmusic' && it.maximum_bit_depth>16 && !isPl) b+='<div class="type-badge badge-hires">HI-RES</div>'; 
        if(it.rank) { let rankClass = 'rank-other'; if(it.rank === 1) rankClass = 'rank-1'; else if(it.rank === 2) rankClass = 'rank-2'; else if(it.rank === 3) rankClass = 'rank-3'; rankBadgeHTML = `<div class="rank-badge ${rankClass}">#${it.rank}</div>`; }
        let subtitle = ''; if (isPl) { subtitle = it.performer ? it.performer.name : 'Playlist'; } else { subtitle = isAlb ? it.artist.name : it.performer.name; }
        let votesHTML = ''; if (it.like_count) { votesHTML = `<div class="vote-count"><i class="fas fa-heart"></i> ${it.like_count}</div>`; }
        d.innerHTML=`${rankBadgeHTML}<div class="badges-container">${b}</div>${mediaElement}<h3>${it.name||it.title}</h3><p>${subtitle}</p>${votesHTML}`; 
        d.onclick=()=>{ if(isAlb) openAlbum(it.id,src); else if(isPl) { if(src === 'ytmusic') openExternalPlaylist(it); else openPlaylist(it.id); } else { if(q){tracks=items; const idx=tracks.findIndex(x=>x.id===it.id); isRadioActive=false; document.getElementById('radioBadge').style.display='none'; loadTrack(idx!==-1?idx:0);} else{tracks=[it]; isRadioActive=false; document.getElementById('radioBadge').style.display='none'; loadTrack(0);} } }; 
        g.appendChild(d); 
    }); 
    updateActiveCard(); 
}

function updateUI(track) {
    els.xlArt.src = track.img;
    els.xlTitle.innerText = track.title;
    els.xlArtist.innerText = track.artist;

    els.capArt.src = track.img;
    els.capTitle.innerText = track.title;
    els.capArtist.innerText = track.artist;
}

// --- 3. Gestion du Player ---

const player = {
    load: (index) => {
        currentIndex = index;
        const t = tracks[index];
        
        // MAGIE : On demande au serveur Python l'URL de stream signée
        const streamUrl = `${API_BASE}/stream/${t.id}`;

        audio.src = streamUrl;
        updateUI(t);
        player.play();
        
        // Gestion des erreurs de lecture
        audio.onerror = () => {
            alert("Impossible de lire ce titre (Droits Qobuz ou Erreur Serveur).");
        };
    },

    play: () => {
        audio.play().catch(e => console.error("Erreur play:", e));
        isPlaying = true;
        els.playIcon.className = 'fas fa-pause';
        els.capArt.classList.add('playing');
    },

    pause: () => {
        audio.pause();
        isPlaying = false;
        els.playIcon.className = 'fas fa-play';
        els.capArt.classList.remove('playing');
    },

    toggle: () => {
        if (tracks.length === 0) return;
        isPlaying ? player.pause() : player.play();
    },

    next: () => {
        let nextIdx = (currentIndex + 1) % tracks.length;
        player.load(nextIdx);
    },

    prev: () => {
        let prevIdx = (currentIndex - 1 + tracks.length) % tracks.length;
        player.load(prevIdx);
    },

    seek: (e) => {
        if (!audio.duration) return;
        const width = els.progressWrapper.clientWidth;
        const clickX = e.offsetX;
        audio.currentTime = (clickX / width) * audio.duration;
    }
};

// --- 4. Événements Audio ---

audio.addEventListener('timeupdate', () => {
    if (audio.duration) {
        const pct = (audio.currentTime / audio.duration) * 100;
        els.progressFill.style.width = `${pct}%`;
    }
});

audio.addEventListener('ended', player.next);

// Lancer l'application
// init(); // L'initialisation est maintenant gérée par window.onload et initAuth

        // --- VARIABLES GLOBALES (déplacées ici pour clarté) ---
        let activeAudio = playerA; 
        let crossfadeDuration = 0; let globalVolume = 1; 
        let isFading = false; let nextTrackTriggered = false; 
        let isPlaying = false; let isRadioActive = false; let isLyricsMode = false;
        let lyricsData = []; let firstLineTime = null; let currentTrackId = null;
        let fadeInterval = null; let isFetchingRadio = false;

        // --- VARIABLES AUDIO WORKLET ---
        let audioCtx, zenithNode, sourceA, sourceB;
        let masterGain; // Pour le mode fallback
        let isOrbitOn = false, isKaraokeOn = false, isLimiterOn = false; // LIMITER OFF PAR DÉFAUT
        let eqFilters = []; const eqFrequencies = [60, 230, 910, 4000, 14000];
        let compressor; // Variable globale pour le compresseur

        let userSettings = { limiter: false, crossfade: 0, magic: true }; // LIMITER FALSE PAR DÉFAUT
        let magicEnabled = true; 
        
        let favorites = [];
        // L'historique sera maintenant géré par Supabase
        let history = []; 

        let navStack = [];

        function resetNavStack() { navStack = []; }
        function pushNavState() {
            navStack.push({
                gridItems: [...currentGridItems],
                title: document.getElementById('viewTitle').innerText,
                scroll: window.scrollY
            });
        }

        function goBack() {
            const artistV = document.getElementById('artistView');
            if (artistV.style.display === 'block') { artistV.style.display = 'none'; showTracks(); return; }
            const playlistV = document.getElementById('playlistView');
            if (playlistV.style.display === 'block') { playlistV.style.display = 'none'; if(document.getElementById('allPlaylistsView').style.display === 'none') { showTracks(); } else { document.getElementById('allPlaylistsView').style.display = 'block'; } return; }
            const allPlView = document.getElementById('allPlaylistsView');
            if (allPlView.style.display === 'block') { allPlView.style.display = 'none'; goHome(); return; }
            if (document.getElementById('trackView').style.display === 'block' && navStack.length === 0) { const searchVal = document.getElementById('searchInput').value; const isResultTitle = document.getElementById('viewTitle').innerText === "Résultats"; if (searchVal || isResultTitle) { document.getElementById('searchInput').value = ""; goHome(); return; } }
            if (navStack.length > 0) { const state = navStack.pop(); showTracks(); renderGrid(state.gridItems, false); document.getElementById('viewTitle').innerText = state.title; setTimeout(() => window.scrollTo(0, state.scroll), 0); safePushState({}, "", window.location.pathname); return; }
            if (window.history.length > 1) { window.history.back(); } else { goHome(); }
        }

        function applySettings(settings) {
            const isLimiter = (settings.limiter === true || settings.limiter === 'true'); isLimiterOn = isLimiter; document.getElementById('settingLimiter').checked = isLimiter; if (audioCtx) toggleLimiter(isLimiter, true);
            const xfade = parseInt(settings.crossfade || 0); crossfadeDuration = xfade; document.getElementById('settingCrossfade').value = xfade; document.getElementById('settingCrossfadeVal').innerText = xfade + "s"; document.querySelector('.xfade-range').value = xfade;
            const isMagic = (settings.magic === true || settings.magic === 'true'); magicEnabled = isMagic; document.getElementById('settingMagic').checked = isMagic; if(!magicEnabled) { document.body.className = ''; document.getElementById('wandContainer').classList.remove('active'); if(typeof stopEffects === 'function') stopEffects(); }
        }
        async function changeSetting(key, value) { userSettings[key] = value; applySettings(userSettings); localStorage.setItem('zenith_pref_' + key, value); if(!supabaseClient) return; const { data: { user } } = await supabaseClient.auth.getUser(); if(user) { try { await supabaseClient.from('profiles').update({ settings: userSettings }).eq('id', user.id); } catch(e) { console.error("Sync settings error", e); } } }
        function loadLocalSettings() { 
            const sLimiter = localStorage.getItem('zenith_pref_limiter'); 
            if(sLimiter !== null) userSettings.limiter = (sLimiter === 'true'); 
            else userSettings.limiter = false; // Valeur par défaut explicite

            const sXfade = localStorage.getItem('zenith_pref_crossfade'); if(sXfade !== null) userSettings.crossfade = parseInt(sXfade); 
            const sMagic = localStorage.getItem('zenith_pref_magic'); if(sMagic !== null) userSettings.magic = (sMagic === 'true'); 
            applySettings(userSettings); 
        }
        const originalLoadProfile = window.loadProfile; window.loadProfile = async function() { if(originalLoadProfile) await originalLoadProfile(); const { data: { user } } = await supabaseClient.auth.getUser(); if(!user) return; if (window.loadUserPlaylists) window.loadUserPlaylists(); if (window.loadUserHistory) await window.loadUserHistory(); const { data, error } = await supabaseClient.from('profiles').select('settings').eq('id', user.id).single(); if (data && data.settings) { userSettings = { ...userSettings, ...data.settings }; applySettings(userSettings); localStorage.setItem('zenith_pref_limiter', userSettings.limiter); localStorage.setItem('zenith_pref_crossfade', userSettings.crossfade); localStorage.setItem('zenith_pref_magic', userSettings.magic); } };
        function showProfileSettings() { document.getElementById('trackView').style.display = 'none'; document.getElementById('artistView').style.display = 'none'; document.getElementById('lyricsView').style.display = 'none'; document.getElementById('blindTestView').style.display = 'none'; document.getElementById('allPlaylistsView').style.display = 'none'; document.getElementById('playlistView').style.display = 'none'; document.getElementById('searchBarContainer').style.display = 'none'; document.getElementById('profileView').style.display = 'block'; window.loadProfile(); }
        window.loadUserFavorites = async function() { if(!supabaseClient) return; const { data: { user } } = await supabaseClient.auth.getUser(); if(!user) return; const { data, error } = await supabaseClient.from('favorites').select('*'); if(data) { favorites = data.map(f => f.track_data); updateLikeButtonState(); } window.loadProfile(); };
        function safePushState(data, title, url) { try { if (window.history && window.history.pushState) { window.history.pushState(data, title, url); } } catch (e) { console.warn("History pushState blocked", e); } }
        
        // --- MOTEUR AUDIO WORKLET (ANTI-COUPURE) ---
        async function initAudioContext() {
            if (audioCtx) return;
            try {
                const AudioContext = window.AudioContext || window.webkitAudioContext;
                sampleRate: 48000
                audioCtx = new AudioContext({ latencyHint: 'playback' });

                // --- FALLBACK HTTP (Si AudioWorklet non dispo) ---
                if (!audioCtx.audioWorklet) {
                    console.warn("⚠️ AudioWorklet non disponible (HTTP). Mode Fallback activé.");
                    showToast("AudioFX désactivés (HTTP)");

                    // Création Master Gain
                    masterGain = audioCtx.createGain();
                    masterGain.gain.value = globalVolume;

                    // Connexion directe
                    sourceA = audioCtx.createMediaElementSource(playerA);
                    sourceB = audioCtx.createMediaElementSource(playerB);
                    sourceA.connect(masterGain);
                    sourceB.connect(masterGain);
                    masterGain.connect(audioCtx.destination);
                    return;
                }
                
                // 2. Chargement Module
                await audioCtx.audioWorklet.addModule('zenith-processor.js');
                
                // 3. Création Nœud Zenith
                zenithNode = new AudioWorkletNode(audioCtx, 'zenith-processor', {
                    numberOfInputs: 2,
                    numberOfOutputs: 1,
                    parameterData: { volume: globalVolume }
                });

                // 4. Connexions Sources
                sourceA = audioCtx.createMediaElementSource(playerA);
                sourceB = audioCtx.createMediaElementSource(playerB);
                sourceA.connect(zenithNode, 0, 0);
                sourceB.connect(zenithNode, 0, 1);

                // 5. Chaîne d'effets (EQ + Compresseur)
                let prevNode = zenithNode;
                eqFilters = eqFrequencies.map((freq, i) => {
                    const filter = audioCtx.createBiquadFilter();
                    filter.type = i===0?'lowshelf':(i===4?'highshelf':'peaking');
                    filter.frequency.value = freq;
                    prevNode.connect(filter);
                    prevNode = filter;
                    return filter;
                });

                compressor = audioCtx.createDynamicsCompressor();
                
                // INITIALISATION CORRECTE DU COMPRESSEUR EN FONCTION DES RÉGLAGES
                if (isLimiterOn) {
                    compressor.threshold.value = -12;
                    compressor.knee.value = 30;
                    compressor.ratio.value = 12;
                    compressor.attack.value = 0.003; 
                    compressor.release.value = 0.25;
                } else {
                    // Transparent par défaut
                    compressor.threshold.value = 0;
                    compressor.ratio.value = 1; 
                }

                prevNode.connect(compressor);
                compressor.connect(audioCtx.destination);
                
                console.log("🚀 Moteur Zenith AudioWorklet activé");

            } catch (e) { console.error("Erreur AudioWorklet:", e); }
        }

        // Helpers Audio
        function setWorkletParam(name, val, ramp=0.1) {
            if(zenithNode) {
                const p = zenithNode.parameters.get(name);
                p.cancelScheduledValues(audioCtx.currentTime);
                p.linearRampToValueAtTime(val, audioCtx.currentTime + ramp);
            }
        }

        function toggleLimiter(forceState = null, silent = false) {
            if(forceState!==null) isLimiterOn = forceState; else isLimiterOn = !isLimiterOn;
            
            // Mise à jour visuelle
            document.getElementById('limiterBtn').classList.toggle('active', isLimiterOn);
            
            // Application Audio Réelle
            if (compressor && audioCtx) {
                const t = audioCtx.currentTime;
                if (isLimiterOn) {
                    compressor.threshold.setValueAtTime(-12, t);
                    compressor.ratio.setValueAtTime(12, t);
                } else {
                    // Retour à la transparence (Bypass virtuel)
                    compressor.threshold.setValueAtTime(0, t);
                    compressor.ratio.setValueAtTime(1, t);
                }
            }

            if(!silent) showToast(isLimiterOn ? "Normalisateur Activé" : "Désactivé (Hi-Fi)");
        }

        function toggleKaraoke() {
            if (!audioCtx) initAudioContext();
            if (!zenithNode) { showToast("Non disponible en HTTP"); return; }
            isKaraokeOn = !isKaraokeOn;
            document.getElementById('karaokeBtn').classList.toggle('active', isKaraokeOn);
            setWorkletParam('karaokeEnabled', isKaraokeOn ? 1 : 0);
            showToast(isKaraokeOn ? "No Vocal Activé" : "Normal");
        }

        function toggleOrbit() {
            if (!audioCtx) initAudioContext();
            if (!zenithNode) { showToast("Non disponible en HTTP"); return; }
            isOrbitOn = !isOrbitOn;
            document.getElementById('orbitBtn').classList.toggle('active', isOrbitOn);
            setWorkletParam('orbitEnabled', isOrbitOn ? 1 : 0);
            
            const orb = document.getElementById('orbitVisual');
            if (isOrbitOn) { orb.className = 'orbit-orb'; void orb.offsetWidth; orb.classList.add('center-stage', 'orbiting'); }
            else { orb.classList.remove('orbiting', 'center-stage'); }
            showToast(isOrbitOn ? "8D Orbit Activé" : "Désactivé");
        }

        function updateCrossfade(val) {
            crossfadeDuration = parseInt(val);
            document.getElementById('xfadeVal').innerText = val + "s";
            document.getElementById('settingCrossfadeVal').innerText = val + "s";
        }

        function setGlobalVolume(val) {
            globalVolume = parseFloat(val);
            if (zenithNode) setWorkletParam('volume', globalVolume, 0.05);
            else if (masterGain) masterGain.gain.setTargetAtTime(globalVolume, audioCtx.currentTime, 0.05);
        }
        
        function updateEQ(i,v) { if(audioCtx && eqFilters[i]) eqFilters[i].gain.value = parseFloat(v); }
        function resetEQ() { document.querySelectorAll('.eq-range').forEach((s, i) => { s.value=0; updateEQ(i, 0); }); }

        function resetLyricsState() { lyricsData = []; firstLineTime = null; const view = document.getElementById('lyricsView'); view.innerHTML = '<div style="margin-top: 100px; color: #888; animation: pulseText 1.5s infinite;">Recherche des paroles...</div>'; document.getElementById('introContainer').style.display = 'none'; }

        // CHARGEMENT PISTE
        async function loadTrack(index, autoPlay = true) {
            if(index < 0 || index >= tracks.length) return;
            
            if (typeof ZenithCast !== 'undefined' && ZenithCast.isCasting) {
                // Mode Cast (inchangé)
                currentIndex = index; const t = tracks[index]; currentTrackId = t.id;
                updateInterfaceInfo(t); updateActiveCard(); fetchLyrics(t, t.performer ? t.performer.name : 'Artiste');
                ZenithCast.loadMedia(t); pause(); isPlaying = true; document.getElementById('playIcon').className = 'fas fa-pause';
                updateLikeButtonState();
                if (index === tracks.length - 1) triggerRadio(true);
                return; 
            }

            // Initialisation Audio
            if (!audioCtx) await initAudioContext();
            if (audioCtx.state === 'suspended') await audioCtx.resume();

            nextTrackTriggered = false; 
            const nextTrack = tracks[index];
            
            // Logique A/B
            const nextAudio = (activeAudio === playerA) ? playerB : playerA; 
            const prevAudio = activeAudio;
            const targetCrossfade = (nextAudio === playerB) ? 1 : 0;

            if (nextTrack.isRadio) { document.getElementById('radioBadge').style.display = 'block'; isRadioActive = true; } else { document.getElementById('radioBadge').style.display = 'none'; isRadioActive = false; }
            if (typeof broadcastTrackChange === 'function') broadcastTrackChange(nextTrack);
            resetLyricsState();

            let url = '';
            if(nextTrack.source === 'subsonic') url = `${API_BASE}/stream_subsonic/${nextTrack.id}`;
            else if(nextTrack.source === 'yt_lazy' || nextTrack.source === 'spotify_lazy') {
                const artist = nextTrack.performer ? nextTrack.performer.name : 'Inconnu';
                url = `${API_BASE}/resolve_stream?title=${encodeURIComponent(nextTrack.title)}&artist=${encodeURIComponent(artist)}`;
            }
            else url = `${API_BASE}/stream/${nextTrack.id}`;

            nextAudio.src = url;
            nextAudio.volume = 1; // Worklet ou Gain gère le volume réel

            currentIndex = index; currentTrackId = nextTrack.id;
            updateInterfaceInfo(nextTrack);
            
            if(nextTrack.source !== 'yt_lazy' && nextTrack.source !== 'spotify_lazy') safePushState({}, "", `/?track=${nextTrack.id}&source=${nextTrack.source}`);
            
            // CROSSFADE VIA WORKLET
            if (autoPlay && isPlaying && crossfadeDuration > 0) {
                nextAudio.play().catch(e=>{});
                if (zenithNode) {
                    const param = zenithNode.parameters.get('crossfade');
                    param.cancelScheduledValues(audioCtx.currentTime);
                    param.setValueAtTime(param.value, audioCtx.currentTime);
                    param.linearRampToValueAtTime(targetCrossfade, audioCtx.currentTime + crossfadeDuration);
                }
                setTimeout(() => { prevAudio.pause(); prevAudio.currentTime = 0; }, crossfadeDuration * 1000);
            } else { 
                if (zenithNode) zenithNode.parameters.get('crossfade').setValueAtTime(targetCrossfade, audioCtx.currentTime);
                prevAudio.pause(); prevAudio.currentTime = 0; 
                if(autoPlay) nextAudio.play().catch(e=>{});
            }
            
            activeAudio = nextAudio; 
            isPlaying = true; 
            document.getElementById('playIcon').className = 'fas fa-pause';
            
            setupMediaSession(nextTrack); fetchLyrics(nextTrack, nextTrack.performer ? nextTrack.performer.name : 'Artiste'); updateActiveCard();
            updateLikeButtonState();
            // Appel à la nouvelle fonction d'ajout à l'historique Supabase
            setTimeout(() => { if(isPlaying && currentTrackId === nextTrack.id) saveTrackToHistory(nextTrack); }, 10000);
            
            if (index === tracks.length - 1 && !isFetchingRadio) { 
                setTimeout(() => { if(currentIndex === tracks.length -1) triggerRadio(true); }, 5000);
            }
        }

        function attachListeners(audioObj) {
            audioObj.onloadedmetadata = () => { 
                if(audioObj === activeAudio) { 
                    let d = audioObj.duration; const t = tracks[currentIndex];
                    if((!d || !isFinite(d)) && t && t.duration) d = t.duration;
                    document.getElementById('totTime').innerText = fmtTime(d); updateMediaSessionState(); 
                } 
            };
            audioObj.ontimeupdate = () => {
                if (isRadioActive && currentIndex === tracks.length - 1 && !isFetchingRadio) {
                     const rem = audioObj.duration - audioObj.currentTime;
                     if (rem < 20 && rem > 0) { triggerRadio(true); }
                }
                // Auto Next (Crossfade trigger)
                if (audioObj === activeAudio && isPlaying && crossfadeDuration > 0 && !nextTrackTriggered) {
                    let remaining = audioObj.duration - audioObj.currentTime;
                    if (remaining <= crossfadeDuration && remaining > 0.5) { nextTrackTriggered = true; handleNext(); return; }
                }
                if(audioObj === activeAudio && !document.hidden) {
                    let d = audioObj.duration; if((!d || !isFinite(d)) && tracks[currentIndex].duration) d = tracks[currentIndex].duration;
                    if(d && isFinite(d)) { document.getElementById('progressFill').style.width = (audioObj.currentTime/d)*100 + '%'; }
                    document.getElementById('currTime').innerText = fmtTime(audioObj.currentTime); syncLyricsUI(); updateMediaSessionState();
                }
            };
            audioObj.onended = () => { if(audioObj === activeAudio && !nextTrackTriggered) handleNext(); };
        }
        attachListeners(playerA); attachListeners(playerB);

        async function handleNext() { if (currentIndex < tracks.length - 1) { loadTrack(currentIndex + 1); } else { triggerRadio(); } }
        
        async function triggerRadio(isBackground = false) {
            if(isFetchingRadio) return; isFetchingRadio = true;
            if(!isBackground) { showToast("Recherche radio... 📻"); document.getElementById('radioBadge').style.display = 'block'; isRadioActive = true; }
            
            const cur = tracks[currentIndex]; 
            const artist = cur.performer ? cur.performer.name : cur.artist;
            let artistHistory = [];
            const startIndex = Math.max(0, currentIndex - 4);
            const recentTracks = tracks.slice(startIndex, currentIndex + 1);
            recentTracks.forEach(t => {
                 let a = 'Inconnu';
                 if(t.performer && t.performer.name) a = t.performer.name;
                 else if(t.artist && t.artist.name) a = t.artist.name;
                 artistHistory.push(a);
            });

            try {
                const historyStr = encodeURIComponent(artistHistory.join('|'));
                const res = await fetch(`${API_BASE}/recommend?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(cur.title)}&current_id=${cur.id}&recent_artists=${historyStr}`);
                const raw = await res.json();
                if(raw && raw.id) {
                    let hd = ''; if(raw.source === 'subsonic') hd = `${API_BASE}/get_subsonic_cover/${raw.album.image.large}`; else hd = (raw.album.image.large||'').replace('_300','_600');
                    if(!raw.performer) raw.performer = { name: raw.artist ? raw.artist.name : 'Artiste' };
                    
                    tracks.push({ ...raw, img: hd, type: 'track', isRadio: true });
                    const specificIndex = tracks.length - 1;
                    
                    const div = document.createElement('div'); div.className = 'track-card'; div.dataset.id = raw.id;
                    div.innerHTML = `<div class="badges-container"><div class="type-badge" style="background:var(--primary)">RADIO</div></div><img src="${hd}"><h3>${raw.title}</h3><p style="color:var(--primary)">${raw.performer.name}</p>`;
                    div.onclick = () => { loadTrack(specificIndex); }; 
                    
                    document.getElementById('trackGrid').appendChild(div);
                    if (!isBackground) { 
                        loadTrack(tracks.length - 1); 
                        setTimeout(() => div.scrollIntoView({behavior: "smooth", block: "center"}), 100); 
                    }
                }
            } catch(e) { 
                console.error(e);
                if(!isBackground && currentIndex < tracks.length-1) loadTrack(currentIndex+1); 
            } finally { isFetchingRadio = false; }
        }

        function updateInterfaceInfo(t) {
            let img = ''; if(t.source === 'subsonic') img = `${API_BASE}/get_subsonic_cover/${t.album.image.large}`; else img = (t.album.image.large||'').replace('_300','_600');
            let artistName = t.performer ? t.performer.name : 'Artiste';
            const xlArt = document.getElementById('xlArt'); const capArt = document.getElementById('capArt'); const amb = document.getElementById('ambientBg');
            const textEls = [ 'xlTitle', 'xlArtist', 'xlBadge', 'capTitle', 'capArtist' ].map(id=>document.getElementById(id));
            textEls.forEach(el => el.classList.add('fade-out-active'));
            setTimeout(() => {
                document.getElementById('xlTitle').innerText = t.title; document.getElementById('xlArtist').innerText = artistName; document.getElementById('capTitle').innerText = t.title; document.getElementById('capArtist').innerText = artistName;
                const badge = document.getElementById('xlBadge'); badge.style.display = (t.source !== 'subsonic' && t.maximum_bit_depth > 16) ? 'block' : 'none';
                if(magicEnabled) checkMagic(t.title, artistName);
                textEls.forEach(el => el.classList.remove('fade-out-active'));
            }, 300);
            const currentSrc = xlArt.getAttribute('src') || ''; 
            if (currentSrc !== img) {
                xlArt.classList.add('fade-out-active'); capArt.classList.add('fade-out-active');
                const tempImg = new Image(); tempImg.onload = () => { xlArt.src = img; capArt.src = img; amb.style.backgroundImage = `url('${img}')`; amb.style.opacity = '1'; xlArt.classList.remove('fade-out-active'); capArt.classList.remove('fade-out-active'); }; tempImg.src = img;
                setTimeout(() => { xlArt.classList.remove('fade-out-active'); capArt.classList.remove('fade-out-active'); if(amb.style.opacity != '1') amb.style.opacity = '1'; }, 1000);
            } else { if(amb.style.opacity != '1') { amb.style.backgroundImage = `url('${img}')`; amb.style.opacity = '1'; } }
        }

        async function openArtistProfile(name) { safePushState({}, "", `/?artist_profile=${encodeURIComponent(name)}`); document.getElementById('trackView').style.display='none'; document.getElementById('lyricsView').style.display='none'; const view = document.getElementById('artistView'); view.style.display='block'; document.getElementById('artistProfileName').innerText = name; document.getElementById('artistProfileBio').innerText = "Chargement..."; document.getElementById('artistTopTracks').innerHTML = '<p style="text-align:center;">Chargement...</p>'; document.getElementById('artistProfileImg').src = 'https://via.placeholder.com/200'; document.getElementById('artistHeaderBg').src = ''; document.getElementById('artistFans').style.display = 'none'; try { const res = await fetch(`${API_BASE}/artist_bio?name=${encodeURIComponent(name)}`); const data = await res.json(); if(data.bio) document.getElementById('artistProfileBio').innerText = data.bio; if(data.image) { document.getElementById('artistProfileImg').src = data.image; document.getElementById('artistHeaderBg').src = data.image; } if(data.nb_fans) { let formatted = data.nb_fans.toLocaleString('fr-FR'); document.getElementById('artistFans').innerHTML = `<i class="fas fa-users"></i> ${formatted} Fans`; document.getElementById('artistFans').style.display = 'inline-flex'; } else { document.getElementById('artistFans').style.display = 'none'; } const grid = document.getElementById('artistTopTracks'); grid.innerHTML = ''; if(data.top_tracks) { data.top_tracks.forEach(t => { const div = document.createElement('div'); div.className = 'track-card'; div.dataset.id = t.id; let b = ''; if(t.source !== 'subsonic' && t.maximum_bit_depth > 16) { b = '<div class="type-badge badge-hires">HI-RES</div>'; } div.innerHTML = `<div class="badges-container">${b}</div><img src="${t.image || 'https://via.placeholder.com/300'}"><h3>${t.title}</h3><p>${t.artist}</p>`; div.onclick = () => { tracks = data.top_tracks.map(x => ({ id: x.id, title: x.title, performer: { name: x.artist }, album: { title: 'Top Tracks', image: { large: x.image } }, source: x.source, maximum_bit_depth: x.maximum_bit_depth, duration: x.duration })); const idx = tracks.findIndex(tr => tr.id === t.id); loadTrack(idx !== -1 ? idx : 0); }; grid.appendChild(div); }); updateActiveCard(); } } catch(e) { console.log(e); } }
        function toggleEQ() { const v=document.getElementById('eqView'); if(!audioCtx)initAudioContext(); if(v.style.display==='flex'){v.classList.remove('active');setTimeout(()=>v.style.display='none',300);}else{v.style.display='flex';setTimeout(()=>v.classList.add('active'),10);} }
        function play() { if(audioCtx && audioCtx.state === 'suspended') audioCtx.resume(); activeAudio.play().then(() => { isPlaying = true; document.getElementById('playIcon').className = 'fas fa-pause'; updateMediaSessionState(); if('mediaSession' in navigator) navigator.mediaSession.playbackState = "playing"; if (typeof broadcastPlay === 'function') broadcastPlay(); }).catch(e => {}); }
        function pause() { playerA.pause(); playerB.pause(); isPlaying = false; document.getElementById('playIcon').className = 'fas fa-play'; if('mediaSession' in navigator) navigator.mediaSession.playbackState = "paused"; if (typeof broadcastPause === 'function') broadcastPause(); }
        function fmtTime(s) { if(!s || isNaN(s)) return "0:00"; let m=Math.floor(s/60), sec=Math.floor(s%60); return m+":"+(sec<10?"0":"")+sec; }
        function showToast(msg) { const t=document.getElementById('toast'); t.innerText=msg; t.classList.add('show'); setTimeout(()=>t.classList.remove('show'),2000); }
        function copyLink() { if(!currentTrackId) return; const t=tracks[currentIndex]; navigator.clipboard.writeText(`${window.location.origin}/?track=${t.id}&source=${t.source}`).then(()=>showToast('Lien copié !')); }
        async function toggleLike() { const btn = document.getElementById('btnLike'); if(btn.disabled) return; btn.disabled = true; try { const t = tracks[currentIndex]; if(!t) return; const { data: { user } } = await supabaseClient.auth.getUser(); if(!user) { showToast("Connectez-vous pour liker !"); return; } const idx = favorites.findIndex(f => String(f.id) === String(t.id)); if (idx === -1) { favorites.push(t); btn.innerHTML = '<i class="fas fa-heart"></i>'; btn.style.color = 'var(--secondary)'; showToast('Liké ❤️'); try { await supabaseClient.from('favorites').insert({ user_id: user.id, track_id: String(t.id), track_data: t }); } catch(e) { console.error(e); } } else { favorites.splice(idx, 1); btn.innerHTML = '<i class="far fa-heart"></i>'; btn.style.color = 'var(--text-dim)'; showToast('Disliké 💔'); try { await supabaseClient.from('favorites').delete().match({ user_id: user.id, track_id: String(t.id) }); } catch(e) { console.error(e); } } } finally { btn.disabled = false; } }
        function updateLikeButtonState() { const t = tracks[currentIndex]; if(!t) return; const idx = favorites.findIndex(f => String(f.id) === String(t.id)); const btn = document.getElementById('btnLike'); if(idx !== -1) { btn.innerHTML = '<i class="fas fa-heart"></i>'; btn.style.color = 'var(--secondary)'; } else { btn.innerHTML = '<i class="far fa-heart"></i>'; btn.style.color = 'var(--text-dim)'; } }
        
        // Nouvelle fonction pour ajouter un titre à l'historique Supabase
        async function saveTrackToHistory(track) {
            if (!supabaseClient) {
                console.error("saveTrackToHistory: Supabase client non initialisé.");
                return;
            }
            const { data: { user } } = await supabaseClient.auth.getUser();
            if (!user) {
                console.warn("saveTrackToHistory: Utilisateur non connecté, historique non sauvegardé.");
                return;
            }

            console.log(`saveTrackToHistory: Tentative de sauvegarde pour l'utilisateur ${user.id}, titre ${track.id}`);

            try {
                // 1. Vérifier si le titre existe déjà dans l'historique de l'utilisateur
                const { data: existingHistory, error: fetchError } = await supabaseClient
                    .from('history')
                    .select('id')
                    .eq('user_id', user.id)
                    .eq('track_id', String(track.id));

                if (fetchError) {
                    console.error("saveTrackToHistory: Erreur lors de la vérification de l'historique existant:", fetchError);
                    throw fetchError;
                }

                if (existingHistory && existingHistory.length > 0) {
                    console.log(`saveTrackToHistory: Titre ${track.id} déjà dans l'historique, mise à jour du timestamp.`);
                    // Si le titre existe, le supprimer pour le réinsérer avec un nouveau timestamp
                    const { error: deleteError } = await supabaseClient
                        .from('history')
                        .delete()
                        .eq('id', existingHistory[0].id);

                    if (deleteError) {
                        console.error("saveTrackToHistory: Erreur lors de la suppression de l'ancien historique:", deleteError);
                        throw deleteError;
                    }
                    console.log(`saveTrackToHistory: Ancien historique pour ${track.id} supprimé.`);
                }

                // 2. Insérer le nouveau titre (ou le titre mis à jour)
                const { error: insertError } = await supabaseClient
                    .from('history')
                    .insert({
                        user_id: user.id,
                        track_id: String(track.id),
                        track_data: track,
                        played_at: new Date().toISOString()
                    });

                if (insertError) {
                    console.error("saveTrackToHistory: Erreur lors de l'insertion du nouveau titre dans l'historique:", insertError);
                    throw insertError;
                }
                console.log(`saveTrackToHistory: Titre ${track.id} inséré/mis à jour dans l'historique.`);

                // 3. Maintenir l'historique à 50 titres maximum
                const { data: currentHistory, error: historyError } = await supabaseClient
                    .from('history')
                    .select('id')
                    .eq('user_id', user.id)
                    .order('played_at', { ascending: false });

                if (historyError) {
                    console.error("saveTrackToHistory: Erreur lors de la récupération de l'historique pour la purge:", historyError);
                    throw historyError;
                }

                if (currentHistory && currentHistory.length > 50) {
                    const idsToDelete = currentHistory.slice(50).map(item => item.id);
                    console.log(`saveTrackToHistory: Purge de ${idsToDelete.length} anciens titres de l'historique.`);
                    const { error: purgeError } = await supabaseClient
                        .from('history')
                        .delete()
                        .in('id', idsToDelete);
                    
                    if (purgeError) {
                        console.error("saveTrackToHistory: Erreur lors de la purge de l'historique:", purgeError);
                        throw purgeError;
                    }
                    console.log("saveTrackToHistory: Purge de l'historique terminée.");
                }
                
                // Recharger l'historique local après modification
                await loadUserHistory();
                console.log("saveTrackToHistory: Historique rechargé localement.");

            } catch (e) {
                console.error("Erreur globale lors de la sauvegarde de l'historique:", e);
                showToast("Erreur sauvegarde historique."); // Inform user
            }
        }

        // Nouvelle fonction pour charger l'historique depuis Supabase
        window.loadUserHistory = async function() {
            if (!supabaseClient) {
                console.error("loadUserHistory: Supabase client non initialisé.");
                history = []; // Clear local history if not logged in
                return;
            }
            const { data: { user } } = await supabaseClient.auth.getUser();
            if (!user) {
                console.warn("loadUserHistory: Utilisateur non connecté, historique non chargé.");
                history = []; // Clear local history if not logged in
                return;
            }

            console.log(`loadUserHistory: Chargement de l'historique pour l'utilisateur ${user.id}.`);

            try {
                const { data, error } = await supabaseClient
                    .from('history')
                    .select('track_data')
                    .eq('user_id', user.id)
                    .order('played_at', { ascending: false })
                    .limit(50);

                if (error) {
                    console.error("loadUserHistory: Erreur lors du chargement de l'historique:", error);
                    throw error;
                }

                if (data) {
                    history = data.map(item => item.track_data);
                    console.log(`loadUserHistory: ${history.length} titres chargés.`);
                } else {
                    history = [];
                    console.log("loadUserHistory: Aucun historique trouvé.");
                }
            } catch (e) {
                console.error("Erreur globale lors du chargement de l'historique:", e);
                history = [];
            }
        };

        function cleanString(s) { return s ? s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase().replace(/[^a-z0-9]/gi,'').trim() : ""; }
        function updateActiveCard() { document.querySelectorAll('.track-card').forEach(el => { el.classList.remove('active-track'); if(el.querySelector('.playing-indicator')) el.querySelector('.playing-indicator').remove(); }); if(!currentTrackId) return; const activeCards = document.querySelectorAll(`.track-card[data-id="${currentTrackId}"]`); activeCards.forEach(active => { active.classList.add('active-track'); const ind = document.createElement('div'); ind.className = 'playing-indicator'; ind.innerHTML = '<i class="fas fa-volume-up"></i>'; active.appendChild(ind); }); }
        function goHome(p=true){ if(p) safePushState({},"","/"); resetNavStack(); showTracks(); document.getElementById('trackGrid').innerHTML=`<div style="grid-column: 1/-1; text-align: center; padding: 60px 20px; color: #ccc;"><h2 style="font-size: 2rem; color: var(--primary); margin-bottom: 20px; text-shadow: 0 0 30px rgba(0, 210, 255, 0.3);">Bienvenue sur Zenith Ultimate</h2><p style="font-size: 1.1rem; line-height: 1.6; max-width: 600px; margin: 0 auto; color: #aaa;">L'expérience audio haute résolution ultime. Profitez d'une qualité sonore cristalline, d'effets spatiaux immersifs et d'une bibliothèque musicale infinie.</p><div style="margin-top: 40px; display: flex; flex-wrap: wrap; justify-content: center; gap: 30px; font-size: 0.9rem;"><div style="display:flex; flex-direction:column; align-items:center; gap:10px;"><div style="width:50px; height:50px; background:rgba(255,255,255,0.05); border-radius:50%; display:flex; align-items:center; justify-content:center;"><i class="fas fa-wave-square" style="color:var(--primary); font-size:20px;"></i></div><span>Audio Hi-Res</span></div><div style="display:flex; flex-direction:column; align-items:center; gap:10px;"><div style="width:50px; height:50px; background:rgba(255,255,255,0.05); border-radius:50%; display:flex; align-items:center; justify-content:center;"><i class="fas fa-globe-americas" style="color:var(--primary); font-size:20px;"></i></div><span>Audio Spatial 8D</span></div><div style="display:flex; flex-direction:column; align-items:center; gap:10px;"><div style="width:50px; height:50px; background:rgba(255,255,255,0.05); border-radius:50%; display:flex; align-items:center; justify-content:center;"><i class="fas fa-microphone-alt" style="color:var(--primary); font-size:20px;"></i></div><span>Paroles Synchro</span></div><div style="display:flex; flex-direction:column; align-items:center; gap:10px;"><div style="width:50px; height:50px; background:rgba(255,255,255,0.05); border-radius:50%; display:flex; align-items:center; justify-content:center;"><i class="fas fa-magic" style="color:var(--primary); font-size:20px;"></i></div><span>Thèmes Magiques</span></div></div><div style="margin-top: 50px;"><p style="font-size: 14px; opacity: 0.6;">Utilisez la barre de recherche pour commencer votre voyage sonore.</p></div></div>`; }
        async function showWeeklyTop() { resetNavStack(); showTracks(); document.getElementById('searchBarContainer').style.display='none'; document.getElementById('viewTitle').innerText="Top 10 Semaine 🏆"; document.getElementById('trackGrid').innerHTML='<p style="text-align:center;margin-top:50px;">Calcul du classement...</p>'; try { const { data, error } = await supabaseClient.rpc('get_weekly_top_tracks'); if(error) throw error; if (data && data.length > 0) { const topTracks = data.map((item, index) => { return { ...item.track_data, rank: index + 1 }; }); renderGrid(topTracks, true); } else { document.getElementById('trackGrid').innerHTML=`<div style="grid-column:1/-1;text-align:center;padding:50px;color:#555;"><i class="fas fa-trophy" style="font-size:30px;margin-bottom:10px;"></i><p>Aucun like cette semaine.<br>Soyez le premier à lancer la tendance !</p></div>`; } } catch(e) { console.error("Top 10 Error", e); showToast("Erreur récupération Top 10"); } }
        window.onpopstate = async (e) => { const p=new URLSearchParams(window.location.search); if(p.get('artist_profile')) await openArtistProfile(p.get('artist_profile')); else if(p.get('artist')) await openArtist(p.get('artist')); else if(p.get('album')) await openAlbum(p.get('album'),p.get('source')); else if(p.get('q')) {document.getElementById('searchInput').value=p.get('q'); await performSearch();} else goHome(false); };
        window.onload = async () => { 
            loadLocalSettings(); 
            const p=new URLSearchParams(window.location.search); 
            if(p.get('track')) await openTrackDirect(p.get('track'),p.get('source')); 
            else if(p.get('artist')) openArtist(p.get('artist')); 
            else if(p.get('album')) openAlbum(p.get('album'),p.get('source')); 
            else goHome(); 
            
            // L'appel à initAuth a été déplacé dans auth.js sur DOMContentLoaded
            // await initAuth(); 
        };
        async function openTrackDirect(id,s){ try{const r=await fetch(`${API_BASE}/track?id=${id}&source=${s||''}`); const d=await r.json(); if(d.id){ let i=d.source==='subsonic'?`${API_BASE}/get_subsonic_cover/${d.album.image.large}`:(d.album.image.large||'').replace('_300','_600'); tracks=[{...d,img:i,performer:d.performer||{name:d.artist?d.artist.name:"Artiste"},type:'track'}]; renderGrid(tracks,false); loadTrack(0, false); }}catch(e){}}
        function showTracks(){ document.getElementById('trackView').style.display='block'; document.getElementById('artistView').style.display='none'; document.getElementById('lyricsView').style.display='none'; document.getElementById('blindTestView').style.display='none'; document.getElementById('profileView').style.display='none'; document.getElementById('playlistView').style.display='none'; document.getElementById('allPlaylistsView').style.display='none'; document.getElementById('searchBarContainer').style.display='flex'; isLyricsMode=false; document.getElementById('introContainer').style.display='none'; document.getElementById('viewTitle').innerText="Résultats"; }
        function showFavorites(){ resetNavStack(); showTracks(); document.getElementById('searchBarContainer').style.display='none'; renderGrid([...favorites], true, "Vous n'avez pas encore de favoris.<br>Likez des titres pour les retrouver ici !"); document.getElementById('viewTitle').innerText="Mes Favoris ❤️"; }
        // Mise à jour de showHistory pour utiliser l'historique Supabase
        function showHistory(){
            resetNavStack();
            showTracks();
            document.getElementById('searchBarContainer').style.display='none';
            renderGrid([...history], true, "Votre historique est vide.<br>Écoutez des titres pour les retrouver ici !<br>Nous gardons vos 50 dernières écoutes.");
            document.getElementById('viewTitle').innerText="Historique des 50 dernières chansons 🕒";
        }
        function showPlaylists(){ resetNavStack(); showTracks(); document.getElementById('trackView').style.display='none'; document.getElementById('allPlaylistsView').style.display='block'; document.getElementById('searchBarContainer').style.display='none'; renderAllPlaylists(); }
        function toggleLyrics(){ isLyricsMode=!isLyricsMode; if(isLyricsMode){ document.getElementById('trackView').style.display='none'; document.getElementById('artistView').style.display='none'; document.getElementById('blindTestView').style.display='none'; document.getElementById('profileView').style.display='none'; document.getElementById('lyricsView').style.display='block'; renderSyncedLyrics(); }else showTracks(); }
        
        async function performSearch(){ 
            resetNavStack(); 
            const q=document.getElementById('searchInput').value; 
            const t=document.getElementById('searchType').value; 
            if(!q)return; 
            safePushState({},"",`/?q=${encodeURIComponent(q)}`); 
            showTracks(); 
            document.getElementById('trackGrid').innerHTML='<p style="text-align:center;margin-top:50px;">Chargement...</p>'; 
            
            let results = [];
            if (t === 'all' || t === 'playlist') {
                const playlists = await searchPublicPlaylists(q);
                results = results.concat(playlists);
            }
            try {
                const r=await fetch(`${API_BASE}/search?q=${encodeURIComponent(q)}&type=${t}`); 
                const d=await r.json(); 
                if(d.albums) d.albums.forEach(a=>results.push({...a,type:'album'})); 
                if(d.tracks) d.tracks.forEach(x=>results.push({...x,type:'track'})); 
                if(d.external_playlists) d.external_playlists.forEach(p=>results.push(p));
            } catch(e) { console.error(e); }
            renderGrid(results, false);
        }

        function openExternalPlaylist(playlist) {
            pushNavState();
            document.getElementById('trackView').style.display = 'none';
            document.getElementById('artistView').style.display = 'none';
            document.getElementById('lyricsView').style.display = 'none';
            document.getElementById('blindTestView').style.display = 'none';
            document.getElementById('profileView').style.display = 'none';
            document.getElementById('allPlaylistsView').style.display = 'none';
            document.getElementById('searchBarContainer').style.display = 'none';
            
            const view = document.getElementById('playlistView');
            view.style.display = 'block';
            
            document.getElementById('playlistTitle').innerHTML = `
                <div style="display:flex; align-items:center; gap:10px;">
                    <span>${playlist.name}</span>
                    <span style="font-size:10px; background:rgba(255,255,255,0.1); padding:2px 6px; border-radius:4px;">PLAYLIST</span>
                </div>`;
            const g = document.getElementById('playlistGrid');
            g.innerHTML = `
                <div style="grid-column:1/-1; text-align:center; padding:50px;">
                    <i class="fas fa-sync fa-spin" style="font-size:30px; color:var(--primary); margin-bottom:20px;"></i>
                    <p>Chargement de la playlist...</p>
                </div>`;
            
            fetch(`${API_BASE}/yt_playlist?id=${playlist.id}`)
                .then(r => r.json())
                .then(details => {
                    if(!details.tracks || details.tracks.length === 0) { g.innerHTML = '<p style="padding:20px;">Vide.</p>'; return; }
                    g.innerHTML = ''; 
                    details.tracks.forEach((it) => {
                        const displayImg = it.img || it.album.image.large || 'https://via.placeholder.com/300'; 
                        const div = document.createElement('div'); div.className = 'track-card'; div.dataset.id = it.id;
                        div.innerHTML = `<div class="badges-container"></div><img src="${displayImg}"><h3>${it.title}</h3><p>${it.performer.name}</p>`;
                        div.onclick = () => { tracks = details.tracks.map(t => ({ ...t, img: t.img || t.album.image.large, type: 'track' })); const idx = tracks.findIndex(x => x.id === it.id); loadTrack(idx !== -1 ? idx : 0); };
                        g.appendChild(div);
                    });
                    updateActiveCard();
                })
                .catch(err => { console.error(err); g.innerHTML = '<p style="text-align:center; padding:20px; color:red;">Erreur de chargement.</p>'; });
        }

        function renderGrid(items, q=false, emptyMsg="Aucun résultat.") {
            currentGridItems = items || [];
            const g=document.getElementById('trackGrid'); g.innerHTML=''; 
            if(!items||items.length===0){ g.innerHTML=`<div style="grid-column:1/-1;text-align:center;padding:50px;color:#555;"><i class="far fa-folder-open" style="font-size:30px;margin-bottom:10px;"></i><p>${emptyMsg}</p></div>`; return; } 
            items.forEach(it=>{ 
                const isAlb=it.type==='album'; const isPl=it.type==='playlist'; const src=it.source; 
                let mediaElement = '';
                if (isPl) { if (typeof getPlaylistCoverHTML === 'function' && src !== 'ytmusic') { mediaElement = getPlaylistCoverHTML(it); } else { let imgUrl = it.image || 'https://via.placeholder.com/300'; mediaElement = `<img src="${imgUrl}">`; } } else { let u = ''; if(src==='subsonic'){ u=`${API_BASE}/get_subsonic_cover/${isAlb?it.image.large:it.album.image.large}`; } else { u=(isAlb?it.image.large:it.album.image.large)||''; u=u.replace('_300','_600'); } if(!u)u='https://via.placeholder.com/300'; mediaElement = `<img src="${u}">`; } 
                const d=document.createElement('div'); d.className='track-card'; if(it.type==='artist') d.classList.add('artist-card'); d.dataset.id=it.id; 
                let b = ''; let rankBadgeHTML = ''; 
                if(isAlb) b='<div class="type-badge badge-album">ALBUM</div>'; else if(isPl) b='<div class="type-badge badge-hires" style="background:#ff0055">PLAYLIST</div>'; 
                if(src!=='subsonic' && src!=='ytmusic' && it.maximum_bit_depth>16 && !isPl) b+='<div class="type-badge badge-hires">HI-RES</div>'; 
                if(it.rank) { let rankClass = 'rank-other'; if(it.rank === 1) rankClass = 'rank-1'; else if(it.rank === 2) rankClass = 'rank-2'; else if(it.rank === 3) rankClass = 'rank-3'; rankBadgeHTML = `<div class="rank-badge ${rankClass}">#${it.rank}</div>`; }
                let subtitle = ''; if (isPl) { subtitle = it.performer ? it.performer.name : 'Playlist'; } else { subtitle = isAlb ? it.artist.name : it.performer.name; }
                let votesHTML = ''; if (it.like_count) { votesHTML = `<div class="vote-count"><i class="fas fa-heart"></i> ${it.like_count}</div>`; }
                d.innerHTML=`${rankBadgeHTML}<div class="badges-container">${b}</div>${mediaElement}<h3>${it.name||it.title}</h3><p>${subtitle}</p>${votesHTML}`; 
                d.onclick=()=>{ if(isAlb) openAlbum(it.id,src); else if(isPl) { if(src === 'ytmusic') openExternalPlaylist(it); else openPlaylist(it.id); } else { if(q){tracks=items; const idx=tracks.findIndex(x=>x.id===it.id); isRadioActive=false; document.getElementById('radioBadge').style.display='none'; loadTrack(idx!==-1?idx:0);} else{tracks=[it]; isRadioActive=false; document.getElementById('radioBadge').style.display='none'; loadTrack(0);} } }; 
                g.appendChild(d); 
            }); 
            updateActiveCard(); 
        }
        async function openArtist(id){ safePushState({},"",`/?artist=${id}`); document.getElementById('trackGrid').innerHTML='<p style="text-align:center;margin-top:50px;">Chargement...</p>'; try{const r=await fetch(`${API_BASE}/artist?id=${id}`); const d=await r.json(); if(d.albums&&d.albums.items) renderGrid(d.albums.items.map(a=>({...a,type:'album',artist:{name:d.name}})),false); }catch(e){console.error(e);} }
        async function openAlbum(id,s){ pushNavState(); safePushState({},"",`/?album=${id}&source=${s||''}`); document.getElementById('trackGrid').innerHTML='<p style="text-align:center;margin-top:50px;">Chargement...</p>'; try{const r=await fetch(`${API_BASE}/album?id=${id}&source=${s||''}`); const d=await r.json(); if(d.tracks&&d.tracks.items) renderGrid(d.tracks.items.map(t=>({...t,type:'track',performer:t.performer||{name:d.artist.name},album:{image:{large:d.image.large},title:d.title},source:d.source||t.source||'qobuz'})),true); }catch(e){console.error(e);} }
        
        function setupMediaSession(t) {
            if ('mediaSession' in navigator) {
                let img = 'https://via.placeholder.com/300';
                if(t.source === 'subsonic') img = `${API_BASE}/get_subsonic_cover/${t.album.image.large}`;
                else if(t.album && t.album.image) img = t.album.image.large.replace('_300','_600');
                navigator.mediaSession.metadata = new MediaMetadata({ title: t.title, artist: t.performer ? t.performer.name : 'Artiste', album: t.album ? t.album.title : '', artwork: [{ src: img, sizes: '512x512', type: 'image/jpeg' }] });
                navigator.mediaSession.setActionHandler('play', play); navigator.mediaSession.setActionHandler('pause', pause); navigator.mediaSession.setActionHandler('previoustrack', () => { if(currentIndex > 0) loadTrack(currentIndex - 1); else activeAudio.currentTime = 0; }); navigator.mediaSession.setActionHandler('nexttrack', handleNext); navigator.mediaSession.setActionHandler('seekto', (d) => { if(d.seekTime) { activeAudio.currentTime = d.seekTime; updateMediaSessionState(); if(typeof broadcastSeek === 'function') broadcastSeek(); } });
            }
        }
        function updateMediaSessionState() { if (!('mediaSession' in navigator)) return; let d = activeAudio.duration; const t = tracks[currentIndex]; if ((!d || !isFinite(d)) && t && t.duration) { d = t.duration; } if (d && isFinite(d) && activeAudio.currentTime <= d) { try { navigator.mediaSession.setPositionState({ duration: d, playbackRate: activeAudio.playbackRate, position: activeAudio.currentTime }); } catch(e) { console.warn("MediaSession Error:", e); } } }
        async function fetchLyrics(t,a,l){ try{const r=await fetch(`${API_BASE}/lyrics?artist=${encodeURIComponent(a)}&title=${encodeURIComponent(t.title)}&album=${encodeURIComponent(l||'')}&duration=${t.duration}`); const d=await r.json(); if(d.type==='synced'){parseLRC(d.lyrics);renderSyncedLyrics();}else if(d.type==='plain')document.getElementById('lyricsView').innerHTML=`<div style="white-space:pre-line;font-size:18px;line-height:1.6;">${d.lyrics}</div>`; else document.getElementById('lyricsView').innerHTML='<p style="margin-top:50px;">Non trouvé.</p>';}catch(e){document.getElementById('lyricsView').innerHTML='<p style="margin-top:50px;">Indisponible.</p>';}}
        function parseLRC(l){if(!l)return;try{const ls=l.split('\n'),rx=/^\[(\d{2}):(\d{2})[.:](\d{2,3})\](.*)/; lyricsData=[]; ls.forEach(x=>{const m=x.match(rx);if(m){const s=parseInt(m[1])*60+parseInt(m[2])+(m[3].length===2?parseInt(m[3])/100:parseInt(m[3])/1000); lyricsData.push({time:s,text:m[4].trim()});}}); if(lyricsData.length>0)firstLineTime=lyricsData[0].time;}catch(e){}}
        function renderSyncedLyrics(){if(!isLyricsMode||!lyricsData.length)return;const c=document.getElementById('lyricsView');c.innerHTML='';const s=document.createElement('div');s.style.height="40vh";c.appendChild(s);lyricsData.forEach((l,i)=>{const d=document.createElement('div');d.className='lyric-line';d.id=`line-${i}`;d.innerText=l.text;d.onclick=()=>{activeAudio.currentTime=l.time;play();};c.appendChild(d);});const b=document.createElement('div');b.style.height="40vh";c.appendChild(b);}
        function syncLyricsUI(){ 
            // OPTIMISATION ARRIERE PLAN
            if (document.hidden) return;

            const ic=document.getElementById('introContainer'); if(isLyricsMode&&firstLineTime!==null&&firstLineTime>0){const lf=firstLineTime-activeAudio.currentTime; if(lf>0.5){ic.style.display='block';document.getElementById('introBarFill').style.width=(activeAudio.currentTime/firstLineTime)*100+'%';document.getElementById('introText').innerText=`Début dans ${lf.toFixed(1)}s`;}else ic.style.display='none';}else ic.style.display='none'; if(!isLyricsMode||!lyricsData.length)return; const cu=activeAudio.currentTime; let idx=-1; for(let i=0;i<lyricsData.length;i++){if(cu>=lyricsData[i].time)idx=i;else break;} document.querySelectorAll('.lyric-line').forEach((d,i)=>{if(i===idx){if(!d.classList.contains('active')){d.classList.add('active');d.scrollIntoView({behavior:"smooth",block:"center"});}}else d.classList.remove('active');}); 
        }

        document.addEventListener('DOMContentLoaded',()=>{
            document.getElementById('btnSearch').addEventListener('click',performSearch);
            document.getElementById('searchInput').addEventListener('keypress',(e)=>{if(e.key==='Enter')performSearch();});
            document.getElementById('btnPlay').addEventListener('click',()=>isPlaying?pause():play());
            document.getElementById('btnNext').addEventListener('click',handleNext);
            document.getElementById('btnPrev').addEventListener('click',()=>{ if(currentIndex > 0) loadTrack(currentIndex - 1); else activeAudio.currentTime = 0; });
            document.getElementById('progressBar').addEventListener('click',(e)=>{if(!activeAudio.duration)return;activeAudio.currentTime=(e.offsetX/document.getElementById('progressBar').clientWidth)*activeAudio.duration;updateMediaSessionState(); if(typeof broadcastSeek === 'function') broadcastSeek();});
            document.querySelector('.vol-slider').oninput=function(){setGlobalVolume(this.value)};
        });

        // GESTION INTELLIGENTE ARRIÈRE-PLAN
        document.addEventListener('visibilitychange', () => {
            if (document.hidden) {
                console.log("Background mode: UI paused");
                if (typeof stopEffects === 'function') stopEffects();
                const orb = document.getElementById('orbitVisual');
                if(orb) orb.style.animation = 'none';
            } else {
                console.log("Foreground mode");
                const t = tracks[currentIndex];
                if (t && magicEnabled && typeof checkMagic === 'function') checkMagic(t.title, t.performer ? t.performer.name : t.artist);
                if (isOrbitOn) {
                     const orb = document.getElementById('orbitVisual');
                     if(orb) { 
                         orb.className = 'orbit-orb'; 
                         void orb.offsetWidth; 
                         orb.classList.add('center-stage', 'orbiting');
                     }
                }
                syncLyricsUI();
            }
        });
