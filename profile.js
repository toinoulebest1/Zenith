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