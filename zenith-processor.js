// zenith-processor.js - Le Cerveau Audio (AudioWorklet)
// Ce code tourne dans le thread audio, totalement isolé de l'interface graphique.

class ZenithProcessor extends AudioWorkletProcessor {
    static get parameterDescriptors() {
        return [
            { name: 'volume', defaultValue: 1.0, minValue: 0.0, maxValue: 1.0 },
            { name: 'crossfade', defaultValue: 0.0, minValue: 0.0, maxValue: 1.0 }, // 0=A, 1=B
            { name: 'orbitEnabled', defaultValue: 0.0 }, // 0 ou 1
            { name: 'karaokeEnabled', defaultValue: 0.0 } // 0 ou 1
        ];
    }

    constructor() {
        super();
        // Pour l'effet 8D
        this.orbitPhase = 0;
        this.orbitSpeed = 0.0005; // Vitesse de rotation (ajustable)
    }

    process(inputs, outputs, parameters) {
        // inputs[0] = Piste A (Stereo)
        // inputs[1] = Piste B (Stereo)
        // outputs[0] = Sortie Master (Stereo)
        
        const inputA = inputs[0];
        const inputB = inputs[1];
        const output = outputs[0];

        // Si pas de sortie, on arrête
        if (!output || output.length === 0) return true;

        const channelCount = output.length; // Généralement 2 (Stéréo)
        
        // Récupération des paramètres (lissage automatique si c'est un tableau)
        const volParam = parameters.volume;
        const xfadeParam = parameters.crossfade;
        const isOrbit = parameters.orbitEnabled[0] > 0.5;
        const isKaraoke = parameters.karaokeEnabled[0] > 0.5;

        // Boucle sur chaque échantillon (128 par bloc)
        for (let i = 0; i < output[0].length; i++) {
            
            // 1. MIXAGE CROSSFADE (A vs B)
            // On gère les tableaux de paramètres (automation) ou les valeurs fixes
            const xfade = xfadeParam.length > 1 ? xfadeParam[i] : xfadeParam[0];
            const vol = volParam.length > 1 ? volParam[i] : volParam[0];

            // Equal Power Crossfade (plus pro que linéaire)
            const gainA = Math.cos(xfade * 0.5 * Math.PI);
            const gainB = Math.sin(xfade * 0.5 * Math.PI);

            // Récupération des échantillons A et B (avec sécurité si mono/stéréo)
            let La = inputA.length > 0 ? inputA[0][i] : 0;
            let Ra = inputA.length > 1 ? inputA[1][i] : La;
            
            let Lb = inputB.length > 0 ? inputB[0][i] : 0;
            let Rb = inputB.length > 1 ? inputB[1][i] : Lb;

            // Mixage
            let L = (La * gainA) + (Lb * gainB);
            let R = (Ra * gainA) + (Rb * gainB);

            // 2. EFFET KARAOKÉ (Annulation de Phase "Center Cancel")
            if (isKaraoke) {
                // La voix est souvent au centre (L=R). L - R annule le centre.
                const side = (L - R);
                // On renvoie le signal "Side" sur les deux oreilles
                L = side;
                R = side;
            }

            // 3. EFFET 8D ORBIT (Rotation Spatiale)
            if (isOrbit) {
                // On incrémente la phase pour tourner
                this.orbitPhase += this.orbitSpeed;
                if (this.orbitPhase > 2 * Math.PI) this.orbitPhase -= 2 * Math.PI;

                // Panoramique sinusoïdal (-1 à 1)
                // On utilise une fonction Cosinus pour simuler le cercle autour de la tête
                // Facteur d'atténuation pour simuler la distance (plus réaliste)
                const pan = Math.sin(this.orbitPhase);
                
                // Application du Panoramique (Loi de puissance constante)
                // Pan -1 (Gauche) -> Angle 0
                // Pan +1 (Droite) -> Angle PI/2
                const x = (pan + 1) / 2; // 0 à 1
                const angle = x * Math.PI / 2;
                
                L = L * Math.cos(angle);
                R = R * Math.sin(angle);
            }

            // 4. VOLUME MASTER & SORTIE
            output[0][i] = L * vol;
            if (channelCount > 1) {
                output[1][i] = R * vol;
            }
        }

        return true; // Garder le processeur vivant
    }
}

registerProcessor('zenith-processor', ZenithProcessor);