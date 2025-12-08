// auth.js - Gestion de l'authentification Supabase

// Configuration Supabase (Mise à jour avec les infos de la branche actuelle)
const SUPABASE_URL = 'https://mzxfcvzqxgslyopkkaej.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im16eGZjdnpxeGdzbHlvcGtrYWVqIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjM3NTg2ODAsImV4cCI6MjA3OTMzNDY4MH0.xUvrW-TmUBl6eQIxRWbdItkW9xPtsalFNo0ICY-6A_o';

let supabaseClient = null;
let currentUserId = null; // Pour éviter les rechargements inutiles
let hasSyncedSpotifySession = false; 

async function initAuth() {
    console.log("initAuth: Starting authentication initialization.");
    if (typeof window.supabase === 'undefined') {
        console.error("Supabase lib not loaded");
        return;
    }
    
    supabaseClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
    
    checkUrlForErrors();

    // 1. Vérifier et gérer la session initiale immédiatement
    const { data: { session } } = await supabaseClient.auth.getSession();
    console.log("initAuth: Initial session check result:", session);
    handleSession(session);

    // 2. Écouter les changements d'état futurs
    supabaseClient.auth.onAuthStateChange((event, session) => {
        if (event === 'TOKEN_REFRESHED') {
            console.log("Auth Event: TOKEN_REFRESHED");
            return;
        }
        
        console.log("Auth Event:", event, "Session:", session);
        handleSession(session);
    });
}

function checkUrlForErrors() {
    const params = new URLSearchParams(window.location.search);
    const hashParams = new URLSearchParams(window.location.hash.substring(1));
    
    const error = params.get('error') || hashParams.get('error');
    const errorDesc = params.get('error_description') || hashParams.get('error_description');
    const errorCode = params.get('error_code') || hashParams.get('error_code');

    if (error) {
        console.error("Auth Error Detected:", error, errorDesc);
        let userMessage = "Erreur de connexion.";
        if (errorCode === 'provider_email_needs_verification' || (errorDesc && errorDesc.includes('Unverified email'))) {
            userMessage = "⚠️ Email non vérifié chez Spotify. Vérifiez vos spams.";
        } else if (errorDesc) {
            userMessage = "⚠️ " + errorDesc.replace(/\+/g, ' ');
        }
        setTimeout(() => {
            if (typeof showToast === 'function') showToast(userMessage);
            // IMPORTANT: Clear the URL parameters after processing to avoid re-triggering errors or redirects.
            window.history.replaceState({}, document.title, window.location.pathname);
        }, 1000);
    }
}

function handleSession(session) {
    const loginScreen = document.getElementById('loginScreen');
    const appLayout = document.querySelector('.app-layout');
    
    console.log("handleSession called with session:", session);

    if (session) {
        // Utilisateur connecté
        if (loginScreen) loginScreen.style.display = 'none';
        if (appLayout) {
            appLayout.style.display = ''; 
            appLayout.classList.add('layout-visible');
        }
        
        const userId = session.user.id;
        
        // Si c'est un nouvel utilisateur ou première charge
        if (userId !== currentUserId) {
            currentUserId = userId;
            console.log("handleSession: User ID changed, loading user data.");
            
            // CHARGEMENT DES DONNÉES (Favoris + Profil)
            if (window.loadUserFavorites) {
                window.loadUserFavorites();
            }
            updateUserProfile(session.user);
            
            // TENTATIVE DE SYNC SPOTIFY (Une seule fois par user)
            if (session.user.app_metadata.provider === 'spotify' && !hasSyncedSpotifySession) {
                hasSyncedSpotifySession = true;
                syncSpotifyProfileData(session.user);
                if (session.provider_token) syncSpotifyFavorites(session.provider_token);
            }
        } else {
            console.log("handleSession: User ID is the same, no need to reload user data.");
        }
        
    } else {
        // Déconnexion
        currentUserId = null;
        hasSyncedSpotifySession = false;
        console.log("handleSession: No session found, showing login screen.");
        
        // STOP AUDIO & RESET
        if (typeof window.pause === 'function') window.pause();
        // On vide les sources pour éviter que ça ne continue en arrière-plan ou ne reprenne
        if (window.playerA) { window.playerA.src = ''; window.playerA.load(); }
        if (window.playerB) { window.playerB.src = ''; window.playerB.load(); }
        
        if (loginScreen) loginScreen.style.display = 'flex';
        if (appLayout) appLayout.classList.remove('layout-visible');
    }
}

async function syncSpotifyProfileData(user) {
    try {
        const { avatar_url, full_name, name, picture } = user.user_metadata;
        const displayName = full_name || name;
        const avatar = avatar_url || picture; 

        if (!displayName && !avatar) return;

        const { data: currentProfile } = await supabaseClient
            .from('profiles')
            .select('username, avatar_url')
            .eq('id', user.id)
            .single();
            
        const updates = { id: user.id, updated_at: new Date() };
        let hasChanges = false;

        if (displayName && (!currentProfile || !currentProfile.username || currentProfile.username === 'Anonyme' || currentProfile.username === user.email)) {
            updates.username = displayName;
            hasChanges = true;
        }
        
        if (avatar && (!currentProfile || !currentProfile.avatar_url)) {
            updates.avatar_url = avatar;
            hasChanges = true;
        }

        if (hasChanges) {
            console.log("🔄 Syncing Profile from Spotify...");
            const { error } = await supabaseClient.from('profiles').upsert(updates);
            if (!error && window.loadProfile) window.loadProfile();
        }
    } catch (e) {
        console.error("Spotify Profile Sync Error:", e);
    }
}

async function syncSpotifyFavorites(token) {
    console.log("🔄 Syncing Spotify Favorites...");
    try {
        const { data: { user } } = await supabaseClient.auth.getUser();
        if (!user) {
            console.error("User not authenticated for Spotify sync.");
            return;
        }

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
                    source: 'spotify_lazy', // Indique que c'est un titre Spotify à résoudre
                    duration: t.duration_ms / 1000,
                    imported_from: 'spotify'
                };
            });

            // Récupérer les favoris existants de l'utilisateur pour éviter les doublons
            const { data: existingFavorites, error: fetchError } = await supabaseClient
                .from('favorites')
                .select('track_id')
                .eq('user_id', user.id);

            if (fetchError) throw fetchError;

            const existingTrackIds = new Set(existingFavorites.map(f => f.track_id));
            let newCount = 0;
            const tracksToInsert = [];

            spotifyTracks.forEach(st => {
                if (!existingTrackIds.has(st.id)) {
                    tracksToInsert.push({
                        user_id: user.id,
                        track_id: st.id,
                        track_data: st
                    });
                    newCount++;
                }
            });

            if (tracksToInsert.length > 0) {
                const { error: insertError } = await supabaseClient
                    .from('favorites')
                    .insert(tracksToInsert);

                if (insertError) throw insertError;
                
                showToast(`✅ ${newCount} titres Spotify importés !`);
                // Recharger les favoris pour mettre à jour l'interface
                if (window.loadUserFavorites) {
                    window.loadUserFavorites();
                }
            } else {
                showToast("Aucun nouveau titre Spotify à importer.");
            }
        }
    } catch (e) {
        console.error("Spotify Sync Error:", e);
        showToast("Erreur lors de l'import Spotify.");
    }
}

async function loginWithSpotify() {
    showAuthLoading(true);
    const { data, error } = await supabaseClient.auth.signInWithOAuth({
        provider: 'spotify',
        options: { scopes: 'user-library-read', redirectTo: window.location.origin }
    });
    if (error) { showAuthLoading(false); showAuthError(error.message); }
}

async function login(email, password) {
    showAuthLoading(true);
    const { data, error } = await supabaseClient.auth.signInWithPassword({ email, password });
    showAuthLoading(false);
    if (error) showAuthError(error.message);
}

async function signup(email, password) {
    const username = document.getElementById('username')?.value.trim();
    if (!username) return showAuthError("Merci de choisir un pseudo !");

    showAuthLoading(true);
    const { data, error } = await supabaseClient.auth.signUp({
        email, password, options: { data: { username } }
    });
    showAuthLoading(false);

    if (error) showAuthError(error.message);
    else {
        if (data?.user) {
            try { await supabaseClient.from('profiles').upsert({ id: data.user.id, username, updated_at: new Date() }); } catch (e) {}
        }
        showAuthError("Inscription réussie ! Vérifiez vos emails.", true);
    }
}

async function logout() {
    await supabaseClient.auth.signOut();
    currentUserId = null; // Explicitly clear
    hasSyncedSpotifySession = false; // Explicitly clear
    // handleSession(null) sera appelée automatiquement par l'écouteur onAuthStateChange avec l'événement 'SIGNED_OUT'.
}

function showAuthError(msg, isSuccess = false) {
    const el = document.getElementById('authMessage');
    if (el) { el.innerText = msg; el.style.color = isSuccess ? '#00d2ff' : '#ff0055'; el.style.opacity = 1; }
}

function showAuthLoading(isLoading) {
    const btn = document.getElementById('btnLoginAction');
    const spotifyBtn = document.getElementById('btnSpotifyAction');
    if(btn) btn.innerText = isLoading ? '...' : (isLoginMode ? 'Se connecter' : "S'inscrire");
    if(spotifyBtn) spotifyBtn.style.opacity = isLoading ? 0.5 : 1;
}

function updateUserProfile(user) { console.log("Logged in as:", user.email); }

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