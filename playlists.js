// playlists.js - Gestion des Playlists

let userPlaylists = [];
let currentPlaylistId = null;
let currentPlaylistOwnerId = null;

// --- 1. CHARGEMENT ---

async function loadUserPlaylists() {
    const { data: { user } } = await supabaseClient.auth.getUser();
    if (!user) return;

    // Charger les playlists
    const { data: playlists, error } = await supabaseClient
        .from('playlists')
        .select('*')
        .eq('user_id', user.id)
        .order('created_at', { ascending: false });

    if (playlists) {
        userPlaylists = playlists;
        
        // Optimisation: Pré-charger un aperçu des titres pour générer les pochettes
        // On charge les 4 premiers items de chaque playlist
        const playlistIds = playlists.map(p => p.id);
        
        if (playlistIds.length > 0) {
             const { data: items } = await supabaseClient
                .from('playlist_items')
                .select('playlist_id, track_data')
                .in('playlist_id', playlistIds);
             
             // Associer les items aux playlists
             if(items) {
                 userPlaylists.forEach(pl => {
                     pl.previewItems = items.filter(i => i.playlist_id === pl.id);
                 });
             }
        }

        if(document.getElementById('allPlaylistsView').style.display === 'block') {
            renderAllPlaylists();
        }
    }
}

// Helper pour sécuriser l'URL Subsonic
function getSafeSubsonicUrl(imagePath) {
    if (!imagePath) return '';
    if (imagePath.startsWith('http')) return imagePath;
    return `${API_BASE}/get_subsonic_cover/${imagePath}`;
}

// Génère le HTML de la pochette (1 image ou 4 images)
function getPlaylistCoverHTML(playlist) {
    const items = playlist.previewItems || [];
    
    // Pas d'items : Placeholder
    if(items.length === 0) {
        return `
        <div style="width:100%; aspect-ratio:1/1; background:linear-gradient(45deg, #333, #111); border-radius:12px; display:flex; align-items:center; justify-content:center; margin-bottom:10px;">
            <i class="fas fa-music" style="font-size:40px; color:#555;"></i>
        </div>`;
    }

    // Moins de 4 items : On prend la première image
    if(items.length < 4) {
        const t = items[0].track_data;
        let url = t.source === 'subsonic' ? getSafeSubsonicUrl(t.album.image.large) : (t.album.image.large || '').replace('_300', '_600');
        return `<img src="${url}" style="width:100%; aspect-ratio:1/1; border-radius:12px; object-fit:cover; margin-bottom:10px;">`;
    }

    // 4 items ou plus : Collage 2x2
    let collageHTML = `<div class="playlist-collage">`;
    for(let i=0; i<4; i++) {
        const t = items[i].track_data;
        let url = t.source === 'subsonic' ? getSafeSubsonicUrl(t.album.image.large) : (t.album.image.large || '').replace('_300', '_600');
        collageHTML += `<img src="${url}">`;
    }
    collageHTML += `</div>`;
    return collageHTML;
}

function renderAllPlaylists() {
    const container = document.getElementById('allPlaylistsGrid');
    if (!container) return;
    container.innerHTML = '';

    const createCard = document.createElement('div');
    createCard.className = 'track-card create-playlist-card';
    createCard.innerHTML = `
        <div style="width:100%; aspect-ratio:1/1; background:rgba(255,255,255,0.05); border-radius:12px; display:flex; align-items:center; justify-content:center; margin-bottom:10px; border: 2px dashed rgba(255,255,255,0.2);">
            <i class="fas fa-plus" style="font-size:30px; color:var(--primary);"></i>
        </div>
        <h3>Nouvelle Playlist</h3>
        <p>Créer</p>
    `;
    createCard.onclick = openCreatePlaylistModal;
    container.appendChild(createCard);

    userPlaylists.forEach(pl => {
        const div = document.createElement('div');
        div.className = 'track-card';
        
        const coverHTML = getPlaylistCoverHTML(pl);
        
        div.innerHTML = `
            ${coverHTML}
            <div class="badges-container">
                ${pl.is_public ? '<div class="type-badge" style="background:var(--primary)">PUBLIC</div>' : ''}
            </div>
            <h3>${pl.name}</h3>
            <p>${(pl.previewItems||[]).length} titres</p>
        `;
        div.onclick = () => openPlaylist(pl.id);
        container.appendChild(div);
    });
}

// --- 2. CRÉATION ---

function openCreatePlaylistModal() {
    document.getElementById('createPlaylistModal').style.display = 'flex';
    document.getElementById('newPlaylistName').focus();
}

function closeCreatePlaylistModal() {
    document.getElementById('createPlaylistModal').style.display = 'none';
}

async function createPlaylist() {
    const name = document.getElementById('newPlaylistName').value.trim();
    const isPublic = document.getElementById('newPlaylistPublic').checked;
    
    if (!name) return showToast("Nom requis !");
    
    const { data: { user } } = await supabaseClient.auth.getUser();
    
    const { data, error } = await supabaseClient
        .from('playlists')
        .insert({ user_id: user.id, name: name, is_public: isPublic })
        .select();

    if (error) {
        showToast("Erreur: " + error.message);
    } else {
        showToast("Playlist créée ! 💿");
        closeCreatePlaylistModal();
        document.getElementById('newPlaylistName').value = '';
        await loadUserPlaylists(); 
        renderAllPlaylists();
    }
}

// --- 3. ÉDITION ---

function openEditPlaylistModal() {
    const currentName = document.getElementById('playlistTitleMain').innerText;
    const isPublic = document.getElementById('playlistPublicBadge').innerText === 'PUBLIC';

    document.getElementById('editPlaylistName').value = currentName;
    document.getElementById('editPlaylistPublic').checked = isPublic;
    document.getElementById('editPlaylistModal').style.display = 'flex';
}

function closeEditPlaylistModal() {
    document.getElementById('editPlaylistModal').style.display = 'none';
}

async function savePlaylistEdits() {
    if (!currentPlaylistId) return;
    const newName = document.getElementById('editPlaylistName').value.trim();
    const newPublic = document.getElementById('editPlaylistPublic').checked;

    if(!newName) return showToast("Le nom ne peut pas être vide");

    const { error } = await supabaseClient
        .from('playlists')
        .update({ name: newName, is_public: newPublic })
        .eq('id', currentPlaylistId);

    if (error) {
        showToast("Erreur: " + error.message);
    } else {
        showToast("Playlist modifiée ! ✅");
        closeEditPlaylistModal();
        openPlaylist(currentPlaylistId);
        loadUserPlaylists(); 
    }
}

// --- 4. AFFICHAGE ET RECHERCHE ---

async function openPlaylist(id) {
    currentPlaylistId = id;
    
    document.getElementById('trackView').style.display = 'none';
    document.getElementById('artistView').style.display = 'none';
    document.getElementById('lyricsView').style.display = 'none';
    document.getElementById('blindTestView').style.display = 'none';
    document.getElementById('profileView').style.display = 'none';
    document.getElementById('allPlaylistsView').style.display = 'none';
    document.getElementById('searchBarContainer').style.display = 'none';
    
    const view = document.getElementById('playlistView');
    view.style.display = 'block';
    
    document.getElementById('playlistTitle').innerHTML = 'Chargement...';
    document.getElementById('playlistGrid').innerHTML = '<p style="text-align:center; padding:20px;">Récupération des titres...</p>';

    // 1. Info Playlist
    const { data: playlist } = await supabaseClient
        .from('playlists')
        .select('*')
        .eq('id', id)
        .single();
        
    const { data: { user } } = await supabaseClient.auth.getUser();
    const isOwner = user && playlist.user_id === user.id;

    if(playlist) {
        document.getElementById('playlistTitle').innerHTML = `
            <div style="display:flex; align-items:center; gap:10px;">
                <span id="playlistTitleMain">${playlist.name}</span>
                <span id="playlistPublicBadge" style="font-size:10px; background:rgba(255,255,255,0.1); padding:2px 6px; border-radius:4px;">${playlist.is_public ? 'PUBLIC' : 'PRIVÉ'}</span>
                ${isOwner ? `
                    <button onclick="openEditPlaylistModal()" class="action-btn-mini" title="Modifier"><i class="fas fa-pen"></i></button>
                    <button onclick="deletePlaylist('${id}')" class="action-btn-mini" style="color:#ff0055;" title="Supprimer"><i class="fas fa-trash"></i></button>
                ` : ''}
            </div>
        `;
    }

    // 2. Titres
    const { data: items } = await supabaseClient
        .from('playlist_items')
        .select('*')
        .eq('playlist_id', id)
        .order('added_at', { ascending: true });

    if(items && items.length > 0) {
        const trackList = items.map(i => i.track_data);
        renderPlaylistGrid(trackList, isOwner, id);
    } else {
        document.getElementById('playlistGrid').innerHTML = `
            <div style="text-align:center; padding:50px; color:#666;">
                <i class="fas fa-compact-disc" style="font-size:40px; margin-bottom:15px;"></i>
                <p>Cette playlist est vide.</p>
            </div>
        `;
    }
}

function renderPlaylistGrid(items, isOwner, playlistId) {
    const g = document.getElementById('playlistGrid');
    g.innerHTML = '';
    items.forEach(it => {
        let u = it.source === 'subsonic' ? getSafeSubsonicUrl(it.album.image.large) : (it.album.image.large || '').replace('_300', '_600');
        const d = document.createElement('div');
        d.className = 'track-card';
        // Important : Ajout de l'ID pour l'indicateur de lecture
        d.dataset.id = it.id; 

        // Gestion du badge Hi-Res
        let b = '';
        if(it.source !== 'subsonic' && it.maximum_bit_depth > 16) {
            b += '<div class="type-badge badge-hires">HI-RES</div>';
        }

        d.innerHTML = `
            <div class="badges-container">${b}</div>
            <img src="${u}">
            <h3>${it.title}</h3>
            <p>${it.performer.name}</p>
            ${isOwner ? `<button onclick="event.stopPropagation(); removeTrackFromPlaylist('${playlistId}', '${it.id}')" style="position:absolute; top:10px; right:10px; background:rgba(0,0,0,0.7); border:none; color:#ff0055; width:24px; height:24px; border-radius:50%; cursor:pointer;"><i class="fas fa-minus"></i></button>` : ''}
        `;
        d.onclick = () => {
            tracks = items; 
            const idx = items.findIndex(x => x.id === it.id);
            loadTrack(idx !== -1 ? idx : 0);
        };
        g.appendChild(d);
    });

    // Mise à jour de l'indicateur de lecture une fois le rendu terminé
    if (window.updateActiveCard) window.updateActiveCard();
}

async function removeTrackFromPlaylist(playlistId, trackId) {
    if(!confirm("Retirer ce titre ?")) return;
    
    const { data: items } = await supabaseClient
        .from('playlist_items')
        .select('id, track_data')
        .eq('playlist_id', playlistId);
        
    const itemToDelete = items.find(i => String(i.track_data.id) === String(trackId));
    
    if(itemToDelete) {
        await supabaseClient.from('playlist_items').delete().eq('id', itemToDelete.id);
        showToast("Titre retiré");
        openPlaylist(playlistId); // Refresh
    }
}

async function deletePlaylist(id) {
    if(!confirm("Supprimer définitivement cette playlist ?")) return;
    await supabaseClient.from('playlists').delete().eq('id', id);
    showToast("Playlist supprimée");
    loadUserPlaylists();
    showPlaylists();
}

// --- 5. RECHERCHE PUBLIQUE ---

async function searchPublicPlaylists(query) {
    // 1. Trouver les playlists
    const { data: playlists, error } = await supabaseClient
        .from('playlists')
        .select('*')
        .eq('is_public', true)
        .ilike('name', `%${query}%`)
        .limit(10);

    if (!playlists || playlists.length === 0) return [];

    const results = [];
    
    // 2. Pour chaque playlist, récupérer :
    //    - le pseudo du créateur (table profiles)
    //    - les images (table playlist_items)
    
    // On récupère tous les IDs nécessaires
    const userIds = [...new Set(playlists.map(p => p.user_id))];
    const playlistIds = playlists.map(p => p.id);

    // Requête Profiles
    const { data: profiles } = await supabaseClient
        .from('profiles')
        .select('id, username')
        .in('id', userIds);

    // Requête Items pour les pochettes
    const { data: items } = await supabaseClient
        .from('playlist_items')
        .select('playlist_id, track_data')
        .in('playlist_id', playlistIds);

    // Construction du résultat
    for (const pl of playlists) {
        // Trouver le pseudo
        const owner = profiles ? profiles.find(p => p.id === pl.user_id) : null;
        const ownerName = owner && owner.username ? owner.username : 'Utilisateur Inconnu';

        // Trouver les items pour cette playlist
        const plItems = items ? items.filter(i => i.playlist_id === pl.id) : [];
        
        // On utilise previewItems comme dans loadUserPlaylists pour que getPlaylistCoverHTML fonctionne
        results.push({
            id: pl.id,
            name: pl.name, // Pour l'affichage
            title: pl.name,
            performer: { name: ownerName }, // Pour l'affichage du créateur
            type: 'playlist', 
            previewItems: plItems, // Important pour getPlaylistCoverHTML
            source: 'local_playlist' 
        });
    }

    return results;
}

// --- AJOUT AVEC VERIFICATION DOUBLON ---
let trackToAddCandidate = null;
function openAddToPlaylistModal() {
    if(!tracks[currentIndex]) return showToast("Aucun titre en lecture");
    trackToAddCandidate = tracks[currentIndex];
    document.getElementById('addToPlaylistModal').style.display = 'flex';
    renderAddToModalList();
}
function closeAddToPlaylistModal() { document.getElementById('addToPlaylistModal').style.display = 'none'; }
function renderAddToModalList() {
    const container = document.getElementById('addToPlaylistList'); container.innerHTML = '';
    if(userPlaylists.length === 0) { container.innerHTML = '<p style="color:#888;">Aucune playlist.</p>'; return; }
    userPlaylists.forEach(pl => {
        const btn = document.createElement('div'); btn.className = 'playlist-select-item';
        btn.innerHTML = `<span>${pl.name}</span> <i class="fas fa-plus"></i>`;
        btn.onclick = () => addTrackToPlaylist(pl.id); container.appendChild(btn);
    });
}

async function addTrackToPlaylist(playlistId) {
    if(!trackToAddCandidate) return;

    // 1. Vérification Doublon
    const { data: existingItems } = await supabaseClient
        .from('playlist_items')
        .select('track_data')
        .eq('playlist_id', playlistId);
    
    if (existingItems) {
        const exists = existingItems.some(item => String(item.track_data.id) === String(trackToAddCandidate.id));
        if (exists) {
            showToast("⚠️ Ce titre est déjà dans la playlist !");
            return;
        }
    }

    // 2. Insertion
    const { error } = await supabaseClient.from('playlist_items').insert({ playlist_id: playlistId, track_data: trackToAddCandidate });
    if(error) showToast("Erreur : " + error.message); 
    else { 
        showToast(`Ajouté ! ✅`); 
        closeAddToPlaylistModal(); 
        loadUserPlaylists();
    }
}