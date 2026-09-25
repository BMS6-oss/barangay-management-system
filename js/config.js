/**
 * BMS Client Environment Configuration
 * ----------------------------------------------------------------------
 * For same-origin production hosting (e.g. https://your-bms-domain.gov.ph/),
 * leave API_BASE_URL empty ('') so the frontend automatically connects
 * to the same-origin backend at window.location.origin.
 * 
 * For decoupled/cross-origin production setups (e.g. static CDN frontend
 * calling a separate cloud API), set the full HTTPS API base URL:
 *   API_BASE_URL: 'https://api.your-domain.gov.ph'
 */
window.__BMS_CONFIG__ = Object.assign(window.__BMS_CONFIG__ || {}, {
    API_BASE_URL: ''
});
