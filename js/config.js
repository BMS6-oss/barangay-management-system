/**
 * BMS Client Environment Configuration
 * ----------------------------------------------------------------------
 * For same-origin production hosting (e.g. Render Docker web service or local server.py),
 * leave API_BASE_URL empty ('') so the frontend automatically connects
 * to the same-origin backend at window.location.origin.
 * 
 * For decoupled/cross-origin production setups (such as GitHub Pages or a static CDN
 * calling the cloud API on Render), configure the production backend API URL:
 */
window.__BMS_CONFIG__ = Object.assign(window.__BMS_CONFIG__ || {}, {
    // Cloud API endpoint used when frontend is deployed on a static host (e.g. GitHub Pages)
    CLOUD_API_URL: 'https://barangay-management-system.onrender.com',

    // Explicit API Base URL override. If set, all requests go directly to this URL.
    // Leave empty ('') to auto-detect same-origin or cloud fallback.
    API_BASE_URL: ''
});

