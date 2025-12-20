// bluetooth.js - Gestion de la sortie audio (Enceintes/Bluetooth)

const BluetoothManager = {
    // Vérifie si le navigateur supporte le changement de sortie audio (Chrome/Edge Desktop)
    isSupported: 'setSinkId' in HTMLMediaElement.prototype,

    init: function() {
        this.initAutoDetect();
    },

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
            const deviceInfo = await navigator.mediaDevices.selectAudioOutput();
            
            // Applique le choix aux deux lecteurs (pour le crossfade)
            await this.applyOutput(deviceInfo.deviceId);
            
            // Changement visuel
            const icon = document.getElementById('btBtnIcon');
            if (icon) {
                icon.style.color = '#00d2ff';
                icon.className = 'fab fa-bluetooth-b'; // Force l'icône B
            }
            
            showToast(`Sortie : ${deviceInfo.label || 'Périphérique externe'}`);

            // DÉTECTION INTELLIGENTE : Si c'est du Bluetooth, on propose/force le mode Direct
            if (deviceInfo.label && deviceInfo.label.toLowerCase().includes('bluetooth')) {
                this.enableDirectModeIfNeeded();
            }

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
            console.log(`Audio output set to device: ${deviceId}`);
        } catch (e) {
            console.error("Failed to set audio sink ID", e);
        }
    },

    // Surveille les changements de périphériques pour activer le mode voiture automatiquement
    initAutoDetect: function() {
        if (!navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
        
        const check = async () => {
            try {
                const devices = await navigator.mediaDevices.enumerateDevices();
                // On cherche un périphérique de sortie dont le nom contient "bluetooth"
                const hasBT = devices.some(d => d.kind === 'audiooutput' && d.label.toLowerCase().includes('bluetooth'));
                
                if (hasBT) {
                    this.enableDirectModeIfNeeded();
                }
            } catch (e) {
                console.warn("Bluetooth auto-detect failed", e);
            }
        };

        navigator.mediaDevices.ondevicechange = check;
        // Vérification initiale après un court délai (laisser le temps aux labels de charger)
        setTimeout(check, 2000);
    },

    enableDirectModeIfNeeded: function() {
        const isDirect = localStorage.getItem('zenith_pref_directAudio') === 'true';
        if (!isDirect) {
            console.log("🚗 Bluetooth detected: Switching to Direct Audio");
            
            // On affiche un toast persistant
            const t = document.createElement('div');
            t.className = 'toast show';
            t.innerHTML = '<i class="fab fa-bluetooth-b"></i> Bluetooth détecté : Mode Voiture activé...';
            t.style.zIndex = '99999';
            t.style.background = '#00d2ff';
            t.style.color = '#000';
            document.body.appendChild(t);
            
            // On sauvegarde et on reload
            localStorage.setItem('zenith_pref_directAudio', 'true');
            
            // Petit délai pour que l'utilisateur lise le message
            setTimeout(() => {
                window.location.reload();
            }, 2000);
        }
    }
};

// Démarrage du script
document.addEventListener('DOMContentLoaded', () => BluetoothManager.init());