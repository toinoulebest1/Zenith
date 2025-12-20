// zenith-processor.js - VERSION "PASSTHROUGH" SÉCURISÉE
class ZenithProcessor extends AudioWorkletProcessor {
    static get parameterDescriptors() {
        return [
            { name: 'volume', defaultValue: 1.0, minValue: 0.0, maxValue: 1.0 },
            { name: 'crossfade', defaultValue: 0.0, minValue: 0.0, maxValue: 1.0 },
            { name: 'orbitEnabled', defaultValue: 0.0 }
        ];
    }

    constructor() {
        super();
        this.orbitPhase = 0;
        this.orbitSpeed = 0.02;
        this.lastGainA = 0;
        this.lastGainB = 0;
        this.lastXfade = -1; 
    }

    process(inputs, outputs, parameters) {
        const output = outputs[0];
        if (!output || output.length === 0) return true;

        const outL = output[0];
        const outR = output[1];
        const isStereoOut = !!outR;
        const bufferSize = outL.length;

        // Récupération sécurisée des paramètres
        const pVol = parameters.volume;
        const pXfade = parameters.crossfade;
        const pOrbit = parameters.orbitEnabled;
        
        // Valeurs par défaut si le tableau de paramètre est vide (sécurité)
        const vol = (pVol && pVol.length > 0) ? pVol[0] : 1.0;
        const xfade = (pXfade && pXfade.length > 0) ? pXfade[0] : 0.0;
        const isOrbit = (pOrbit && pOrbit.length > 0) ? (pOrbit[0] > 0.5) : false;

        const inputA = inputs[0];
        const inputB = inputs[1];

        // Gestion Entrée A
        const inputAL = (inputA && inputA.length > 0) ? inputA[0] : null;
        const inputAR = (inputA && inputA.length > 1) ? inputA[1] : inputAL;
        
        // Gestion Entrée B
        const inputBL = (inputB && inputB.length > 0) ? inputB[0] : null;
        const inputBR = (inputB && inputB.length > 1) ? inputB[1] : inputBL;

        // --- MODE PASSTHROUGH (SÉCURISÉ) ---
        // On ne copie que si les buffers existent ET ont la même taille que la sortie
        if (!isOrbit && Math.abs(vol - 1.0) < 0.001) {
            
            // CAS 1 : Piste A uniquement
            if (xfade <= 0.001) {
                if (inputAL && inputAL.length === bufferSize) {
                    outL.set(inputAL);
                } else {
                    outL.fill(0);
                }

                if (isStereoOut) {
                    if (inputAR && inputAR.length === bufferSize) {
                        outR.set(inputAR);
                    } else {
                        outR.fill(0);
                    }
                }
                return true; 
            }
            
            // CAS 2 : Piste B uniquement
            else if (xfade >= 0.999) {
                if (inputBL && inputBL.length === bufferSize) {
                    outL.set(inputBL);
                } else {
                    outL.fill(0);
                }

                if (isStereoOut) {
                    if (inputBR && inputBR.length === bufferSize) {
                        outR.set(inputBR);
                    } else {
                        outR.fill(0);
                    }
                }
                return true;
            }
        }

        // --- MODE MIXAGE / EFFETS (Boucle JS) ---
        
        // Calcul Gains Crossfade
        if (xfade !== this.lastXfade) {
            this.lastGainA = Math.cos(xfade * 0.5 * Math.PI);
            this.lastGainB = Math.sin(xfade * 0.5 * Math.PI);
            this.lastXfade = xfade;
        }
        let gainA = this.lastGainA;
        let gainB = this.lastGainB;

        // Calcul Orbit
        let orbitL = 1, orbitR = 1;
        if (isOrbit) {
            this.orbitPhase += this.orbitSpeed;
            if (this.orbitPhase > 2 * Math.PI) this.orbitPhase -= 2 * Math.PI;
            const pan = Math.sin(this.orbitPhase);
            const x = (pan + 1) / 2;
            const angle = x * Math.PI / 2;
            orbitL = Math.cos(angle);
            orbitR = Math.sin(angle);
        }

        // Boucle principale
        const isVolAutomated = pVol.length > 1;
        
        for (let i = 0; i < bufferSize; i++) {
            const currentVol = isVolAutomated ? pVol[i] : vol;
            
            // Lecture safe (éviter undefined)
            const saL = inputAL ? inputAL[i] : 0;
            const saR = inputAR ? inputAR[i] : saL;
            const sbL = inputBL ? inputBL[i] : 0;
            const sbR = inputBR ? inputBR[i] : sbL;

            // Mix
            let L = (saL * gainA) + (sbL * gainB);
            let R = (saR * gainA) + (sbR * gainB);

            // Orbit
            if (isOrbit) {
                L *= orbitL;
                R *= orbitR;
            }

            // Écriture (Protection NaN)
            const valL = L * currentVol;
            const valR = R * currentVol;
            
            outL[i] = isFinite(valL) ? valL : 0;
            if (isStereoOut) outR[i] = isFinite(valR) ? valR : 0;
        }

        return true;
    }
}

registerProcessor('zenith-processor', ZenithProcessor);