// tv.js - Gestion du mode Android TV et Navigation Spatiale

const TVUtils = {
    isTV: false,
    
    detect: function() {
        const ua = navigator.userAgent.toLowerCase();
        // Détection basique + paramètre URL pour forcer le test
        if (ua.includes('android') && (ua.includes('tv') || ua.includes('smart'))) {
            this.isTV = true;
        } else if (new URLSearchParams(window.location.search).get('mode') === 'tv') {
            this.isTV = true;
        }
        
        if (this.isTV) {
            console.log("📺 Mode TV Activé");
            document.body.classList.add('tv-mode');
            this.initNavigation();
        }
    },

    initNavigation: function() {
        // Focus initial
        setTimeout(() => {
            const firstItem = document.querySelector('.nav-item');
            if (firstItem) firstItem.focus();
        }, 1000);

        // Gestionnaire de touches
        document.addEventListener('keydown', (e) => {
            if (!this.isTV) return;

            // Mapping des touches télécommande
            const code = e.keyCode;
            const keys = {
                LEFT: 37, UP: 38, RIGHT: 39, DOWN: 40,
                ENTER: 13, BACK: 461, ESC: 27,
                MEDIA_PLAY_PAUSE: 179,
                MEDIA_STOP: 178,
                MEDIA_NEXT: 176,
                MEDIA_PREV: 177
            };

            // Gestion Retour
            if (code === keys.BACK || code === keys.ESC) {
                e.preventDefault();
                if (typeof goBack === 'function') goBack();
                return;
            }

            // Navigation Spatiale (Flèches)
            if ([keys.LEFT, keys.UP, keys.RIGHT, keys.DOWN].includes(code)) {
                // On empêche le scroll par défaut du navigateur
                e.preventDefault();
                this.navigate(code);
                return;
            }
            
            // Touche OK / Enter sur un élément focusable
            if (code === keys.ENTER) {
                const active = document.activeElement;
                if (active && active.click) {
                    active.click();
                    // Effet visuel de clic
                    active.classList.add('active-click');
                    setTimeout(() => active.classList.remove('active-click'), 200);
                }
            }
        });
        
        // Rendre les éléments focusables
        this.observeDOM();
    },

    // Ajoute tabindex="0" aux éléments interactifs dynamiques
    makeFocusable: function() {
        const selectors = [
            '.nav-item', 
            '.track-card', 
            '.login-btn', 
            '.ctrl-btn', 
            '.play-btn', 
            '.search-input', 
            '.search-btn',
            '.action-btn-mini',
            '.back-btn-main'
        ];
        
        selectors.forEach(sel => {
            document.querySelectorAll(sel).forEach(el => {
                if (!el.hasAttribute('tabindex')) {
                    el.setAttribute('tabindex', '0');
                }
            });
        });
    },

    observeDOM: function() {
        this.makeFocusable();
        // Observer pour les nouveaux éléments (chargement infini, changement de vue)
        const observer = new MutationObserver(() => this.makeFocusable());
        observer.observe(document.body, { childList: true, subtree: true });
    },

    // Algorithme de navigation spatiale simple
    navigate: function(keyCode) {
        const active = document.activeElement;
        if (!active || active === document.body) {
            // Si rien n'est focus, on focus le premier menu
            const first = document.querySelector('.nav-item');
            if (first) first.focus();
            return;
        }

        const activeRect = active.getBoundingClientRect();
        const activeCenter = {
            x: activeRect.left + activeRect.width / 2,
            y: activeRect.top + activeRect.height / 2
        };

        const candidates = document.querySelectorAll('[tabindex="0"]:not([style*="display: none"])');
        let bestCandidate = null;
        let minDistance = Infinity;

        candidates.forEach(cand => {
            if (cand === active) return;
            if (cand.offsetParent === null) return; // Élément caché

            const candRect = cand.getBoundingClientRect();
            const candCenter = {
                x: candRect.left + candRect.width / 2,
                y: candRect.top + candRect.height / 2
            };

            let isValid = false;
            let dist = 0;

            // Filtrage directionnel
            // UP (38)
            if (keyCode === 38 && candCenter.y < activeCenter.y) {
                // On privilégie l'alignement vertical strict
                isValid = true;
                // Pondération : distance verticale compte plus
                dist = Math.hypot((candCenter.x - activeCenter.x) * 4, candCenter.y - activeCenter.y);
            }
            // DOWN (40)
            else if (keyCode === 40 && candCenter.y > activeCenter.y) {
                isValid = true;
                dist = Math.hypot((candCenter.x - activeCenter.x) * 4, candCenter.y - activeCenter.y);
            }
            // LEFT (37)
            else if (keyCode === 37 && candCenter.x < activeCenter.x) {
                isValid = true;
                // Pondération : distance horizontale compte plus
                dist = Math.hypot(candCenter.x - activeCenter.x, (candCenter.y - activeCenter.y) * 4);
            }
            // RIGHT (39)
            else if (keyCode === 39 && candCenter.x > activeCenter.x) {
                isValid = true;
                dist = Math.hypot(candCenter.x - activeCenter.x, (candCenter.y - activeCenter.y) * 4);
            }

            if (isValid && dist < minDistance) {
                minDistance = dist;
                bestCandidate = cand;
            }
        });

        if (bestCandidate) {
            bestCandidate.focus();
            bestCandidate.scrollIntoView({ behavior: 'smooth', block: 'center' });
        }
    }
};

document.addEventListener('DOMContentLoaded', () => {
    TVUtils.detect();
});