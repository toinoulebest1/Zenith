// script.js

// Variables globales
let tracks = [];
let currentIndex = 0;
let audio = new Audio();
audio.crossOrigin = "anonymous"; // Important pour le streaming
let isPlaying = false;

// CONFIGURATION SERVEUR LOCAL
// C'est l'adresse du script Python server.py
const API_BASE = 'http://127.0.0.1:5000';

// Éléments du DOM
const els = {
    grid: document.getElementById('trackGrid'),
    xlArt: document.getElementById('xlArt'),
    xlTitle: document.getElementById('xlTitle'),
    xlArtist: document.getElementById('xlArtist'),
    capArt: document.getElementById('capArt'),
    capTitle: document.getElementById('capTitle'),
    capArtist: document.getElementById('capArtist'),
    playIcon: document.getElementById('playIcon'),
    progressFill: document.getElementById('progressFill'),
    progressWrapper: document.querySelector('.progress-wrapper')
};

// --- 1. Initialisation & API ---

async function init() {
    try {
        // On appelle notre serveur Python local
        const res = await fetch(`${API_BASE}/favorites?limit=50`);
        const data = await res.json();

        if (data.tracks && data.tracks.items) {
            processTracks(data.tracks.items);
        } else {
            els.grid.innerHTML = "<p style='padding:20px'>Erreur: Impossible de récupérer les favoris via Python.</p>";
        }
    } catch (e) {
        console.error(e);
        els.grid.innerHTML = "<p style='padding:20px'>Erreur: Le serveur Python (server.py) n'est pas lancé !</p>";
    }
}

function processTracks(items) {
    tracks = items.map(item => ({
        id: item.id, // ID Important pour demander le stream
        title: item.title,
        artist: item.performer.name,
        album: item.album.title,
        img: (item.album.image.large || '').replace('_600', '_300'),
        hires: item.maximum_bit_depth > 16
    }));

    renderGrid();
}

// --- 2. Rendu Visuel ---

function renderGrid() {
    els.grid.innerHTML = '';
    tracks.forEach((track, i) => {
        const card = document.createElement('div');
        card.className = 'track-card';
        card.innerHTML = `
            <img src="${track.img}" loading="lazy">
            <h3>${track.title}</h3>
            <p>${track.artist}</p>
            ${track.hires ? '<span style="background:white; color:black; padding:2px 4px; border-radius:2px; font-size:9px; font-weight:bold;">HI-RES</span>' : ''}
        `;
        card.onclick = () => player.load(i);
        els.grid.appendChild(card);
    });
}

function updateUI(track) {
    els.xlArt.src = track.img;
    els.xlTitle.innerText = track.title;
    els.xlArtist.innerText = track.artist;

    els.capArt.src = track.img;
    els.capTitle.innerText = track.title;
    els.capArtist.innerText = track.artist;
}

// --- 3. Gestion du Player ---

const player = {
    load: (index) => {
        currentIndex = index;
        const t = tracks[index];
        
        // MAGIE : On demande au serveur Python l'URL de stream signée
        const streamUrl = `${API_BASE}/stream/${t.id}`;

        audio.src = streamUrl;
        updateUI(t);
        player.play();
        
        // Gestion des erreurs de lecture
        audio.onerror = () => {
            alert("Impossible de lire ce titre (Droits Qobuz ou Erreur Serveur).");
        };
    },

    play: () => {
        audio.play().catch(e => console.error("Erreur play:", e));
        isPlaying = true;
        els.playIcon.className = 'fas fa-pause';
        els.capArt.classList.add('playing');
    },

    pause: () => {
        audio.pause();
        isPlaying = false;
        els.playIcon.className = 'fas fa-play';
        els.capArt.classList.remove('playing');
    },

    toggle: () => {
        if (tracks.length === 0) return;
        isPlaying ? player.pause() : player.play();
    },

    next: () => {
        let nextIdx = (currentIndex + 1) % tracks.length;
        player.load(nextIdx);
    },

    prev: () => {
        let prevIdx = (currentIndex - 1 + tracks.length) % tracks.length;
        player.load(prevIdx);
    },

    seek: (e) => {
        if (!audio.duration) return;
        const width = els.progressWrapper.clientWidth;
        const clickX = e.offsetX;
        audio.currentTime = (clickX / width) * audio.duration;
    }
};

// --- 4. Événements Audio ---

audio.addEventListener('timeupdate', () => {
    if (audio.duration) {
        const pct = (audio.currentTime / audio.duration) * 100;
        els.progressFill.style.width = `${pct}%`;
    }
});

audio.addEventListener('ended', player.next);

// Lancer l'application
init();