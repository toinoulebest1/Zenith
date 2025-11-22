// blindtest.js - Nouvelle Logique QCM

const BLIND_TEST_API = '/blind_test_tracks';
const ROUND_DURATION = 15; // 15 secondes pour deviner
const GAME_MAX_ROUNDS = 5;

let gameTracks = [];
let currentRound = 0;
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
    
    viewContainer.innerHTML = `
        <div class="bt-container">
            <div class="bt-header">
                <div class="bt-score-pill"><i class="fas fa-flag"></i> Manche <span id="btRound">1</span>/${GAME_MAX_ROUNDS}</div>
                <div class="bt-score-pill" style="color:var(--primary)"><i class="fas fa-trophy"></i> Score: <span id="btScore">0</span></div>
            </div>

            <div class="bt-cover-container">
                <img id="btCoverImg" class="bt-cover-img" src="https://via.placeholder.com/300">
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
        </div>
    `;
    
    document.getElementById('btNextBtn').onclick = nextRound;
}

// --- LOGIQUE DU JEU ---

window.startBlindTest = async function() {
    // 1. Setup UI
    document.getElementById('trackView').style.display = 'none';
    document.getElementById('lyricsView').style.display = 'none';
    document.getElementById('blindTestView').style.display = 'block';
    
    // Stop main player if playing
    if(typeof pause === 'function') pause();

    setupBlindTestUI();
    
    // 2. Reset Stats
    currentRound = 0;
    score = 0;
    
    // 3. Fetch Tracks
    document.getElementById('btMessage').innerText = "Chargement des pistes...";
    try {
        const res = await fetch(API_BASE + BLIND_TEST_API);
        if (!res.ok) throw new Error("API Error");
        
        const data = await res.json();
        
        // Vérification que c'est bien un tableau
        if (Array.isArray(data)) {
            gameTracks = data;
        } else if (data.tracks) {
            gameTracks = data.tracks;
        } else {
            throw new Error("Format de données invalide");
        }
        
        // On a besoin d'au moins 4 pistes pour faire un QCM
        if (gameTracks.length < 4) {
             document.getElementById('btMessage').innerText = "Erreur: Pas assez de pistes trouvées.";
             return;
        }

        nextRound();

    } catch (e) {
        console.error("BlindTest Error:", e);
        document.getElementById('btMessage').innerText = "Erreur connexion (" + e.message + ")";
    }
}

function nextRound() {
    // Stop audio précédent
    btAudio.pause();
    btAudio.currentTime = 0;
    
    if (currentRound >= GAME_MAX_ROUNDS) {
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
        imgEl.src = 'https://via.placeholder.com/300/111/111?text=?';
        imgEl.classList.remove('revealed');
    }

    // Choix du morceau courant
    // Attention à l'index bounds
    const trackIndex = (currentRound - 1) % gameTracks.length;
    currentTrackMeta = gameTracks[trackIndex];
    
    // Traitement de l'image (Qobuz vs Subsonic)
    if (!currentTrackMeta.img) {
         if (currentTrackMeta.album && currentTrackMeta.album.image && currentTrackMeta.album.image.large) {
             currentTrackMeta.img = currentTrackMeta.album.image.large.replace('_300', '_600');
         } else {
             currentTrackMeta.img = 'https://via.placeholder.com/300';
         }
    }

    // 1. Génération des choix (1 correct + 3 faux)
    const options = generateOptions(currentTrackMeta, gameTracks);
    renderOptions(options);

    // 2. Audio
    const streamUrl = `${API_BASE}/stream/${currentTrackMeta.id}`;
    
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
        <p style="font-size:24px; margin:20px 0;">Score Final : <strong style="color:var(--primary)">${score} / ${GAME_MAX_ROUNDS}</strong></p>
        
        <div style="display:flex; gap:10px; justify-content:center;">
            <button onclick="startBlindTest()" class="login-btn" style="width:auto;">Rejouer</button>
            <button onclick="document.getElementById('trackView').style.display='block'; document.getElementById('blindTestView').style.display='none';" class="login-btn" style="width:auto; background:#333; color:white;">Quitter</button>
        </div>
    `;
}