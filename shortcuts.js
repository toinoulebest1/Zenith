// shortcuts.js - Gestion des raccourcis clavier pour Zenith Player

document.addEventListener('keydown', (e) => {
    // 1. On ignore si l'utilisateur écrit dans un champ de texte
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;

    // 2. SUPPORT TV : Si on est en mode TV et qu'un élément a le focus,
    // on laisse tv.js gérer la navigation avec les flèches.
    // On n'intercepte que si le focus est sur le body ou l'élément actif n'est pas "navigable"
    const isTV = document.body.classList.contains('tv-mode');
    if (isTV && ['ArrowUp', 'ArrowDown', 'ArrowLeft', 'ArrowRight'].includes(e.code)) {
        if (document.activeElement !== document.body) {
            return; // On laisse tv.js gérer le focus
        }
    }

    // Prévention du scroll par défaut
    if(['Space', 'ArrowUp', 'ArrowDown'].includes(e.code)) {
        e.preventDefault();
    }

    switch(e.code) {
        case 'Space': // Play/Pause
        case 'KeyK':
            if (typeof isPlaying !== 'undefined' && typeof play === 'function' && typeof pause === 'function') {
                isPlaying ? pause() : play();
                showToast(isPlaying ? "Pause ⏸️" : "Lecture ▶️");
            }
            break;

        case 'ArrowRight': // Avancer +5s
            if (typeof activeAudio !== 'undefined') {
                activeAudio.currentTime = Math.min(activeAudio.duration, activeAudio.currentTime + 5);
                if(typeof updateMediaSessionState === 'function') updateMediaSessionState();
                if(typeof syncLyricsUI === 'function') syncLyricsUI();
                showToast("Avance +5s ⏩");
            }
            break;

        case 'ArrowLeft': // Reculer -5s
        case 'KeyJ':
            if (typeof activeAudio !== 'undefined') {
                activeAudio.currentTime = Math.max(0, activeAudio.currentTime - 5);
                if(typeof updateMediaSessionState === 'function') updateMediaSessionState();
                if(typeof syncLyricsUI === 'function') syncLyricsUI();
                showToast("Recul -5s ⏪");
            }
            break;

        case 'ArrowUp': // Volume +
            if (typeof globalVolume !== 'undefined' && typeof setGlobalVolume === 'function') {
                let vUp = Math.min(1, parseFloat(globalVolume) + 0.05);
                setGlobalVolume(vUp);
                const slider = document.querySelector('.vol-slider');
                if(slider) slider.value = vUp;
            }
            break;

        case 'ArrowDown': // Volume -
            if (typeof globalVolume !== 'undefined' && typeof setGlobalVolume === 'function') {
                let vDown = Math.max(0, parseFloat(globalVolume) - 0.05);
                setGlobalVolume(vDown);
                const slider = document.querySelector('.vol-slider');
                if(slider) slider.value = vDown;
            }
            break;

        case 'KeyM': // Mute Intelligent
            if (typeof setGlobalVolume === 'function') {
                const slider = document.querySelector('.vol-slider');
                
                if (globalVolume > 0) {
                    // ON COUPE
                    window.lastVolume = globalVolume; // On sauvegarde le niveau actuel
                    setGlobalVolume(0);
                    if(slider) slider.value = 0;
                    showToast("Muet 🔇");
                } else {
                    // ON REMET
                    // On rétablit le dernier volume ou 1 (100%) par défaut
                    let restoreVol = window.lastVolume || 1;
                    setGlobalVolume(restoreVol);
                    if(slider) slider.value = restoreVol;
                    showToast("Volume rétabli 🔊");
                }
            }
            break;
            
        case 'KeyF': // Fullscreen / Zen Mode
            toggleZenMode();
            break;

        case 'KeyL': // CORRECTION : Paroles (Lyrics) au lieu d'avance rapide
        case 'KeyV': // V fonctionne aussi
            if (typeof toggleLyrics === 'function') {
                toggleLyrics();
                showToast("Paroles 🎤");
            }
            break;
    }
});

// GESTION DU MODE ZEN ET DE L'INACTIVITÉ
let isZenMode = false;
let zenIdleTimer = null;

function resetZenIdle() {
    if (!isZenMode) return;
    
    // Réveil
    document.body.classList.remove('zen-idle');
    
    // Reset Timer
    if (zenIdleTimer) clearTimeout(zenIdleTimer);
    
    // Au bout de 3 secondes d'inactivité, on cache tout
    zenIdleTimer = setTimeout(() => {
        if (isZenMode) document.body.classList.add('zen-idle');
    }, 3000);
}

function setZenMode(active) {
    isZenMode = active;
    
    // SAUVEGARDE DE L'ÉTAT (PERSISTANCE)
    localStorage.setItem('zenith_zen_mode', active);

    if (isZenMode) {
        document.body.classList.add('zen-active');
        
        // Activation détection inactivité
        document.addEventListener('mousemove', resetZenIdle);
        document.addEventListener('click', resetZenIdle);
        document.addEventListener('keydown', resetZenIdle);
        document.addEventListener('touchstart', resetZenIdle); // Support tactile
        
        // On lance le timer tout de suite
        resetZenIdle();
        
    } else {
        document.body.classList.remove('zen-active');
        document.body.classList.remove('zen-idle');
        
        // Nettoyage
        document.removeEventListener('mousemove', resetZenIdle);
        document.removeEventListener('click', resetZenIdle);
        document.removeEventListener('keydown', resetZenIdle);
        document.removeEventListener('touchstart', resetZenIdle);
        
        if (zenIdleTimer) clearTimeout(zenIdleTimer);
    }
}

function toggleZenMode() {
    setZenMode(!isZenMode);
    showToast(isZenMode ? "Mode Zen Activé 🧘" : "Mode Zen Désactivé");
}

// FONCTION DE RESTAURATION ROBUSTE
function restoreZenState() {
    const savedState = localStorage.getItem('zenith_zen_mode') === 'true';
    if (savedState) {
        // On réapplique le mode même si la variable isZenMode est déjà à true,
        // car le DOM peut avoir été réinitialisé par le navigateur
        setZenMode(true);
    }
}

// 1. Restauration au chargement initial
document.addEventListener('DOMContentLoaded', restoreZenState);

// 2. Restauration différée pour surcharger d'autres scripts qui réinitialiseraient l'UI
window.addEventListener('load', () => {
    setTimeout(restoreZenState, 500);
});

// 3. Restauration CRITIQUE lors du retour sur l'app (PWA Resume)
document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
        // Petit délai pour laisser le navigateur redessiner la page
        setTimeout(restoreZenState, 200);
    }
});