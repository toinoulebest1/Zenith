// profile.js - Gestion du profil et upload d'avatar

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
            if (data.avatar_url) {
                document.getElementById('profileAvatarPreview').src = data.avatar_url;
                // Mise à jour aussi dans la sidebar si on veut
            }
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