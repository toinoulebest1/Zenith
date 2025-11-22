// shortcuts.js - Gestion des raccourcis clavier pour Zenith Player

document.addEventListener('keydown', (e) => {
    // On ignore si l'utilisateur écrit dans un champ de texte (recherche, login...)
    if (['INPUT', 'TEXTAREA', 'SELECT'].includes(document.activeElement.tagName)) return;

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

// Fonction simple pour le mode Zen (cache l'UI sauf le player et l'art)
let isZenMode = false;
function toggleZenMode() {
    isZenMode = !isZenMode;
    const appLayout = document.querySelector('.app-layout');
    
    if (isZenMode) {
        document.body.classList.add('zen-active');
        showToast("Mode Zen Activé 🧘");
    } else {
        document.body.classList.remove('zen-active');
        showToast("Mode Zen Désactivé");
    }
}