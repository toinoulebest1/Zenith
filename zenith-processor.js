// zenith-processor.js - Le Cerveau Audio (AudioWorklet)
// Ce code tourne dans le thread audio, totalement isolé de l'interface graphique.

class ZenithProcessor extends AudioWorkletProcessor {
    static get parameterDescriptors() {
        return [
            { name: 'volume', defaultValue: 1.0, minValue: 0.0, maxValue: 1.0 },
            { name: 'crossfade', defaultValue: 0.0, minValue: 0.0, maxValue: 1.0 }, // 0=A, 1=B
            { name: 'orbitEnabled', defaultValue: 0.0 } // 0 ou 1
        ];
    }

    constructor() {
        super();
        // Pour l'effet 8D
        this.orbitPhase = 0;
        this.orbitSpeed = 0.0005; // Vitesse de rotation
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
        
        // Récupération des paramètres
        const volParam = parameters.volume;
        const xfadeParam = parameters.crossfade;
        const isOrbit = parameters.orbitEnabled[0] > 0.5;

        // Boucle sur chaque échantillon (128 par bloc)
        for (let i = 0; i < output[0].length; i++) {
            
            // 1. MIXAGE CROSSFADE (A vs B)
            const xfade = xfadeParam.length > 1 ? xfadeParam[i] : xfadeParam[0];
            const vol = volParam.length > 1 ? volParam[i] : volParam[0];

            // Equal Power Crossfade
            const gainA = Math.cos(xfade * 0.5 * Math.PI);
            const gainB = Math.sin(xfade * 0.5 * Math.PI);

            // Récupération des échantillons A et B
            let La = inputA.length > 0 ? inputA[0][i] : 0;
            let Ra = inputA.length > 1 ? inputA[1][i] : La;
            
            let Lb = inputB.length > 0 ? inputB[0][i] : 0;
            let Rb = inputB.length > 1 ? inputB[1][i] : Lb;

            // Mixage initial
            let L = (La * gainA) + (Lb * gainB);
            let R = (Ra * gainA) + (Rb * gainB);

            // 2. EFFET 8D ORBIT (Rotation Spatiale)
            if (isOrbit) {
                this.orbitPhase += this.orbitSpeed;
                if (this.orbitPhase > 2 * Math.PI) this.orbitPhase -= 2 * Math.PI;

                const pan = Math.sin(this.orbitPhase);
                const x = (pan + 1) / 2; // 0 à 1
                const angle = x * Math.PI / 2;
                
                // Panoramique à puissance constante
                L = L * Math.cos(angle);
                R = R * Math.sin(angle);
            }

            // 3. VOLUME MASTER & SORTIE
            output[0][i] = L * vol;
            if (channelCount > 1) {
                output[1][i] = R * vol;
            }
        }

        return true;
    }
}

registerProcessor('zenith-processor', ZenithProcessor);