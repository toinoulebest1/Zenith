// profile.js - Gestion du profil et upload d'avatar

const DEFAULT_AVATAR = "https://mzxfcvzqxgslyopkkaej.supabase.co/storage/v1/object/public/avatar_nopersonalize/avatarno_personalize.png";

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
async function fixSpotifyTags() {
    if (!confirm("Cette action va retirer le badge 'Spotify' de vos favoris actuels pour corriger les erreurs d'affichage et permettre leur suppression. Continuer ?")) return;

    const btn = document.getElementById('btnFixSpotify');
    if(btn) { btn.disabled = true; btn.innerText = "Nettoyage en cours..."; }

    try {
        const { data: { user } } = await supabaseClient.auth.getUser();
        if (!user) return;

        // 1. Récupérer tous les favoris
        const { data: favs, error } = await supabaseClient
            .from('favorites')
            .select('*')
            .eq('user_id', user.id);

        if (error) throw error;

        let count = 0;

        // 2. Parcourir et nettoyer
        for (const fav of favs) {
            // Si le titre est marqué comme importé de Spotify
            if (fav.track_data && fav.track_data.imported_from === 'spotify') {
                const cleanData = { ...fav.track_data };
                delete cleanData.imported_from; // On retire le tag
                
                // On met à jour la ligne en base
                await supabaseClient
                    .from('favorites')
                    .update({ track_data: cleanData })
                    .eq('id', fav.id);
                
                count++;
            }
        }

        showToast(`✅ Terminé ! ${count} titres corrigés.`);
        
        // 3. Rafraîchir l'interface
        if (window.loadUserFavorites) window.loadUserFavorites();

    } catch (e) {
        console.error(e);
        showToast("Erreur lors du nettoyage.");
    } finally {
        if(btn) { btn.disabled = false; btn.innerText = "Réparer les badges Spotify (Party Bug)"; }
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