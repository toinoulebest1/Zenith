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
*   **Tidal:** self-hosted hifi-api instance (`TIDAL_HIFI_BASE`, HTTP so server-side only): `/search/?s=`, `/album/`, `/artist/`, `/info/`, and playback `/track/?id=&quality=HI_RES_LOSSLESS` (full tracks, up to 24-bit; `assetPresentation` must be `FULL`). `/tidal_manifest/{id}` returns its DASH manifest for Shaka (or `{mimeType, url}` for a direct FLAC "BTS" link, played by the audio element through the proxy); segments go through `/tidal_proxy`. The Eljohnzc addon (`ELJOHNZC_BASE`) is only a fallback. Tracks use `source: 'tidal_hund'`.
*   **Lossless mode (default, `LOSSLESS_MODE=1`):** Qobuz has priority everywhere, Tidal is the fallback. Tracks, albums and artists are searched on Qobuz and Tidal and merged without duplicates (`_merge_lossless`: Qobuz always wins, Tidal only adds what Qobuz lacks); artist pages come from Qobuz (`artist/page`), then Tidal, then Deezer; only playlists come from Deezer. Any other source is resolved at play time to Qobuz first, then Tidal (`/resolve_metadata`, no Deezer fallback). If one source can't play a track, the same track is tried on the other (`tryOtherSource`, `exclude=` param), then it is skipped silently (`skipUnplayable`, max 3 in a row) — never show a toast for this.
*   **Sources & audio health:** four playback sources, in priority order — `qobuz` (official API, `/stream/{id}` redirect), `tidal` (hifi-api instance, see above), `mono` (Monochrome `MONO_BASE` = tracks.monochrome.st: search `/search`, `/releases/{id}`, `/artists/{id}`; audio `/track/{id}` is a direct FLAC played by the BROWSER — the server gets 429), `deezer` (account cookie `DEEZER_ARL`: catalogue from the public api.deezer.com, tracks are `source: 'deezer_flac'` with the Deezer ID; `DeezerArl` gets the CDN link via gw-light `song.getData` + `media.deezer.com/v1/get_url`, best format allowed, same list as Deezer's web player: FLAC_24 (Hi-Res, since Oct 2026) → FLAC → MP3_320 → MP3_128, all `BF_CBC_STRIPE`; `/deezer_stream/{id or ISRC}` decrypts Blowfish stripe by ranges; `/deezer_info/{id}` gives the real bit depth / kHz read from the FLAC STREAMINFO for the player badge; search marks Hi-Res tracks via `song.getListData` `FILESIZE_FLAC_24`). `/sources/health` tests real audio server-side (Qobuz signed URL + first bytes, Tidal full-track manifest + first segment with the addon as fallback, Deezer link + first bytes; cached 3 min); the browser probes Monochrome audio itself and counts real playback failures (`noteSourceFailure`, 2 strikes = set aside). The browser sends `?sources=` (only sources whose audio works) to `/search`, `/resolve_metadata` and `/artist_bio`; the Tidal catalogue always complements search results last and is resolved to a working source at play time. Monochrome rate-limits audio per IP: don't probe it more than needed.
*   **Qobuz:** official API via `qobuz_api.py`. `QOBUZ_TOKEN` + `QOBUZ_USER_ID` come only from the environment (`.env` locally, Vercel settings online) — never hardcode them. App id/secret have built-in defaults matching the token. Qobuz is enabled as soon as a token is set; the old kennyy mirror is off (`QOBUZ_ALT_ENABLED=0`, it takes ~20 s to fail). Same rule for `DEEZER_ARL`: environment only, never hardcoded.
*   **Lyrics:** lrc.red first (word-level TTML parsed server-side in `sync_lrcred_lyrics`; set `LRCRED_SITE` / `LRCRED_CONTACT` to identify the app as their terms require, and always show the visible `#lyricsCredit` link to lrc.red when its lyrics are displayed). YouTube Music and LRCLib remain fallbacks.
*   **Images:** Tidal images are constructed via UUID; Qobuz images via URL replacement (`_600` vs `_300`).

### 4. Styling
*   **Tokens:** All colors, radii and fonts are CSS variables at the top of `style.css`. Use `var(--primary)` / `rgba(var(--primary-rgb), a)` for the accent (user-selectable in Settings, stored in `localStorage.zenith_accent`) and `var(--primary-ink)` for text on accent backgrounds. Never hardcode the accent color.
*   **Fonts:** `--font-display` (Bricolage Grotesque) for headings, `--font-ui` (Manrope) for everything else.
*   **Layout:** `.app-layout` is a grid (sidebar / main / "now playing" panel, player bar across the bottom). ≤1180px: icon-only sidebar. ≤1024px: the now-playing panel becomes a full-screen view (`body.np-open`). ≤768px: bottom tab bar (`.mobile-tabbar`), the sidebar becomes the "Plus" sheet, mini-player above the tabs.
*   **Views:** Switch views with `hideAllViews()` then show the target; set the menu state with `setActiveNav(key)`. Use `uiState('loading'|'empty'|'error', msg)` for loading/empty/error states instead of inline markup.
*   **Cards:** Wrap covers in `.card-media` (badges, `.card-play` button and the playing indicator live inside it).
*   **Effects:** Easter eggs and visual modes are handled in `magic.css`.