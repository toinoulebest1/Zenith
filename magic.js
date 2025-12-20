// magic.js - Gestionnaire Ultimate des Easter Eggs

let effectInterval = null;

function checkMagic(title, artist) {
    const t = title.toLowerCase();
    const a = artist.toLowerCase();
    const body = document.body;
    const wand = document.getElementById('wandContainer');

    // RESET (Ciblé pour ne pas casser le mode Zen)
    const magicClasses = ['fire-mode', 'rain-mode', 'ocean-mode', 'starwars-mode', 'matrix-mode', 'disco-mode'];
    magicClasses.forEach(c => body.classList.remove(c));
    
    wand.classList.remove('active');
    stopEffects();

    // 1. HARRY POTTER
    if ((t.includes("dumbledore") && t.includes("farewell")) || t.includes("harry potter") || t.includes("hedwig")) {
        console.log("🪄 Mode: Harry Potter");
        wand.classList.add('active');
    }

    // 2. FIRE / HELL
    else if (t.includes("fire") || t.includes("burn") || t.includes("hell") || a.includes("ac/dc") || a.includes("rammstein") || a.includes("doom")) {
        console.log("🔥 Mode: Fire");
        body.classList.add('fire-mode');
        startEmbers();
    }

    // 3. RAIN / SAD
    else if (t.includes("rain") || t.includes("sad") || t.includes("tears") || t.includes("lofi") || t.includes("cry")) {
        console.log("🌧️ Mode: Rain");
        body.classList.add('rain-mode');
        startRain();
    }

    // 4. OCEAN / WATER
    else if (t.includes("ocean") || t.includes("sea") || t.includes("water") || t.includes("blue") || t.includes("aquatic")) {
        console.log("🌊 Mode: Ocean");
        body.classList.add('ocean-mode');
        startBubbles();
    }

    // 5. STAR WARS
    else if (t.includes("star wars") || t.includes("imperial") || t.includes("force") || a.includes("john williams")) {
        console.log("🌌 Mode: Star Wars");
        body.classList.add('starwars-mode');
    }

    // 6. MATRIX
    else if (t.includes("matrix") || t.includes("techno") || t.includes("hack") || a.includes("daft punk")) {
        console.log("🕶️ Mode: Matrix");
        body.classList.add('matrix-mode');
    }

    // 7. DISCO
    else if (t.includes("disco") || t.includes("party") || t.includes("dance")) {
        console.log("🪩 Mode: Disco");
        body.classList.add('disco-mode');
    }
}

// --- EFFETS VISUELS ---

function stopEffects() {
    if(effectInterval) { clearInterval(effectInterval); effectInterval = null; }
    document.querySelectorAll('.ember, .raindrop, .bubble').forEach(e => e.remove());
}

function startEmbers() {
    const layout = document.querySelector('.app-layout');
    if(!layout) return;
    effectInterval = setInterval(() => {
        const e = document.createElement('div');
        e.className = 'ember';
        e.style.left = Math.random() * 100 + '%';
        e.style.animationDuration = (2 + Math.random() * 3) + 's';
        layout.appendChild(e);
        setTimeout(() => e.remove(), 5000);
    }, 100);
}

function startRain() {
    const layout = document.querySelector('.app-layout');
    if(!layout) return;
    effectInterval = setInterval(() => {
        const r = document.createElement('div');
        r.className = 'raindrop';
        r.style.left = Math.random() * 100 + '%';
        r.style.animationDuration = (0.5 + Math.random() * 0.5) + 's';
        layout.appendChild(r);
        setTimeout(() => r.remove(), 1000);
    }, 50);
}

function startBubbles() {
    const layout = document.querySelector('.app-layout');
    if(!layout) return;
    effectInterval = setInterval(() => {
        const b = document.createElement('div');
        b.className = 'bubble';
        b.style.left = Math.random() * 100 + '%';
        const size = 5 + Math.random() * 15;
        b.style.width = size + 'px';
        b.style.height = size + 'px';
        b.style.animationDuration = (4 + Math.random() * 6) + 's';
        layout.appendChild(b);
        setTimeout(() => b.remove(), 10000);
    }, 400);
}