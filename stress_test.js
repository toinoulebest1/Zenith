// stress_test.js - Simulation de charge sur l'API Python (No Supabase)

const StressTest = {
    isRunning: false,
    activeBots: 0,
    stats: { requests: 0, errors: 0, success: 0, lastError: null },
    
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
        this.stats = { requests: 0, errors: 0, success: 0, lastError: null };
        
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
        // Délai initial aléatoire
        await new Promise(r => setTimeout(r, Math.random() * 2000));

        while (this.isRunning) {
            try {
                // 1. RECHERCHE
                const term = this.terms[Math.floor(Math.random() * this.terms.length)];
                
                // On récupère le JSON pour trouver un vrai ID
                const searchData = await this.makeRequest(`/search?q=${term}&type=track`, true);
                
                if (!this.isRunning) break;
                await new Promise(r => setTimeout(r, 200 + Math.random() * 500));

                if (searchData && searchData.tracks && searchData.tracks.length > 0) {
                    // On prend un track au hasard dans les résultats
                    const randomTrack = searchData.tracks[Math.floor(Math.random() * Math.min(5, searchData.tracks.length))];
                    const trackId = randomTrack.id;
                    const source = randomTrack.source || 'qobuz';
                    
                    // 2. RECUPERATION TRACK INFO (Avec un ID valide !)
                    await this.makeRequest(`/track?id=${trackId}&source=${source}`);

                    if (!this.isRunning) break;
                    
                    // 3. RECUPERATION PAROLES (Avec les vraies infos du titre)
                    // On encode proprement les paramètres
                    const artist = randomTrack.performer ? randomTrack.performer.name : (randomTrack.artist ? randomTrack.artist.name : "Unknown");
                    const title = randomTrack.title;
                    const duration = randomTrack.duration || 200;
                    
                    await this.makeRequest(`/lyrics?artist=${encodeURIComponent(artist)}&title=${encodeURIComponent(title)}&duration=${duration}`);
                } else {
                    // Si pas de résultat de recherche, on attend juste un peu
                    this.stats.lastError = "Search: No results found (Bot skipped cycle)";
                }

                // Délai avant prochain cycle
                await new Promise(r => setTimeout(r, 1000 + Math.random() * 2000));

            } catch (e) {
                console.error(`Bot ${id} error:`, e);
            }
        }
    },

    makeRequest: async function(url, returnJson = false) {
        if (!this.isRunning) return null;
        try {
            const res = await fetch(url);
            this.stats.requests++;
            
            if (res.ok) {
                this.stats.success++;
                if (returnJson) {
                    try { return await res.json(); } catch(e) { return null; }
                }
                return true;
            } else {
                this.stats.errors++;
                let msg = res.statusText;
                try {
                    const json = await res.json();
                    msg = json.detail || json.error || msg;
                } catch(e) {}
                
                // On ignore les 404 lyrics qui sont "normales" (pas de paroles trouvées)
                if (url.includes('/lyrics') && res.status === 404) {
                    // On ne log pas ça comme une erreur critique dans l'overlay, c'est juste la vie
                } else {
                    this.stats.lastError = `HTTP ${res.status}: ${msg}`;
                    console.warn(`StressTest Error: ${this.stats.lastError} (${url})`);
                }
                return null;
            }
        } catch (e) {
            this.stats.requests++;
            this.stats.errors++;
            this.stats.lastError = `Net: ${e.message}`;
            console.error(`StressTest Network Error:`, e);
            return null;
        }
        finally {
            this.updateOverlay();
        }
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
            el.style.maxWidth = '300px';
            document.body.appendChild(el);
        }
        this.updateOverlay();
    },

    updateOverlay: function() {
        const el = document.getElementById('stressOverlay');
        if (!el) return;
        
        let errorHtml = '';
        if (this.stats.lastError) {
            errorHtml = `
                <div style="margin-top:10px; padding-top:5px; border-top:1px solid #333; color:#ff5555; font-size:10px; word-break:break-word;">
                    Dernière erreur critique :<br>
                    ${this.stats.lastError}
                </div>
            `;
        }

        el.innerHTML = `
            <div style="font-weight:bold; color:#ff0055; margin-bottom:5px;">⚠️ STRESS TEST ACTIF</div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:5px; font-size:12px;">
                <div>Bots: <span style="color:white">${this.activeBots}</span></div>
                <div>Total: <span style="color:cyan">${this.stats.requests}</span></div>
                <div>OK: <span style="color:#00ff88">${this.stats.success}</span></div>
                <div>Err: <span style="color:red">${this.stats.errors}</span></div>
            </div>
            ${errorHtml}
            <button onclick="StressTest.stop()" style="margin-top:10px; background:#ff0055; color:white; border:none; padding:5px 10px; width:100%; cursor:pointer; font-weight:bold; border-radius:4px;">STOP</button>
        `;
    }
};