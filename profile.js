// profile.js - Gestion du profil et upload d'avatar

// OPTIMISATION EGRESS : Utilisation d'une image externe (Placehold.co) au lieu de Supabase Storage
// Cela évite de consommer votre quota "Egress" à chaque chargement de page.
const DEFAULT_AVATAR = "https://placehold.co/400x400/1a1a1a/666666?text=U";

async function loadProfile() {
    const { data: { user } } = await supabaseClient.auth.getUser();
    if (!user) return;

    // Afficher l'état de chargement
    document.getElementById('profileUsername').value = "Chargement...";

    try {
        const { data, error } = await supabaseClient
            .from('profiles')
            .select('username, avatar_url')
            .eq('id', user.id)
            .single();

        if (error) throw error;

        if (data) {
            document.getElementById('profileUsername').value = data.username || '';
            
            // Si l'utilisateur a un avatar, on l'utilise, sinon on met l'image par défaut
            const avatarUrl = data.avatar_url ? data.avatar_url : DEFAULT_AVATAR;
            document.getElementById('profileAvatarPreview').src = avatarUrl;
        }
    } catch (error) {
        console.error('Erreur chargement profil:', error.message);
    }
}

async function updateProfile() {
    const { data: { user } } = await supabaseClient.auth.getUser();
    if (!user) return showToast("Vous devez être connecté");

    const username = document.getElementById('profileUsername').value;
    const avatarFile = document.getElementById('profileAvatarInput').files[0];
    const btn = document.getElementById('btnSaveProfile');
    
    btn.disabled = true;
    btn.innerText = "Sauvegarde...";

    // On récupère l'URL actuelle (soit celle qu'on vient de charger, soit le défaut)
    let avatar_url = document.getElementById('profileAvatarPreview').src;

    try {
        // 1. Upload de l'image si une nouvelle est sélectionnée
        if (avatarFile) {
            // Optimisation : On supprime l'ancien avatar s'il existait pour économiser le stockage (optionnel mais recommandé)
            
            const fileExt = avatarFile.name.split('.').pop();
            const fileName = `${user.id}-${Math.random()}.${fileExt}`;
            const filePath = `${fileName}`;

            const { error: uploadError } = await supabaseClient.storage
                .from('avatars')
                .upload(filePath, avatarFile);

            if (uploadError) throw uploadError;

            // Récupération de l'URL publique
            const { data: { publicUrl } } = supabaseClient.storage
                .from('avatars')
                .getPublicUrl(filePath);
            
            avatar_url = publicUrl;
        }

        // 2. Mise à jour de la table profiles
        const { error } = await supabaseClient
            .from('profiles')
            .upsert({
                id: user.id,
                username: username,
                avatar_url: avatar_url,
                updated_at: new Date()
            });

        if (error) throw error;

        showToast("Profil mis à jour ! ✅");
        
        // Mise à jour visuelle immédiate
        document.getElementById('profileAvatarPreview').src = avatar_url;
        
    } catch (error) {
        showToast("Erreur: " + error.message);
        console.error(error);
    } finally {
        btn.disabled = false;
        btn.innerText = "Enregistrer les modifications";
    }
}

// Outil de réparation pour les favoris pollués par le Party Mode
// VERSION OPTIMISÉE ET ROBUSTE
async function fixSpotifyTags() {
    if (!confirm("Nettoyer les badges Spotify erronés ? Cela retirera le logo Spotify de vos favoris actuels.")) return;

    const btn = document.getElementById('btnFixSpotify');
    if(btn) { btn.disabled = true; btn.innerText = "Traitement en cours..."; }

    try {
        const { data: { user } } = await supabaseClient.auth.getUser();
        if (!user) return;

        // 1. Récupérer tous les favoris (Attention Egress : On ne récupère que ce qui est nécessaire)
        const { data: favs, error } = await supabaseClient
            .from('favorites')
            .select('id, track_data') 
            .eq('user_id', user.id);

        if (error) throw error;

        // 2. Préparer les mises à jour en parallèle
        const updates = [];
        
        for (const fav of favs) {
            // Si le titre est marqué comme importé de Spotify
            if (fav.track_data && fav.track_data.imported_from === 'spotify') {
                const cleanData = { ...fav.track_data };
                delete cleanData.imported_from; // On retire le tag
                
                // On ajoute la promesse de mise à jour
                updates.push(
                    supabaseClient
                        .from('favorites')
                        .update({ track_data: cleanData })
                        .eq('id', fav.id)
                );
            }
        }

        if (updates.length > 0) {
            await Promise.all(updates);
            showToast(`✅ ${updates.length} titres nettoyés !`);
            
            // 3. Rafraîchir l'interface après un court délai pour la propagation
            setTimeout(() => {
                if (window.loadUserFavorites) window.loadUserFavorites();
            }, 1000);
        } else {
            showToast("Aucun titre nécessitant une correction.");
        }

    } catch (e) {
        console.error(e);
        showToast("Erreur lors du nettoyage.");
    } finally {
        if(btn) { btn.disabled = false; btn.innerText = "Réparer les badges Spotify"; }
    }
}

// --- MIGRATION TIDAL (OLD -> NEW) ---
async function migrateTidalLegacy() {
    if (!confirm("Cette opération va scanner tous vos favoris et playlists pour mettre à jour les anciens liens Tidal vers le nouveau système. Cela peut prendre du temps. Continuer ?")) return;

    const btn = document.getElementById('btnMigrateTidal');
    const originalText = btn.innerText;
    btn.disabled = true;

    try {
        const { data: { user } } = await supabaseClient.auth.getUser();
        if (!user) return;

        let totalUpdated = 0;
        let errors = 0;

        // --- 1. MIGRATION DES FAVORIS ---
        btn.innerText = "Scan Favoris...";
        const { data: favorites } = await supabaseClient
            .from('favorites')
            .select('id, track_data')
            .eq('user_id', user.id);

        if (favorites) {
            // Filtrer les anciens Tidal (source === 'tidal' ou source manquante mais ID numérique typique Tidal)
            const candidates = favorites.filter(f => f.track_data && (f.track_data.source === 'tidal' || f.track_data.source === 'tidal_old'));
            
            for (let i = 0; i < candidates.length; i++) {
                const item = candidates[i];
                btn.innerText = `Favoris: ${i + 1}/${candidates.length}`;
                
                try {
                    // Appel API pour résoudre avec le nouveau système
                    const res = await fetch(`${API_BASE}/resolve_metadata?title=${encodeURIComponent(item.track_data.title)}&artist=${encodeURIComponent(item.track_data.performer ? item.track_data.performer.name : item.track_data.artist)}`);
                    if (res.ok) {
                        const newData = await res.json();
                        // On garde les infos de date d'ajout si elles existent
                        if(item.track_data.added_at) newData.added_at = item.track_data.added_at;
                        
                        await supabaseClient
                            .from('favorites')
                            .update({ track_data: newData })
                            .eq('id', item.id);
                        totalUpdated++;
                    } else {
                        errors++;
                        console.warn("Échec résolution:", item.track_data.title);
                    }
                } catch (e) {
                    errors++;
                    console.error(e);
                }
                // Petit délai pour ne pas spammer l'API
                await new Promise(r => setTimeout(r, 200)); 
            }
        }

        // --- 2. MIGRATION DES PLAYLISTS ---
        btn.innerText = "Scan Playlists...";
        
        // On récupère les playlists de l'user
        const { data: playlists } = await supabaseClient
            .from('playlists')
            .select('id')
            .eq('user_id', user.id);
            
        if (playlists && playlists.length > 0) {
            const playlistIds = playlists.map(p => p.id);
            
            // On récupère les items
            const { data: items } = await supabaseClient
                .from('playlist_items')
                .select('id, track_data')
                .in('playlist_id', playlistIds);
                
            if (items) {
                const plCandidates = items.filter(f => f.track_data && (f.track_data.source === 'tidal' || f.track_data.source === 'tidal_old'));
                
                for (let i = 0; i < plCandidates.length; i++) {
                    const item = plCandidates[i];
                    btn.innerText = `Playlists: ${i + 1}/${plCandidates.length}`;
                    
                    try {
                        const res = await fetch(`${API_BASE}/resolve_metadata?title=${encodeURIComponent(item.track_data.title)}&artist=${encodeURIComponent(item.track_data.performer ? item.track_data.performer.name : item.track_data.artist)}`);
                        if (res.ok) {
                            const newData = await res.json();
                            if(item.track_data.added_at) newData.added_at = item.track_data.added_at;

                            await supabaseClient
                                .from('playlist_items')
                                .update({ track_data: newData })
                                .eq('id', item.id);
                            totalUpdated++;
                        } else {
                            errors++;
                        }
                    } catch (e) {
                        errors++;
                    }
                    await new Promise(r => setTimeout(r, 200));
                }
            }
        }

        showToast(`Migration terminée ! ✅ ${totalUpdated} titres mis à jour.`);
        if (window.loadUserFavorites) window.loadUserFavorites(); // Refresh UI

    } catch (e) {
        console.error("Erreur migration:", e);
        showToast("Erreur durant la migration");
    } finally {
        btn.disabled = false;
        btn.innerText = originalText;
    }
}

// Déclencheur pour l'input fichier caché
function triggerAvatarUpload() {
    document.getElementById('profileAvatarInput').click();
}

// Prévisualisation locale immédiate au changement de fichier
document.getElementById('profileAvatarInput').addEventListener('change', function(event) {
    const file = event.target.files[0];
    if (file) {
        const reader = new FileReader();
        reader.onload = function(e) {
            document.getElementById('profileAvatarPreview').src = e.target.result;
        }
        reader.readAsDataURL(file);
    }
});