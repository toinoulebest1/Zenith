// bluetooth.js - Gestion de la sortie audio (Enceintes/Bluetooth)

const BluetoothManager = {
    // Vérifie si le navigateur supporte le changement de sortie audio (Chrome/Edge Desktop)
    isSupported: 'setSinkId' in HTMLMediaElement.prototype,

    toggle: async function() {
        // Cas 1 : Mobile ou Navigateur non compatible (Safari, Firefox, Android Chrome)
        // Sur mobile, le son sort automatiquement sur le périphérique Bluetooth connecté par l'OS.
        if (!this.isSupported) {
            showToast("⚠️ Utilisez les réglages Bluetooth de votre appareil pour connecter une enceinte.");
            return;
        }

        // Cas 2 : PC/Mac Compatible (Audio Output Selection API)
        try {
            // Demande à l'utilisateur de choisir un périphérique de sortie
            // Cela ouvre une popup native du navigateur
            const deviceId = await navigator.mediaDevices.selectAudioOutput();
            
            // Applique le choix aux deux lecteurs (pour le crossfade)
            await this.applyOutput(deviceId);
            
            // Changement visuel
            const icon = document.getElementById('btBtnIcon');
            if (icon) {
                icon.style.color = '#00d2ff';
                icon.className = 'fab fa-bluetooth-b'; // Force l'icône B
            }
            
            showToast("Sortie audio modifiée 🔊");

        } catch (err) {
            if (err.name === 'NotFoundError') {
                showToast("Aucun périphérique audio trouvé.");
            } else if (err.name !== 'NotAllowedError') {
                // NotAllowed = L'utilisateur a annulé la sélection, on ignore
                console.error('Audio Output Error:', err);
                showToast("Erreur lors du changement de sortie.");
            }
        }
    },

    // Applique l'ID du périphérique aux éléments audio
    applyOutput: async function(deviceId) {
        try {
            if (window.playerA && typeof window.playerA.setSinkId === 'function') {
                await window.playerA.setSinkId(deviceId);
            }
            if (window.playerB && typeof window.playerB.setSinkId === 'function') {
                await window.playerB.setSinkId(deviceId);
            }
            
            // Note : L'AudioContext (Web Audio API) suit généralement la sortie par défaut,
            // mais setSinkId sur les éléments HTML Audio suffit souvent pour rediriger le flux principal.
            console.log(`Audio output set to device: ${deviceId}`);
        } catch (e) {
            console.error("Failed to set audio sink ID", e);
        }
    }
};