# AI Rules & Tech Stack Documentation

## Tech Stack

*   **Frontend:** Vanilla HTML5, CSS3, and JavaScript (ES6+). No frontend framework (React/Vue/Angular) is currently used.
*   **Backend:** Python 3 using **Flask**.
*   **Deployment:** Configured for Vercel Serverless Functions (`vercel.json`, `api/index.py`).
*   **Styling:** Custom CSS (`magic.css`, `style.css`) using CSS Variables for theming.
*   **Icons:** FontAwesome (via CDN).
*   **Audio Engine:** HTML5 `Audio` API with `AudioContext` for effects (8D, EQ, Karaoke).
*   **Casting:** Google Cast SDK (`cast_sender.js`).

## Development Rules & Conventions

### 1. Backend (Python/Flask)
*   **Structure:** The core entry point for Vercel is `api/index.py`. Local development uses `server.py`.
*   **API Calls:** Use the `requests` library for all external HTTP calls (Qobuz, Tidal, Deezer, etc.).
*   **Modules:**
    *   `qobuz_api.py`: Handles Qobuz authentication and stream URL retrieval.
    *   `lyrics_search.py`: Handles lyrics retrieval from LRCLib.
*   **Response Format:** All API endpoints must return JSON. Errors should return appropriate HTTP status codes (404, 500).

### 2. Frontend (HTML/JS)
*   **DOM Manipulation:** Use standard API (`document.getElementById`, `querySelector`). Do not introduce jQuery or other DOM libraries.
*   **State Management:** Global variables (e.g., `window.tracks`, `window.audio`) are currently used for player state. Keep this pattern unless a refactor is requested.
*   **Audio:**
    *   Use `crossOrigin = "anonymous"` for audio elements to allow `AudioContext` processing.
    *   Effects (EQ, Panning) must handle `AudioContext` states (suspended/running).
*   **Modularity:** Keep distinct features in separate files (e.g., `blindtest.js`, `cast.js`) and include them in `index.html`.

### 3. External Integrations
*   **Qobuz:** Requires valid `app_id`, `user_id`, and `token`. Use `qobuz_api.py` wrapper.
*   **Tidal:** Uses `tidal.kinoplus.online` proxy.
*   **Lyrics:** Prioritize synced lyrics from LRCLib.
*   **Images:** Tidal images are constructed via UUID; Qobuz images via URL replacement (`_600` vs `_300`).

### 4. Styling
*   **Themes:** Use CSS variables (`--primary`, `--glass-bg`) for colors to support dynamic theming.
*   **Responsive:** Ensure layouts (Grid/Flexbox) work on mobile. The app uses a specific "Glassmorphism" aesthetic.
*   **Effects:** Easter eggs and visual modes are handled in `magic.css`.