// dashboard.js - Gestion de la page d'accueil dynamique

const Dashboard = {
    items: [], // Registre local pour lecture

    render: async function() {
        const container = document.getElementById('trackGrid');
        if (!container) return;
        
        container.innerHTML = '<div style="grid-column:1/-1;text-align:center;padding:50px;"><i class="fas fa-circle-notch fa-spin" style="font-size:30px; color:var(--primary);"></i><p style="margin-top:10px;">Chargement de votre espace...</p></div>';
        
        // Reset
        this.items = [];

        // 1. Header (Message d'accueil)
        const hour = new Date().getHours();
        let greeting = "Bonjour";
        if(hour >= 18) greeting = "Bonsoir";
        else if(hour < 5) greeting = "Bonne nuit";
        
        let username = "Mélomane";
        try {
            const nameInput = document.getElementById('profileUsername');
            if(nameInput && nameInput.value) username = nameInput.value;
        } catch(e) {}

        let html = `
            <div style="grid-column: 1/-1; padding: 10px 0 20px 0;">
                <h2 style="font-size: 2rem; margin-bottom: 5px; background:linear-gradient(to right, #fff, #aaa); -webkit-background-clip:text; -webkit-text-fill-color:transparent;">${greeting}, ${username}</h2>
                <p style="color: var(--text-dim); font-size: 1rem;">Votre flux personnel</p>
            </div>
        `;

        // 2. REPRENDRE (Historique)
        if (window.userHistory && window.userHistory.length > 0) {
            html += this.buildSection("Reprendre la lecture ↺", window.userHistory.slice(0, 10));
        }

        // 3. FAVORIS (Aléatoire)
        if (window.favorites && window.favorites.length > 0) {
            const shuffled = [...window.favorites].sort(() => 0.5 - Math.random()).slice(0, 10);
            html += this.buildSection("Vos coups de cœur ❤️", shuffled);
        }

        // 4. TENDANCES (RPC)
        try {
            if (typeof supabaseClient !== 'undefined') {
                const { data } = await supabaseClient.rpc('get_weekly_top_tracks');
                if (data && data.length > 0) {
                     const topTracks = data.map(item => item.track_data).slice(0, 10);
                     html += this.buildSection("Tendances de la semaine 🔥", topTracks);
                }
            }
        } catch(e) { console.error("Dashboard trends error", e); }

        // 5. EXPLORER (Cartes statiques)
        html += `
            <div style="grid-column: 1/-1; margin-top: 20px;">
                <h3 class="dash-section-title">Explorer</h3>
                <div class="dashboard-scroll-row">
                    ${this.buildQuickCard("fa-wave-square", "Hi-Res Audio", "24-bit Quality", "searchHiRes()")}
                    ${this.buildQuickCard("fa-compact-disc", "Nouveautés", "Albums Récents", "searchNewReleases()")}
                    ${this.buildQuickCard("fa-trophy", "Top 10", "Classement", "showWeeklyTop()")}
                    ${this.buildQuickCard("fa-random", "Radio Mode", "Flux Infini", "startSmartRadio()")}
                </div>
            </div>
        `;
        
        // Spacer pour le bas de page (mobile)
        html += `<div style="grid-column: 1/-1; height: 100px;"></div>`;

        container.innerHTML = html;
    },

    buildSection: function(title, trackList) {
        if(!trackList || trackList.length === 0) return "";
        
        // Ajouter au registre pour pouvoir les jouer
        trackList.forEach(t => this.items.push(t));

        let html = `
            <div style="grid-column: 1/-1; margin-bottom: 25px;">
                <h3 class="dash-section-title">${title}</h3>
                <div class="dashboard-scroll-row">
        `;

        trackList.forEach(t => {
            let img = t.img || (t.album && t.album.image && t.album.image.large) || 'https://placehold.co/300x300/1a1a1a/666666?text=Music';
            if(img.includes('_300')) img = img.replace('_300', '_600');
            
            const artist = t.performer ? t.performer.name : (t.artist ? t.artist.name : 'Artiste');
            
            html += `
                <div class="track-card dash-card" onclick="Dashboard.play('${t.id}')">
                    <img src="${img}" loading="lazy">
                    <h3 style="font-size: 14px; margin-top: 8px;">${t.title}</h3>
                    <p style="font-size: 12px; color: #888;">${artist}</p>
                </div>
            `;
        });

        html += `</div></div>`;
        return html;
    },

    buildQuickCard: function(icon, title, subtitle, action) {
        return `
            <div class="track-card dash-quick-card" onclick="${action}">
                <div class="dash-icon-circle">
                    <i class="fas ${icon}"></i>
                </div>
                <h3 style="text-align:center; font-size:14px;">${title}</h3>
                <p style="text-align:center; font-size:11px; color:#666;">${subtitle}</p>
            </div>
        `;
    },

    play: function(id) {
        const track = this.items.find(t => String(t.id) === String(id));
        if (track) {
            // On joue le titre cliqué
            tracks = [track];
            loadTrack(0);
            
            // On prépare la radio pour la suite
            setTimeout(() => triggerRadio(true), 1000);
        }
    }
};

// Fonctions Helper
function searchHiRes() {
    document.getElementById('searchInput').value = "Hi-Res";
    performSearch();
}

function searchNewReleases() {
     const year = new Date().getFullYear();
     document.getElementById('searchInput').value = `${year}`;
     document.getElementById('searchType').value = "album";
     performSearch();
}

function startSmartRadio() {
    if(window.favorites && window.favorites.length > 0) {
        const seed = window.favorites[Math.floor(Math.random() * window.favorites.length)];
        tracks = [seed];
        loadTrack(0);
        showToast(`Radio lancée depuis "${seed.title}" 📻`);
        setTimeout(() => triggerRadio(true), 1000);
    } else {
        showToast("Ajoutez des favoris pour lancer la radio !");
    }
}