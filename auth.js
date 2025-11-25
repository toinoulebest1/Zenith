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
    
    // Écouter les changements d'état (Connexion, Déconnexion)
    supabaseClient.auth.onAuthStateChange((event, session) => {
        console.log("Auth Event:", event);
        handleSession(session);
    });
}

function handleSession(session) {
    const loginScreen = document.getElementById('loginScreen');
    const appLayout = document.querySelector('.app-layout');
    
    if (session) {
        // Utilisateur connecté
        if (loginScreen) loginScreen.style.display = 'none';
        if (appLayout) {
            // FIX MOBILE : On n'utilise pas style.display = 'grid' ici car ça casse le CSS mobile
            // On utilise une classe pour laisser le CSS décider (Grid sur PC, Flex sur Mobile)
            appLayout.style.display = ''; 
            appLayout.classList.add('layout-visible');
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
    // Récupération du pseudo depuis le nouveau champ
    const usernameInput = document.getElementById('username');
    const username = usernameInput ? usernameInput.value.trim() : "";

    if (!username) {
        showAuthError("Merci de choisir un pseudo !");
        return;
    }

    showAuthLoading(true);
    
    // On passe le username dans les métadonnées
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
        // Si l'inscription est réussie et qu'on a un utilisateur
        if (data && data.user) {
            // On force la mise à jour du profil pour être sûr que le pseudo est bien enregistré
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
    if(btn) btn.innerText = isLoading ? '...' : (isLoginMode ? 'Se connecter' : "S'inscrire");
}

function updateUserProfile(user) {
    // Petit helper pour afficher l'email dans la sidebar par exemple
    console.log("Logged in as:", user.email);
}

// État local du formulaire
let isLoginMode = true;
function toggleAuthMode() {
    isLoginMode = !isLoginMode;
    
    // Affichage conditionnel du champ pseudo
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

// Initialisation au chargement
document.addEventListener('DOMContentLoaded', initAuth);