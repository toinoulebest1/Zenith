// blindtest.js - Nouvelle Logique QCM avec Thèmes

const BLIND_TEST_API = '/blind_test_tracks';
const ROUND_DURATION = 15; // 15 secondes pour deviner

let gameTracks = [];
let currentRound = 0;
let totalRounds = 5; // Valeur par défaut
let score = 0;
let currentTrackMeta = null;
let timerInterval = null;
let timeLeft = ROUND_DURATION;
let isRoundActive = false;

// Instance audio dédiée au jeu pour ne pas casser le lecteur principal
const btAudio = new Audio();
btAudio.crossOrigin = "anonymous";

// Lecteur Shaka dédié au jeu : les titres Tidal (addon) sont en DASH, pas en URL directe.
// Les segments (CDN sans CORS) sont réécrits vers /tidal_proxy, comme dans le lecteur principal.
let btShaka = null;
function getBlindTestShaka() {
    if (!btShaka && typeof shaka !== 'undefined' && shaka.Player.isBrowserSupported()) {
        btShaka = new shaka.Player(btAudio);
        btShaka.getNetworkingEngine().registerRequestFilter((type, request) => {
            request.uris = request.uris.map(u =>
                u.includes('audio.tidal.com') ? `${API_BASE}/tidal_proxy?url=${encodeURIComponent(u)}` : u
            );
        });
    }
    return btShaka;
}

// --- DOM ELEMENTS ---
function setupBlindTestUI() {
    const viewContainer = document.getElementById('blindTestView');
    if (!viewContainer) return;

    // On vide le conteneur pour être sûr
    viewContainer.innerHTML = '';

    // Si on n'a pas encore choisi de thème (pas de tracks chargés), on affiche le sélecteur
    if (gameTracks.length === 0) {
        renderThemeSelector(viewContainer);
    } else {
        renderGameInterface(viewContainer);
    }
}

function renderThemeSelector(container) {
    const themes = [
        { name: "Global Hits", icon: "fa-globe", query: "Global Hits", bg: "linear-gradient(135deg,#0ea5e9,#0b3a5c)" },
        { name: "Années 80", icon: "fa-compact-disc", query: "Best of 80s", bg: "linear-gradient(135deg,#ec4899,#5b1140)" },
        { name: "Années 90", icon: "fa-child", query: "Best of 90s", bg: "linear-gradient(135deg,#f59e0b,#6b3a04)" },
        { name: "Années 2000", icon: "fa-mobile-screen", query: "Best of 2000s", bg: "linear-gradient(135deg,#8b5cf6,#33176b)" },
        { name: "Disney", icon: "fa-wand-magic-sparkles", query: "Disney Hits", bg: "linear-gradient(135deg,#3b82f6,#172f6b)" },
        { name: "Rap US", icon: "fa-microphone-lines", query: "Hip Hop Classics", bg: "linear-gradient(135deg,#ef4444,#5c1414)" },
        { name: "Rock Legends", icon: "fa-guitar", query: "Rock Classics", bg: "linear-gradient(135deg,#78716c,#292524)" },
        { name: "Cinéma", icon: "fa-film", query: "Movie Soundtracks", bg: "linear-gradient(135deg,#d97706,#4a2a03)" },
        { name: "Anime", icon: "fa-dragon", query: "Anime Openings", bg: "linear-gradient(135deg,#f43f5e,#4c0519)" },
        { name: "K-Pop", icon: "fa-heart", query: "K-Pop Hits", bg: "linear-gradient(135deg,#d946ef,#4a0f57)" }
    ];

    let html = `
        <div class="bt-container">
            <h1 class="bt-title">Blind test</h1>
            <p class="bt-sub">Écoutez un extrait, trouvez le bon titre parmi quatre.</p>

            <div class="bt-panel">
                <div class="bt-panel-head">
                    <span>Nombre de manches</span>
                    <span id="lblRoundsDisplay">5</span>
                </div>
                <input type="range" class="settings-slider" id="btRoundInput" min="3" max="20" value="5" step="1" oninput="document.getElementById('lblRoundsDisplay').innerText = this.value">
            </div>

            <p class="bt-label">Choisissez un thème</p>
            <div class="bt-theme-grid">
    `;

    themes.forEach(t => {
        html += `
            <div class="bt-theme-card" style="--tile-bg:${t.bg}" onclick="launchGame('${t.query.replace(/'/g, "\\'")}')">
                <i class="fas ${t.icon}"></i>
                <span>${t.name}</span>
            </div>
        `;
    });

    html += `
            </div>

            <div class="bt-custom-row">
                <input type="text" id="customThemeInput" class="login-input" placeholder="Ou un thème perso (ex : Rihanna)…" onkeypress="if(event.key==='Enter') launchGame(this.value)">
                <button class="login-btn" onclick="launchGame(document.getElementById('customThemeInput').value)">Jouer</button>
            </div>
        </div>
    `;

    container.innerHTML = html;
}

function renderGameInterface(container) {
    container.innerHTML = `
        <div class="bt-container">
            <div class="bt-header">
                <div class="bt-score-pill"><i class="fas fa-flag"></i> Manche <span id="btRound">1</span>/${totalRounds}</div>
                <div class="bt-score-pill accent"><i class="fas fa-trophy"></i> Score : <span id="btScore">0</span></div>
            </div>

            <div class="bt-cover-container">
                <img id="btCoverImg" class="bt-cover-img" src="https://placehold.co/300x300/17171d/3a3a46?text=?" alt="">
                <div id="btLoading">Prêt ?</div>
            </div>

            <div class="bt-timer-bar">
                <div id="btTimerFill" class="bt-timer-fill"></div>
            </div>

            <div id="btMessage"></div>

            <div id="btOptionsGrid" class="bt-options-grid">
                <!-- Les boutons seront générés ici -->
            </div>

            <button id="btNextBtn" class="login-btn bt-next-btn" style="display:none;">
                Suivant <i class="fas fa-arrow-right"></i>
            </button>

            <div>
                <button onclick="stopBlindTest()" class="bt-quit">Quitter la partie</button>
            </div>
        </div>
    `;

    document.getElementById('btNextBtn').onclick = nextRound;
}

// --- LOGIQUE DU JEU ---

window.startBlindTest = function() {
    // 1. Setup UI
    if (typeof hideAllViews === 'function') hideAllViews();
    if (typeof setActiveNav === 'function') setActiveNav('blindtest');
    isLyricsMode = false;
    document.getElementById('blindTestView').style.display = 'block';

    // Stop main player if playing
    if(typeof pause === 'function') pause();

    // Reset game state
    gameTracks = [];
    currentRound = 0;
    score = 0;

    setupBlindTestUI();
}

window.launchGame = async function(theme) {
    if (!theme) theme = "Global Hits";

    // Récupérer le nombre de rounds choisi
    const roundsInput = document.getElementById('btRoundInput');
    totalRounds = roundsInput ? parseInt(roundsInput.value) : 5;

    // Afficher écran de chargement
    const viewContainer = document.getElementById('blindTestView');
    viewContainer.innerHTML = `
        <div class="bt-container">
            <div class="ui-state loading" style="padding-top:18vh;">
                <i class="fas fa-compact-disc fa-spin" style="font-size:44px;"></i>
                <h2 class="bt-title" style="font-size:1.6rem;">Préparation de la partie…</h2>
                <p>Thème « ${theme} » · ${totalRounds} titres</p>
            </div>
        </div>
    `;

    try {
        const res = await fetch(`${API_BASE}${BLIND_TEST_API}?theme=${encodeURIComponent(theme)}&limit=${totalRounds}`);
        if (!res.ok) throw new Error("API Error");

        const data = await res.json();

        if (Array.isArray(data)) {
            gameTracks = data;
        } else if (data.tracks) {
            gameTracks = data.tracks;
        } else {
            throw new Error("Format de données invalide");
        }

        // On vérifie qu'on a au moins assez de pistes pour faire une question (1 bonne + 3 mauvaises = 4 min)
        // Mais idéalement on veut le nombre demandé.
        if (gameTracks.length < 4) {
             viewContainer.innerHTML = `
                <div class="bt-container">
                    <div class="ui-state" style="padding-top:16vh;">
                        <i class="fas fa-face-frown-open"></i>
                        <h2 class="bt-title" style="font-size:1.6rem;">Oups !</h2>
                        <p>Pas assez de titres trouvés pour le thème « ${theme} ».</p>
                        <button class="login-btn" style="width:auto;" onclick="startBlindTest()">Essayer un autre thème</button>
                    </div>
                </div>`;
             return;
        }

        // Ajuster le totalRounds si on a trouvé moins de musiques que prévu
        totalRounds = Math.min(totalRounds, gameTracks.length);

        // Démarrer la partie
        setupBlindTestUI(); // Affiche l'interface de jeu car gameTracks est rempli
        nextRound();

    } catch (e) {
        console.error("BlindTest Error:", e);
        viewContainer.innerHTML = `
            <div class="bt-container">
                <div class="ui-state error" style="padding-top:16vh;">
                    <i class="fas fa-triangle-exclamation"></i>
                    <h2 class="bt-title" style="font-size:1.6rem;">Impossible de lancer la partie</h2>
                    <p>${e.message}</p>
                    <button class="login-btn" style="width:auto;" onclick="startBlindTest()">Retour</button>
                </div>
            </div>`;
    }
}

window.stopBlindTest = function() {
    btAudio.pause();
    if (btShaka) btShaka.unload();
    btAudio.src = "";
    startBlindTest(); // Retour à la sélection
}

function nextRound() {
    // Stop audio précédent
    btAudio.pause();
    btAudio.currentTime = 0;

    if (currentRound >= totalRounds) {
        endGame();
        return;
    }

    // Reset UI
    isRoundActive = true;
    currentRound++;

    const roundEl = document.getElementById('btRound');
    const scoreEl = document.getElementById('btScore');
    const msgEl = document.getElementById('btMessage');
    const nextBtn = document.getElementById('btNextBtn');
    const loadingEl = document.getElementById('btLoading');
    const timerFill = document.getElementById('btTimerFill');
    const imgEl = document.getElementById('btCoverImg');

    if(roundEl) roundEl.innerText = currentRound;
    if(scoreEl) scoreEl.innerText = score;
    if(nextBtn) nextBtn.style.display = 'none';
    if(msgEl) msgEl.innerText = "Écoutez...";
    if(timerFill) timerFill.style.width = '100%';
    if(loadingEl) loadingEl.style.display = 'block';

    // Image floutée et placeholder
    if(imgEl) {
        imgEl.src = 'https://placehold.co/300x300/111/444?text=?';
        imgEl.classList.remove('revealed');
    }

    // Choix du morceau courant
    // Attention à l'index bounds
    const trackIndex = (currentRound - 1) % gameTracks.length;
    currentTrackMeta = gameTracks[trackIndex];

    // Traitement de l'image
    if (!currentTrackMeta.img) {
         if (currentTrackMeta.album && currentTrackMeta.album.image && currentTrackMeta.album.image.large) {
             currentTrackMeta.img = currentTrackMeta.album.image.large.replace('_300', '_600');
         } else {
             currentTrackMeta.img = 'https://placehold.co/300x300/1a1a1a/666666?text=Music';
         }
    }

    // 1. Génération des choix (1 correct + 3 faux)
    const options = generateOptions(currentTrackMeta, gameTracks);
    renderOptions(options);

    // 2. Audio
    btAudio.volume = 1;

    // On attend que ça charge un peu
    btAudio.onloadeddata = () => {
         if(!isRoundActive) return; // Si l'utilisateur a quitté entre temps

         // Random start si le morceau est long (> 30s)
         if (btAudio.duration > 30) {
             const maxStart = btAudio.duration - 20;
             btAudio.currentTime = Math.floor(Math.random() * maxStart);
         }

         btAudio.play().then(() => {
             if(loadingEl) loadingEl.style.display = 'none';
             startTimer();
         }).catch(e => {
             console.error("Auto-play blocked", e);
             if(msgEl) msgEl.innerText = "Cliquez pour lire";
             // Fallback click to play
             document.body.onclick = () => { btAudio.play(); document.body.onclick = null; }
         });
    };

    // Fallback si ça charge pas (une seule fois par manche : Shaka et l'élément audio
    // peuvent tous deux signaler la même erreur)
    let roundFailed = false;
    btAudio.onerror = () => {
         if (roundFailed) return;
         roundFailed = true;
         console.error("Audio Error", btAudio.error);
         if(msgEl) msgEl.innerText = "Erreur lecture, on passe...";
         setTimeout(nextRound, 2000);
    }

    // Chargement APRÈS la pose des gestionnaires : Shaka peut déclencher loadeddata très vite.
    // Titre Tidal (addon) : DASH FLAC lu via le Shaka du jeu.
    if (currentTrackMeta.source === 'tidal_hund') {
        const player = getBlindTestShaka();
        if (!player) { btAudio.onerror(); return; }
        loadTidalIntoShaka(player, currentTrackMeta.id).catch(e => {
            console.error("Tidal Load Error", e);
            btAudio.onerror();
        });
    } else {
        let streamUrl = '';
        if (currentTrackMeta.source === 'subsonic') {
            streamUrl = `${API_BASE}/stream_subsonic/${currentTrackMeta.id}`;
        } else {
            streamUrl = `${API_BASE}/stream/${currentTrackMeta.id}`;
        }
        // Shaka doit lâcher l'élément audio avant qu'on lui donne une URL directe
        (btShaka ? btShaka.unload() : Promise.resolve()).then(() => { btAudio.src = streamUrl; });
    }
}

function generateOptions(correctTrack, allTracks) {
    // Filtrer pour ne pas avoir le bon titre dans les faux
    const wrongTracks = allTracks.filter(t => t.id !== correctTrack.id);
    // Mélanger les faux
    const shuffledWrong = wrongTracks.sort(() => 0.5 - Math.random());
    // Prendre 3 faux
    const selection = shuffledWrong.slice(0, 3);
    // Ajouter le vrai
    selection.push(correctTrack);
    // Mélanger le tout
    return selection.sort(() => 0.5 - Math.random());
}

function renderOptions(options) {
    const grid = document.getElementById('btOptionsGrid');
    if(!grid) return;
    grid.innerHTML = '';

    options.forEach(track => {
        const btn = document.createElement('div');
        btn.className = 'bt-option-btn';

        let artistName = track.artist;
        if (!artistName && track.performer) artistName = track.performer.name;

        btn.innerHTML = `
            <span style="font-size:1.05em; text-align:center;">${track.title}</span>
            <span style="font-size:0.88em; opacity:0.65; font-weight:600; text-align:center;">${artistName || 'Artiste inconnu'}</span>
        `;
        btn.onclick = () => handleAnswer(track, btn);
        grid.appendChild(btn);
    });
}

function startTimer() {
    if (timerInterval) clearInterval(timerInterval);
    timeLeft = ROUND_DURATION;

    timerInterval = setInterval(() => {
        timeLeft -= 0.1;
        const pct = (timeLeft / ROUND_DURATION) * 100;
        const fill = document.getElementById('btTimerFill');
        if(fill) fill.style.width = `${pct}%`;

        if (timeLeft <= 0) {
            handleAnswer(null, null); // Time out
        }
    }, 100);
}

function handleAnswer(selectedTrack, btnElement) {
    if (!isRoundActive) return;
    isRoundActive = false;
    clearInterval(timerInterval);
    btAudio.pause();

    const isCorrect = selectedTrack && selectedTrack.id === currentTrackMeta.id;
    const btns = document.querySelectorAll('.bt-option-btn');

    // Reveal Visuals
    const imgEl = document.getElementById('btCoverImg');
    if(imgEl) {
        imgEl.src = currentTrackMeta.img;
        imgEl.classList.add('revealed');
    }
    document.getElementById('btNextBtn').style.display = 'inline-block';

    if (isCorrect) {
        score++;
        document.getElementById('btScore').innerText = score;
        document.getElementById('btMessage').innerText = "✅ BONNE RÉPONSE !";
        if(btnElement) btnElement.classList.add('correct');
    } else {
        document.getElementById('btMessage').innerText = "❌ RATÉ !";
        if (btnElement) btnElement.classList.add('wrong');

        // Montrer la bonne réponse
        btns.forEach(b => {
            if (b.innerHTML.includes(currentTrackMeta.title)) {
                b.classList.add('correct');
            }
        });
    }
}

function endGame() {
    const container = document.querySelector('.bt-container');
    if(!container) return;

    const ratio = totalRounds ? score / totalRounds : 0;
    const verdict = ratio === 1 ? 'Sans faute, impressionnant !' : ratio >= 0.7 ? 'Très belle oreille !' : ratio >= 0.4 ? 'Pas mal du tout.' : 'La prochaine sera la bonne.';
    container.innerHTML = `
        <div class="ui-state" style="padding-top:12vh;">
            <i class="fas fa-trophy" style="font-size:48px; color:var(--gold);"></i>
            <h1 class="bt-title">Partie terminée</h1>
            <p style="font-size:15px;">${verdict}</p>
            <div style="font-family:var(--font-display); font-size:3.2rem; font-weight:800; color:var(--primary); line-height:1;">${score}<span style="font-size:1.4rem; color:var(--text-faint);"> / ${totalRounds}</span></div>
            <div class="btn-row" style="justify-content:center; max-width:360px; margin-top:6px;">
                <button onclick="startBlindTest()" class="login-btn">Rejouer</button>
                <button onclick="goHome()" class="login-btn btn-ghost">Quitter</button>
            </div>
        </div>
    `;
}