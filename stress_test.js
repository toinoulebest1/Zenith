// stress_test.js - Simulation de charge sur l'API Python (No Supabase)

const StressTest = {
    isRunning: false,
    activeBots: 0,
    stats: { requests: 0, errors: 0, success: 0 },
    
    // Liste de termes pour varier les recherches
    terms: [
        "Da", "Love", "Night", "Sky", "Fire", "Blue", "Red", "Rock", "Pop", "Jazz", 
        "Weeknd", "Jackson", "Queen", "Beatles", "Daft", "Rap", "Piano", "Hans Zimmer",
        "Techno", "House", "Drake", "Eminem", "Rihanna", "Adele", "Coldplay", "Imagine",
        "Lofi", "Chill", "Gym", "Party", "Summer", "Winter", "Rain", "Sun"
    ],

    start: function(botCount) {
        if (this.isRunning) return;
        this.isRunning = true;
        this.activeBots = botCount;
        this.stats = { requests: 0, errors: 0, success: 0 };
        
        this.showOverlay();
        
        console.log(`🚀 STRESS TEST: Démarrage de ${botCount} bots...`);
        
        for (let i = 0; i < botCount; i++) {
            this.runBot(i);
        }
    },

    stop: function() {
        this.isRunning = false;
        this.activeBots = 0;
        console.log("🛑 STRESS TEST: Arrêt demandé.");
        const el = document.getElementById('stressOverlay');
        if(el) el.innerHTML = `<div style="color:#ff0055; font-weight:bold;">Test terminé.</div>`;
        setTimeout(() => { if(el) el.remove(); }, 3000);
    },

    runBot: async function(id) {
        // Délai initial aléatoire pour ne pas tout lancer à la milliseconde près
        await new Promise(r => setTimeout(r, Math.random() * 2000));

        while (this.isRunning) {
            try {
                // 1. RECHERCHE
                const term = this.terms[Math.floor(Math.random() * this.terms.length)];
                await this.makeRequest(`/search?q=${term}&type=track`);
                
                // Petite pause "humaine" (ou pas)
                if (!this.isRunning) break;
                await new Promise(r => setTimeout(r, 200 + Math.random() * 500));

                // 2. RECUPERATION TRACK INFO (Simulation d'un clic)
                // On utilise des IDs bidons ou on pourrait parser le résultat précédent, 
                // mais pour stresser le serveur, des appels random suffisent parfois.
                // Ici on va faire une requête Track sur un ID populaire pour tester le cache/fetch
                await this.makeRequest(`/track?id=5966783&source=qobuz`); 

                if (!this.isRunning) break;
                
                // 3. RECUPERATION PAROLES
                await this.makeRequest(`/lyrics?artist=${term}&title=Love&duration=200`);

                // Délai avant prochain cycle
                await new Promise(r => setTimeout(r, 1000 + Math.random() * 2000));

            } catch (e) {
                console.error(`Bot ${id} error:`, e);
            }
        }
    },

    makeRequest: async function(url) {
        if (!this.isRunning) return;
        const start = Date.now();
        try {
            const res = await fetch(url);
            this.stats.requests++;
            if (res.ok) this.stats.success++;
            else this.stats.errors++;
        } catch (e) {
            this.stats.requests++;
            this.stats.errors++;
        }
        this.updateOverlay();
    },

    showOverlay: function() {
        let el = document.getElementById('stressOverlay');
        if (!el) {
            el = document.createElement('div');
            el.id = 'stressOverlay';
            el.style.position = 'fixed';
            el.style.bottom = '20px';
            el.style.right = '20px';
            el.style.background = 'rgba(0,0,0,0.9)';
            el.style.border = '1px solid #ff0055';
            el.style.padding = '15px';
            el.style.borderRadius = '10px';
            el.style.zIndex = '10000';
            el.style.color = 'white';
            el.style.fontFamily = 'monospace';
            el.style.boxShadow = '0 0 20px rgba(255, 0, 85, 0.3)';
            document.body.appendChild(el);
        }
        this.updateOverlay();
    },

    updateOverlay: function() {
        const el = document.getElementById('stressOverlay');
        if (!el) return;
        el.innerHTML = `
            <div style="font-weight:bold; color:#ff0055; margin-bottom:5px;">⚠️ STRESS TEST ACTIF</div>
            <div>Bots: <span style="color:white">${this.activeBots}</span></div>
            <div>Reqs: <span style="color:cyan">${this.stats.requests}</span></div>
            <div>OK: <span style="color:#00ff88">${this.stats.success}</span></div>
            <div>Err: <span style="color:red">${this.stats.errors}</span></div>
            <button onclick="StressTest.stop()" style="margin-top:10px; background:#ff0055; color:white; border:none; padding:5px 10px; width:100%; cursor:pointer; font-weight:bold;">STOP</button>
        `;
    }
};