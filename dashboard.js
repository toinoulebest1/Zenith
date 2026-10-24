// dashboard.js - Gestion de la page d'accueil dynamique

// `favorites` est déclaré avec `let` dans index.html : accessible ici par son nom, pas comme propriété de window
function getFavoriteTracks() {
    return (typeof favorites !== 'undefined' && Array.isArray(favorites)) ? favorites : [];
}

const Dashboard = {
    items: [], // Registre local pour lecture

    render: async function() {
        const container = document.getElementById('trackGrid');
        if (!container) return;

        // Reset
        this.items = [];

        // 1. Header (Message d'accueil)
        const now = new Date();
        const hour = now.getHours();
        let greeting = "Bonjour";
        if (hour >= 18) greeting = "Bonsoir";
        else if (hour < 5) greeting = "Bonne nuit";
        const dateLabel = now.toLocaleDateString('fr-FR', { weekday: 'long', day: 'numeric', month: 'long' });

        let username = "";
        try {
            const nameInput = document.getElementById('profileUsername');
            if (nameInput && nameInput.value && !nameInput.value.startsWith('Chargement')) username = nameInput.value;
        } catch(e) {}

        const history = window.userHistory || [];
        const favs = getFavoriteTracks();

        let html = `
            <div class="dashboard">
                <header class="dash-hero">
                    <div>
                        <p class="dash-eyebrow">${escapeHtml(dateLabel)}</p>
                        <h1 class="dash-greeting">${greeting}${username ? `, <span>${escapeHtml(username)}</span>` : ''}</h1>
                        <p class="dash-sub">${history.length ? 'Reprenez là où vous vous étiez arrêté.' : 'Que voulez-vous écouter aujourd\'hui ?'}</p>
                    </div>
                </header>

                <div class="dash-quick-grid">
                    ${this.buildTile('fa-heart', 'Favoris', favs.length ? `${favs.length} titre${favs.length > 1 ? 's' : ''}` : 'Vos coups de cœur', 'showFavorites()', 'linear-gradient(135deg,#ff5f8f,#b83280)')}
                    ${this.buildTile('fa-clock-rotate-left', 'Écoutés récemment', 'Vos 50 dernières écoutes', 'showHistory()', 'linear-gradient(135deg,#6d8dff,#3b4bd8)')}
                    ${this.buildTile('fa-layer-group', 'Playlists', 'Vos collections', 'showPlaylists()', 'linear-gradient(135deg,#34d399,#0f8a60)')}
                    ${this.buildTile('fa-trophy', 'Top 10 semaine', 'Les plus likés', 'showWeeklyTop()', 'linear-gradient(135deg,#fbbf24,#c46a06)')}
                    ${this.buildTile('fa-earth-europe', 'Top des pays', 'Classements mondiaux', 'showTopCountries()', 'linear-gradient(135deg,#38bdf8,#0b5f8f)')}
                    ${this.buildTile('fa-bookmark', 'Souvenirs', 'Vos moments épinglés', 'showMemories()', 'linear-gradient(135deg,#a78bfa,#6234d4)')}
                </div>
        `;

        // 2. REPRENDRE (Historique)
        if (history.length > 0) {
            html += this.buildSection("Reprendre l'écoute", 'fa-rotate-left', history.slice(0, 12));
        }

        // 3. FAVORIS (Aléatoire)
        if (favs.length > 0) {
            const shuffled = [...favs].sort(() => 0.5 - Math.random()).slice(0, 12);
            html += this.buildSection("Vos coups de cœur", 'fa-heart', shuffled);
        }

        // Premier lancement : rien à reprendre ni de favoris
        if (!history.length && !favs.length) {
            html += `
                <div class="dash-empty">
                    <i class="fas fa-wand-magic-sparkles"></i>
                    <div><strong>Bienvenue sur Zenith.</strong> Recherchez un titre ou un artiste : vos écoutes et vos favoris apparaîtront ici.</div>
                </div>`;
        }

        // 4. TENDANCES (RPC) : chargées après l'affichage pour ne pas retarder l'accueil
        html += `<div id="dashTrends"></div>`;

        // 5. EXPLORER (Cartes statiques)
        html += `
                <section class="dash-section">
                    <div class="dash-section-head"><h3 class="dash-section-title"><i class="fas fa-compass"></i> Explorer</h3></div>
                    <div class="dash-explore">
                        ${this.buildQuickCard("fa-wave-square", "Hi-Res Audio", "Qualité 24 bits", "searchHiRes()", 'linear-gradient(135deg,#a8741a,#4a2f07)')}
                        ${this.buildQuickCard("fa-compact-disc", "Nouveautés", "Albums de l'année", "searchNewReleases()", 'linear-gradient(135deg,#7c3aed,#2e0b5c)')}
                        ${this.buildQuickCard("fa-tower-broadcast", "Radio de vos favoris", "Flux infini", "startSmartRadio()", 'linear-gradient(135deg,#0ea5e9,#0b3a5c)')}
                        ${this.buildQuickCard("fa-gamepad", "Blind test", "Défiez vos oreilles", "startBlindTest()", 'linear-gradient(135deg,#e11d48,#5c1023)')}
                    </div>
                </section>
            </div>
        `;

        container.innerHTML = html;
        if (typeof updateActiveCard === 'function') updateActiveCard();

        try {
            if (typeof supabaseClient !== 'undefined' && supabaseClient) {
                const { data } = await supabaseClient.rpc('get_weekly_top_tracks');
                const slot = document.getElementById('dashTrends');
                if (slot && data && data.length > 0) {
                    const topTracks = data.map(item => item.track_data).slice(0, 12);
                    slot.outerHTML = this.buildSection("Tendances de la semaine", 'fa-fire', topTracks);
                    if (typeof updateActiveCard === 'function') updateActiveCard();
                }
            }
        } catch(e) { console.error("Dashboard trends error", e); }
    },

    buildTile: function(icon, title, subtitle, action, bg) {
        return `
            <div class="dash-tile" onclick="${action}">
                <div class="dash-tile-icon" style="--tile-bg:${bg}"><i class="fas ${icon}"></i></div>
                <div style="min-width:0"><strong>${title}</strong><span>${subtitle}</span></div>
            </div>
        `;
    },

    buildSection: function(title, icon, trackList) {
        if(!trackList || trackList.length === 0) return "";

        // Ajouter au registre pour pouvoir les jouer
        trackList.forEach(t => this.items.push(t));

        let html = `
            <section class="dash-section">
                <div class="dash-section-head">
                    <h3 class="dash-section-title"><i class="fas ${icon}"></i> ${title}</h3>
                    <div class="shelf-nav">
                        <button class="icon-btn" onclick="Dashboard.scrollRow(this, -1)" title="Précédent"><i class="fas fa-chevron-left"></i></button>
                        <button class="icon-btn" onclick="Dashboard.scrollRow(this, 1)" title="Suivant"><i class="fas fa-chevron-right"></i></button>
                    </div>
                </div>
                <div class="dashboard-scroll-row">
        `;

        trackList.forEach(t => {
            let img = t.img || (t.album && t.album.image && t.album.image.large) || 'https://placehold.co/300x300/17171d/3a3a46?text=%E2%99%AA';
            if(img.includes('_300')) img = img.replace('_300', '_600');

            const artist = t.performer ? t.performer.name : (t.artist ? t.artist.name : 'Artiste');

            html += `
                <div class="track-card dash-card" data-id="${t.id}" data-sig="${getTrackSignature(t)}" onclick="Dashboard.play('${t.id}')">
                    <div class="card-media">
                        <img src="${img}" loading="lazy" alt="">
                        <span class="card-play"><i class="fas fa-play"></i></span>
                    </div>
                    <h3>${escapeHtml(t.title)}</h3>
                    <p>${escapeHtml(artist)}</p>
                </div>
            `;
        });

        html += `</div></section>`;
        return html;
    },

    buildQuickCard: function(icon, title, subtitle, action, bg) {
        return `
            <div class="track-card dash-quick-card" style="--tile-bg:${bg}" onclick="${action}">
                <i class="fas ${icon}"></i>
                <strong>${title}</strong>
                <span>${subtitle}</span>
            </div>
        `;
    },

    scrollRow: function(btn, dir) {
        const row = btn.closest('.dash-section').querySelector('.dashboard-scroll-row');
        if (row) row.scrollBy({ left: dir * row.clientWidth * 0.85, behavior: 'smooth' });
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
    const favs = getFavoriteTracks();
    if(favs.length > 0) {
        const seed = favs[Math.floor(Math.random() * favs.length)];
        tracks = [seed];
        loadTrack(0);
        showToast(`Radio lancée depuis « ${seed.title} »`);
        setTimeout(() => triggerRadio(true), 1000);
    } else {
        showToast("Ajoutez des favoris pour lancer la radio !");
    }
}
