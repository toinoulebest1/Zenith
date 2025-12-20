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
        { name: "Global Hits", icon: "fa-globe", query: "Global Hits" },
        { name: "Années 80", icon: "fa-compact-disc", query: "Best of 80s" },
        { name: "Années 90", icon: "fa-child", query: "Best of 90s" },
        { name: "Années 2000", icon: "fa-mobile-alt", query: "Best of 2000s" },
        { name: "Disney", icon: "fa-magic", query: "Disney Hits" },
        { name: "Rap US", icon: "fa-music", query: "Hip Hop Classics" },
        { name: "Rock Legends", icon: "fa-guitar", query: "Rock Classics" },
        { name: "Cinéma", icon: "fa-film", query: "Movie Soundtracks" },
        { name: "Anime", icon: "fa-dragon", query: "Anime Openings" },
        { name: "K-Pop", icon: "fa-heart", query: "K-Pop Hits" }
    ];

    let html = `
        <div class="bt-container">
            <h1 style="margin-bottom:10px;">Blind Test 🎵</h1>
            <p style="color:#aaa; margin-bottom:20px;">Configurez votre partie</p>
            
            <div style="background:rgba(255,255,255,0.05); padding:20px; border-radius:15px; margin-bottom:30px; border:1px solid rgba(255,255,255,0.1);">
                <div style="display:flex; justify-content:space-between; margin-bottom:10px;">
                    <span style="font-weight:bold;">Nombre de musiques</span>
                    <span id="lblRoundsDisplay" style="color:var(--primary); font-weight:bold;">5</span>
                </div>
                <input type="range" id="btRoundInput" min="3" max="20" value="5" step="1" style="width:100%; cursor:pointer;" oninput="document.getElementById('lblRoundsDisplay').innerText = this.value">
            </div>

            <p style="color:#aaa; margin-bottom:15px;">Choisissez un thème :</p>
            
            <div class="bt-theme-grid">
    `;

    themes.forEach(t => {
        html += `
            <div class="bt-theme-card" onclick="launchGame('${t.query.replace(/'/g, "\\'")}')">
                <i class="fas ${t.icon}"></i>
                <span>${t.name}</span>
            </div>
        `;
    });

    html += `
            </div>
            
            <div style="margin-top:30px; display:flex; gap:10px; justify-content:center;">
                <input type="text" id="customThemeInput" class="login-input" style="width:200px; margin:0;" placeholder="Thème perso (ex: Rihanna)...">
                <button class="login-btn" style="width:auto; margin:0;" onclick="launchGame(document.getElementById('customThemeInput').value)">GO</button>
            </div>
            
             <button onclick="document.getElementById('trackView').style.display='block'; document.getElementById('blindTestView').style.display='none';" class="login-btn" style="width:auto; background:transparent; border:1px solid #333; margin-top:30px;">Retour</button>
        </div>
        
        <style>
            .bt-theme-grid {
                display: grid;
                grid-template-columns: repeat(auto-fill, minmax(140px, 1fr));
                gap: 15px;
            }
            .bt-theme-card {
                background: rgba(255,255,255,0.05);
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 15px;
                padding: 20px;
                cursor: pointer;
                transition: 0.2s;
                display: flex;
                flex-direction: column;
                align-items: center;
                gap: 10px;
            }
            .bt-theme-card i { font-size: 24px; color: var(--primary); }
            .bt-theme-card:hover {
                background: rgba(255,255,255,0.15);
                transform: translateY(-5px);
                border-color: var(--primary);
            }
        </style>
    `;
    
    container.innerHTML = html;
}

function renderGameInterface(container) {
    container.innerHTML = `
        <div class="bt-container">
            <div class="bt-header">
                <div class="bt-score-pill"><i class="fas fa-flag"></i> Manche <span id="btRound">1</span>/${totalRounds}</div>
                <div class="bt-score-pill" style="color:var(--primary)"><i class="fas fa-trophy"></i> Score: <span id="btScore">0</span></div>
            </div>

            <div class="bt-cover-container">
                <img id="btCoverImg" class="bt-cover-img" src="https://placehold.co/300x300/1a1a1a/666666?text=?">
                <div id="btLoading" style="position:absolute; top:50%; left:50%; transform:translate(-50%,-50%); font-weight:bold;">Prêt ?</div>
            </div>

            <div class="bt-timer-bar">
                <div id="btTimerFill" class="bt-timer-fill"></div>
            </div>

            <div id="btMessage" style="height:24px; font-weight:bold; color:var(--primary); margin-bottom:10px;"></div>

            <div id="btOptionsGrid" class="bt-options-grid">
                <!-- Les boutons seront générés ici -->
            </div>

            <button id="btNextBtn" style="margin-top:20px; padding:12px 30px; background:white; color:black; border:none; border-radius:30px; font-weight:bold; cursor:pointer; display:none;">
                Suivant <i class="fas fa-arrow-right"></i>
            </button>
            
            <div style="margin-top:20px;">
                <button onclick="stopBlindTest()" style="background:transparent; border:none; color:#666; cursor:pointer;">Quitter la partie</button>
            </div>
        </div>
    `;
    
    document.getElementById('btNextBtn').onclick = nextRound;
}

// --- LOGIQUE DU JEU ---

window.startBlindTest = function() {
    // 1. Setup UI
    document.getElementById('trackView').style.display = 'none';
    document.getElementById('lyricsView').style.display = 'none';
    document.getElementById('blindTestView').style.display = 'block';
    document.getElementById('artistView').style.display = 'none';
    
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
        <div style="display:flex; flex-direction:column; align-items:center; justify-content:center; height:60vh;">
            <i class="fas fa-compact-disc fa-spin" style="font-size:50px; color:var(--primary); margin-bottom:20px;"></i>
            <h2>Préparation du Blind Test...</h2>
            <p style="color:#888;">Thème : "${theme}" (${totalRounds} titres)</p>
            <p style="color:#666; font-size:12px; margin-top:10px;">Cela peut prendre quelques secondes.</p>
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
                    <h2>Oups !</h2>
                    <p>Pas assez de titres trouvés pour le thème "${theme}".</p>
                    <button class="login-btn" onclick="startBlindTest()">Essayer un autre thème</button>
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
                <h2>Erreur</h2>
                <p>Impossible de lancer la partie.</p>
                <p style="font-size:12px; color:#666;">${e.message}</p>
                <button class="login-btn" onclick="startBlindTest()">Retour</button>
            </div>`;
    }
}

window.stopBlindTest = function() {
    btAudio.pause();
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
    let streamUrl = '';
    if (currentTrackMeta.source === 'subsonic') {
        streamUrl = `${API_BASE}/stream_subsonic/${currentTrackMeta.id}`;
    } else {
        streamUrl = `${API_BASE}/stream/${currentTrackMeta.id}`;
    }
    
    btAudio.src = streamUrl;
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
    
    // Fallback si ça charge pas
    btAudio.onerror = () => {
         console.error("Audio Error", btAudio.error);
         if(msgEl) msgEl.innerText = "Erreur lecture, on passe...";
         setTimeout(nextRound, 2000);
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
            <span style="font-size:1.1em; margin-bottom:4px; text-align:center;">${track.title}</span>
            <span style="font-size:0.9em; opacity:0.7; text-align:center;">${artistName || 'Artiste Inconnu'}</span>
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
    
    container.innerHTML = `
        <h1>Partie Terminée !</h1>
        <p style="font-size:24px; margin:20px 0;">Score Final : <strong style="color:var(--primary)">${score} / ${totalRounds}</strong></p>
        
        <div style="display:flex; gap:10px; justify-content:center;">
            <button onclick="startBlindTest()" class="login-btn" style="width:auto;">Rejouer</button>
            <button onclick="document.getElementById('trackView').style.display='block'; document.getElementById('blindTestView').style.display='none';" class="login-btn" style="width:auto; background:#333; color:white;">Quitter</button>
        </div>
    `;
}