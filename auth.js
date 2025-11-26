// auth.js - Gestion de l'authentification Supabase

// Configuration Supabase (Mise à jour avec les infos de la branche actuelle)
const SUPABASE_URL = 'https://mzxfcvzqxgslyopkkaej.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im16eGZjdnpxeGdzbHlvcGtrYWVqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM3NTg2ODAsImV4cCI6MjA3OTMzNDY4MH0.xUvrW-TmUBl6eQIxRWbdItkW9xPtsalFNo0ICY-6A_o';

let supabaseClient = null;

function initAuth() {
    if (typeof window.supabase === 'undefined') {
        console.error("Supabase lib not loaded");
        return;
    }
    
    supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
    
    // Vérification des erreurs dans l'URL au retour d'OAuth
    checkUrlForErrors();

    // Écouter les changements d'état (Connexion, Déconnexion)
    supabaseClient.auth.onAuthStateChange((event, session) => {
        console.log("Auth Event:", event);
        handleSession(session);
    });
}

function checkUrlForErrors() {
    const params = new URLSearchParams(window.location.search);
    const hashParams = new URLSearchParams(window.location.hash.substring(1)); // Supprime le #
    
    const error = params.get('error') || hashParams.get('error');
    const errorDesc = params.get('error_description') || hashParams.get('error_description');
    const errorCode = params.get('error_code') || hashParams.get('error_code');

    if (error) {
        console.error("Auth Error Detected:", error, errorDesc);
        
        let userMessage = "Erreur de connexion.";
        
        // GESTION DES ERREURS SPOTIFY COMMUNES
        if (errorCode === 'provider_email_needs_verification' || (errorDesc && errorDesc.includes('Unverified email'))) {
            userMessage = "⚠️ Email non vérifié chez Spotify. Consultez votre boîte mail (et les spams) pour valider votre compte Spotify.";
        } 
        else if (error === 'server_error' && errorDesc && errorDesc.includes('user profile')) {
            userMessage = "⚠️ Erreur Spotify Dev : Avez-vous ajouté votre email dans 'User Management' sur le Dashboard Spotify ?";
        }
        else if (errorDesc) {
            userMessage = "⚠️ " + errorDesc.replace(/\+/g, ' ');
        }

        // On attend un peu que l'UI soit chargée pour afficher le toast
        setTimeout(() => {
            if (typeof showToast === 'function') showToast(userMessage);
            else alert(userMessage);
            
            // Nettoyage de l'URL pour ne pas réafficher l'erreur au refresh
            window.history.replaceState({}, document.title, window.location.pathname);
        }, 1000);
    }
}

function handleSession(session) {
    const loginScreen = document.getElementById('loginScreen');
    const appLayout = document.querySelector('.app-layout');
    
    if (session) {
        // Utilisateur connecté
        if (loginScreen) loginScreen.style.display = 'none';
        if (appLayout) {
            appLayout.style.display = ''; 
            appLayout.classList.add('layout-visible');
        }
        
        // TENTATIVE DE SYNC SPOTIFY (Favoris + Profil)
        if (session.user && session.user.app_metadata.provider === 'spotify') {
            syncSpotifyProfileData(session.user);
            
            if (session.provider_token) {
                syncSpotifyFavorites(session.provider_token);
            }
        }
        
        // CHARGEMENT DES DONNÉES UTILISATEUR
        if (window.loadUserFavorites) {
            window.loadUserFavorites();
        }
        
        updateUserProfile(session.user);
    } else {
        // Utilisateur déconnecté
        if (loginScreen) loginScreen.style.display = 'flex';
        if (appLayout) {
            appLayout.classList.remove('layout-visible');
        }
    }
}

// Nouvelle fonction : Sync Avatar et Pseudo depuis Spotify
async function syncSpotifyProfileData(user) {
    try {
        const { avatar_url, full_name, name, picture } = user.user_metadata;
        const displayName = full_name || name;
        // Spotify renvoie parfois 'picture' ou 'avatar_url' via Supabase
        const avatar = avatar_url || picture; 

        if (!displayName && !avatar) return;

        // On vérifie d'abord si le profil est déjà rempli pour ne pas écraser inutilement
        // Mais si c'est la première connexion ou si c'est "Anonyme", on met à jour.
        const { data: currentProfile } = await supabaseClient
            .from('profiles')
            .select('username, avatar_url')
            .eq('id', user.id)
            .single();
            
        const updates = {
            id: user.id,
            updated_at: new Date()
        };
        let hasChanges = false;

        // Mise à jour du pseudo si vide ou générique
        if (displayName && (!currentProfile || !currentProfile.username || currentProfile.username === 'Anonyme' || currentProfile.username === user.email)) {
            updates.username = displayName;
            hasChanges = true;
        }
        
        // Mise à jour de l'avatar si vide
        if (avatar && (!currentProfile || !currentProfile.avatar_url)) {
            updates.avatar_url = avatar;
            hasChanges = true;
        }

        // Si on a détecté des infos utiles à sauvegarder
        if (hasChanges) {
            console.log("🔄 Syncing Profile from Spotify metadata...");
            const { error } = await supabaseClient.from('profiles').upsert(updates);
            
            if (!error) {
                // Rafraîchir l'interface immédiatement si possible
                if (window.loadProfile) window.loadProfile();
                showToast("Profil synchronisé avec Spotify ! 🎧");
            } else {
                console.error("Profile update error:", error);
            }
        }
    } catch (e) {
        console.error("Spotify Profile Sync Error:", e);
    }
}

// Fonction d'importation Favoris Spotify
async function syncSpotifyFavorites(token) {
    console.log("🔄 Syncing Spotify Favorites...");
    try {
        const response = await fetch('https://api.spotify.com/v1/me/tracks?limit=50', {
            headers: { 'Authorization': 'Bearer ' + token }
        });

        if (!response.ok) throw new Error("Spotify API Error");
        const data = await response.json();

        if (data.items) {
            const spotifyTracks = data.items.map(item => {
                const t = item.track;
                return {
                    id: t.id,
                    title: t.name,
                    performer: { name: t.artists[0].name },
                    album: { title: t.album.name, image: { large: t.album.images[0]?.url } },
                    source: 'spotify_lazy',
                    duration: t.duration_ms / 1000,
                    imported_from: 'spotify'
                };
            });

            if (typeof favorites !== 'undefined') {
                let newCount = 0;
                spotifyTracks.forEach(st => {
                     const exists = favorites.some(f => 
                        f.title.toLowerCase() === st.title.toLowerCase() && 
                        f.performer.name.toLowerCase() === st.performer.name.toLowerCase()
                     );
                     if(!exists) {
                         favorites.push(st);
                         newCount++;
                     }
                });
                
                const viewTitle = document.getElementById('viewTitle');
                if(viewTitle && viewTitle.innerText.includes('Favoris')) {
                    if(typeof showFavorites === 'function') showFavorites();
                }
                
                if(newCount > 0) {
                    console.log(`✅ ${newCount} nouveaux titres Spotify importés.`);
                    showToast(`✅ ${newCount} titres Spotify importés !`);
                }
            }
        }
    } catch (e) {
        console.error("Spotify Sync Error:", e);
    }
}

async function loginWithSpotify() {
    showAuthLoading(true);
    const redirectUrl = window.location.origin; 
    console.log("OAuth Redirect To:", redirectUrl);

    const { data, error } = await supabaseClient.auth.signInWithOAuth({
        provider: 'spotify',
        options: {
            scopes: 'user-library-read',
            redirectTo: redirectUrl
        }
    });
    
    if (error) {
        showAuthLoading(false);
        showAuthError(error.message);
    }
}

async function login(email, password) {
    showAuthLoading(true);
    const { data, error } = await supabaseClient.auth.signInWithPassword({
        email: email,
        password: password,
    });
    showAuthLoading(false);
    
    if (error) {
        showAuthError(error.message);
    }
}

async function signup(email, password) {
    const usernameInput = document.getElementById('username');
    const username = usernameInput ? usernameInput.value.trim() : "";

    if (!username) {
        showAuthError("Merci de choisir un pseudo !");
        return;
    }

    showAuthLoading(true);
    
    const { data, error } = await supabaseClient.auth.signUp({
        email: email,
        password: password,
        options: {
            data: {
                username: username
            }
        }
    });
    
    showAuthLoading(false);

    if (error) {
        showAuthError(error.message);
    } else {
        if (data && data.user) {
            try {
                await supabaseClient.from('profiles').upsert({
                    id: data.user.id,
                    username: username,
                    updated_at: new Date()
                });
            } catch (e) {
                console.error("Erreur save profile:", e);
            }
        }
        showAuthError("Inscription réussie ! Vérifiez vos emails si nécessaire.", true);
    }
}

async function logout() {
    const { error } = await supabaseClient.auth.signOut();
    if (error) console.error('Error logging out:', error);
}

// UI Helpers
function showAuthError(msg, isSuccess = false) {
    const el = document.getElementById('authMessage');
    if (el) {
        el.innerText = msg;
        el.style.color = isSuccess ? '#00d2ff' : '#ff0055';
        el.style.opacity = 1;
    }
}

function showAuthLoading(isLoading) {
    const btn = document.getElementById('btnLoginAction');
    const spotifyBtn = document.getElementById('btnSpotifyAction');
    
    if(btn) btn.innerText = isLoading ? '...' : (isLoginMode ? 'Se connecter' : "S'inscrire");
    if(spotifyBtn && isLoading) spotifyBtn.style.opacity = 0.5;
}

function updateUserProfile(user) {
    console.log("Logged in as:", user.email);
}

let isLoginMode = true;
function toggleAuthMode() {
    isLoginMode = !isLoginMode;
    const userField = document.getElementById('username');
    if (userField) {
        userField.style.display = isLoginMode ? 'none' : 'block';
        if (!isLoginMode) userField.focus();
    }
    document.getElementById('authTitle').innerText = isLoginMode ? 'Connexion' : 'Inscription';
    document.getElementById('btnLoginAction').innerText = isLoginMode ? 'Se connecter' : "S'inscrire";
    document.getElementById('btnToggleMode').innerHTML = isLoginMode ? 'Pas de compte ? <b>Créer un compte</b>' : 'Déjà un compte ? <b>Se connecter</b>';
    document.getElementById('authMessage').innerText = '';
}

document.addEventListener('DOMContentLoaded', initAuth);