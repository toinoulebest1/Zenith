// magic.js - La logique de l'animation

let magicInterval = null;

function checkMagic(title, artist) {
    const t = title.toLowerCase();
    const a = artist.toLowerCase();
    const wand = document.getElementById('wandContainer');
    
    // Détecte Dumbledore's Farewell ou Harry Potter
    if ((t.includes("dumbledore") && t.includes("farewell")) || t.includes("harry potter")) {
        console.log("🪄 Lumos Maxima !");
        wand.classList.add('active');
        startParticles();
    } else {
        wand.classList.remove('active');
        stopParticles();
    }
}

function startParticles() {
    if(magicInterval) return;
    const panel = document.querySelector('.right-panel');
    
    magicInterval = setInterval(() => {
        const p = document.createElement('div');
        p.className = 'particle';
        // Position aléatoire
        p.style.left = Math.random() * 100 + '%'; 
        // Taille aléatoire
        const size = 2 + Math.random() * 4;
        p.style.width = size + 'px';
        p.style.height = size + 'px';
        // Durée aléatoire
        p.style.animationDuration = (2 + Math.random() * 3) + 's';
        
        panel.appendChild(p);
        
        // Nettoyage
        setTimeout(() => p.remove(), 5000);
    }, 200);
}

function stopParticles() {
    if(magicInterval) {
        clearInterval(magicInterval);
        magicInterval = null;
    }
    // Supprime les particules existantes
    document.querySelectorAll('.particle').forEach(p => p.remove());
}