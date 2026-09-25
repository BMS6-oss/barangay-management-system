/**
 * BMS Content Protection Module
 * ============================================================
 * Provides multi-layer content deterrence for the public
 * Barangay Management System website.
 *
 * Layers implemented:
 *   1. Context-menu (right-click) protection via event delegation
 *   2. Image drag prevention via event delegation
 *   3. Touch long-press prevention (mobile)
 *   4. CSS watermark overlay injection on protected images
 *   5. Ctrl+S keyboard deterrence on public welcome page
 *   6. MutationObserver for dynamically loaded images
 *
 * IMPORTANT LIMITATIONS (documented honestly):
 *   - Screenshots cannot be prevented by browser-based code.
 *   - Screen recordings cannot be prevented.
 *   - Browser DevTools can bypass client-side protections.
 *   - The visible watermark is the strongest deterrence layer
 *     because it persists in screenshots and screen recordings.
 *
 * Behavior by page context:
 *   PUBLIC WELCOME PAGE  → Full protection active
 *   RESIDENT DASHBOARD   → Image-only protection (no form interference)
 *   ADMIN DASHBOARD      → Protection OFF (admins need full browser access)
 *   LOGIN PAGE           → Protection OFF (no images to protect)
 * ============================================================
 */
window.BMSContentProtection = (() => {
    'use strict';

    // ── Default configuration (overridden by server settings) ─────────────
    let config = {
        enabled: true,
        watermarkEnabled: true,
        watermarkText: 'OFFICIAL BARANGAY WEBSITE',
        watermarkOpacity: 0.30,
        watermarkPosition: 'bottom-right',
        rightClickProtection: true,
        dragPrevention: true,
    };

    let initialized = false;
    let touchTimer = null;

    // ── Context classification ────────────────────────────────────────────
    /**
     * Determine whether a given element is within a protected public zone.
     * Returns false for admin dashboard, login page, and form elements.
     */
    function isInPublicZone(el) {
        if (!el) return false;
        // Never protect admin dashboard or login
        if (el.closest('#appContainer') || el.closest('.app-container')) return false;
        if (el.closest('#loginPage') || el.closest('.login-wrapper')) return false;
        // Protect welcome landing page and any element marked data-bms-zone="public"
        if (el.closest('#welcomeLandingPage')) return true;
        if (el.closest('[data-bms-zone="public"]')) return true;
        return false;
    }

    /**
     * Determine if the specific target element should be protected.
     * Protects images but NOT interactive controls, inputs, or text.
     */
    function shouldProtect(el) {
        if (!config.enabled) return false;
        if (!el) return false;

        // Always allow: inputs, buttons, select, textarea, links, interactive controls
        const interactiveTags = new Set(['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON', 'A', 'LABEL']);
        if (interactiveTags.has(el.tagName)) return false;
        if (el.closest('input, select, textarea, button, a[href], label')) return false;
        if (el.isContentEditable) return false;

        // Check if within a protected image context
        const isProtectedImage = (
            el.classList.contains('protected-img') ||
            el.closest('.protected-img') !== null ||
            el.classList.contains('hero-photo-bg') ||
            el.classList.contains('hero-photo-section') ||
            el.closest('.hero-photo-section') !== null ||
            el.classList.contains('gallery-photo') ||
            el.closest('.gallery-photo') !== null ||
            el.closest('.gallery-item') !== null ||
            el.classList.contains('official-avatar-img') ||
            el.closest('.official-avatar-img') !== null ||
            el.closest('.official-avatar-wrap') !== null ||
            el.classList.contains('bms-protected-media') ||
            el.closest('.bms-protected-media') !== null ||
            el.id === 'galleryModalImg' ||
            el.id === 'heroBgImage' ||
            el.closest('#heroBgImage') !== null ||
            // Logo images
            (el.tagName === 'IMG' && el.closest('.gov-logo-img') !== null) ||
            (el.tagName === 'IMG' && el.closest('.login-logo') !== null)
        );

        if (isProtectedImage) return true;

        // Any image element on the public welcome page
        if (el.tagName === 'IMG' && isInPublicZone(el)) return true;

        return false;
    }

    // ── Right-click / context menu protection ─────────────────────────────
    function setupContextMenuProtection() {
        document.addEventListener('contextmenu', (e) => {
            if (!config.rightClickProtection) return;
            if (shouldProtect(e.target)) {
                e.preventDefault();
            }
        }, { passive: false });
    }

    // ── Drag prevention ───────────────────────────────────────────────────
    function setupDragPrevention() {
        document.addEventListener('dragstart', (e) => {
            if (!config.dragPrevention) return;
            if (shouldProtect(e.target)) {
                e.preventDefault();
                return false;
            }
        }, { passive: false });

        // Also handle mousedown to prevent ghost drag image on some browsers
        document.addEventListener('mousedown', (e) => {
            if (!config.dragPrevention) return;
            if (e.target.tagName === 'IMG' && shouldProtect(e.target)) {
                e.target.setAttribute('draggable', 'false');
            }
        }, { passive: true });
    }

    // ── Touch long-press prevention (mobile) ──────────────────────────────
    function setupTouchProtection() {
        document.addEventListener('touchstart', (e) => {
            if (!config.enabled) return;
            const t = e.target;
            if (!shouldProtect(t)) return;
            // Start a timer; if user holds > 600ms, it's a long-press context menu attempt
            touchTimer = setTimeout(() => {
                // Blur the element so the browser long-press menu doesn't appear
                if (document.activeElement && document.activeElement !== document.body) {
                    document.activeElement.blur();
                }
            }, 600);
        }, { passive: true });

        document.addEventListener('touchend', () => {
            if (touchTimer) {
                clearTimeout(touchTimer);
                touchTimer = null;
            }
        }, { passive: true });

        document.addEventListener('touchcancel', () => {
            if (touchTimer) {
                clearTimeout(touchTimer);
                touchTimer = null;
            }
        }, { passive: true });
    }

    // ── Keyboard deterrence (Ctrl+S only, welcome page only) ──────────────
    function setupKeyboardDeterrence() {
        document.addEventListener('keydown', (e) => {
            if (!config.enabled) return;
            const welcomePage = document.getElementById('welcomeLandingPage');
            if (!welcomePage || welcomePage.style.display === 'none') return;

            // Block Ctrl+S (Save page) on the public welcome page only
            if ((e.ctrlKey || e.metaKey) && e.key === 's') {
                e.preventDefault();
            }
            // NOTE: Ctrl+C, Ctrl+V, and all other shortcuts are intentionally NOT blocked
            // to preserve normal usability (copying text, reference numbers, etc.)
        }, { passive: false });
    }

    // ── CSS watermark overlay injection ───────────────────────────────────
    /**
     * Injects a CSS-only visible watermark overlay into a container element.
     * The watermark is inside a pointer-events:none layer so it doesn't
     * interfere with clicks, but it remains visible in screenshots.
     *
     * @param {Element} containerEl - Must have position:relative (or we set it)
     * @param {string} [customText] - Override watermark text for this element
     */
    function applyWatermarkOverlay(containerEl, customText) {
        if (!config.watermarkEnabled) return;
        if (!containerEl) return;
        // Avoid double-adding
        if (containerEl.querySelector('.bms-wm-overlay')) return;

        const text = customText || config.watermarkText;
        const year = new Date().getFullYear();
        const displayText = text.replace('{year}', year);

        const overlay = document.createElement('div');
        overlay.className = 'bms-wm-overlay';
        overlay.setAttribute('aria-hidden', 'true');
        overlay.setAttribute('role', 'presentation');

        const span = document.createElement('span');
        span.className = 'bms-wm-text';
        span.textContent = displayText;

        overlay.appendChild(span);
        containerEl.appendChild(overlay);

        // Ensure container has positioning context
        const cs = window.getComputedStyle(containerEl);
        if (cs.position === 'static') {
            containerEl.style.position = 'relative';
        }
    }

    /**
     * Remove watermark overlay from a container (useful before admin preview).
     */
    function removeWatermarkOverlay(containerEl) {
        if (!containerEl) return;
        const existing = containerEl.querySelector('.bms-wm-overlay');
        if (existing) existing.remove();
    }

    // ── Apply draggable=false to protected images ─────────────────────────
    function hardenImage(imgEl) {
        if (!imgEl || imgEl.tagName !== 'IMG') return;
        imgEl.setAttribute('draggable', 'false');
        imgEl.setAttribute('data-bms-protected', 'true');
        imgEl.classList.add('protected-img');
    }

    /**
     * Scan and harden all images currently in the public zones.
     * Called on init and after dynamic content loads.
     */
    function hardenAllPublicImages() {
        const selectors = [
            '#welcomeLandingPage img',
            '#galleryModalImg',
            '.gov-logo-img',
            '.login-logo img',
        ];
        selectors.forEach(sel => {
            document.querySelectorAll(sel).forEach(img => hardenImage(img));
        });
    }

    // ── MutationObserver for dynamic content ──────────────────────────────
    function setupMutationObserver() {
        const observer = new MutationObserver((mutations) => {
            mutations.forEach(m => {
                m.addedNodes.forEach(node => {
                    if (node.nodeType !== 1) return; // Element nodes only
                    // Harden any new images in public zones
                    if (node.tagName === 'IMG' && isInPublicZone(node)) {
                        hardenImage(node);
                    }
                    if (node.querySelectorAll) {
                        node.querySelectorAll('img').forEach(img => {
                            if (isInPublicZone(img)) hardenImage(img);
                        });
                    }
                });
            });
        });

        observer.observe(document.body, {
            childList: true,
            subtree: true,
        });
    }

    // ── Inject protection CSS into document head ──────────────────────────
    function injectProtectionStyles() {
        if (document.getElementById('bms-cp-styles')) return;
        const style = document.createElement('style');
        style.id = 'bms-cp-styles';
        style.textContent = `
            /* BMS Content Protection Styles */

            /* Protected image base */
            img.protected-img,
            img[data-bms-protected="true"] {
                -webkit-user-drag: none;
                user-select: none;
                -webkit-user-select: none;
                -moz-user-select: none;
            }

            /* Protected media container */
            .bms-protected-media {
                position: relative;
                overflow: hidden;
                user-select: none;
                -webkit-user-select: none;
            }
            .bms-protected-media img {
                -webkit-user-drag: none;
                pointer-events: none;
            }

            /* Watermark overlay layer */
            .bms-wm-overlay {
                position: absolute;
                bottom: 0;
                left: 0;
                right: 0;
                top: 0;
                pointer-events: none;
                z-index: 10;
                display: flex;
                align-items: flex-end;
                justify-content: flex-end;
                padding: 0.6rem 0.8rem;
            }

            /* Watermark text pill */
            .bms-wm-text {
                display: inline-block;
                background: rgba(0, 0, 0, 0.28);
                color: rgba(255, 255, 255, 0.85);
                font-size: clamp(0.55rem, 1.1vw, 0.78rem);
                font-weight: 600;
                letter-spacing: 0.06em;
                padding: 0.25rem 0.55rem;
                border-radius: 4px;
                white-space: nowrap;
                text-shadow: 0 1px 3px rgba(0,0,0,0.5);
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                pointer-events: none;
                user-select: none;
                -webkit-user-select: none;
                line-height: 1.4;
            }

            /* Hero section watermark — positioned at bottom right of hero */
            .hero-photo-section .bms-wm-overlay {
                z-index: 30;
                padding: 1rem 1.4rem;
            }
            .hero-photo-section .bms-wm-text {
                font-size: clamp(0.6rem, 1.4vw, 0.85rem);
                background: rgba(0, 0, 0, 0.32);
                padding: 0.3rem 0.75rem;
            }

            /* Gallery lightbox watermark */
            #galleryModal .bms-wm-overlay {
                z-index: 100;
                padding: 1rem 1.5rem;
            }

            /* Print styles — keep watermark visible on print */
            @media print {
                .bms-wm-overlay {
                    display: flex !important;
                }
                .bms-wm-text {
                    color: rgba(0,0,0,0.45) !important;
                    background: transparent !important;
                    border: 1px solid rgba(0,0,0,0.2) !important;
                }
            }

            /* Reduced motion: no animation on watermark */
            @media (prefers-reduced-motion: reduce) {
                .bms-wm-overlay,
                .bms-wm-text {
                    transition: none !important;
                    animation: none !important;
                }
            }
        `;
        document.head.appendChild(style);
    }

    // ── Load protection settings from server ──────────────────────────────
    async function loadSettings() {
        try {
            if (window.BMSSQLite && typeof window.BMSSQLite.getPublicProtectionSettings === 'function') {
                const serverSettings = await window.BMSSQLite.getPublicProtectionSettings();
                if (serverSettings) {
                    config = { ...config, ...serverSettings };
                }
            }
        } catch (e) {
            // Non-fatal: use defaults
            console.warn('[BMS Protection] Could not load server protection settings, using defaults.', e.message);
        }
    }

    // ── Apply watermarks to gallery lightbox modal ────────────────────────
    function setupGalleryModalProtection() {
        const modal = document.getElementById('galleryModal');
        if (!modal) return;

        // Observe when modal becomes visible to apply watermark to new image
        const modalObserver = new MutationObserver(() => {
            const modalImg = document.getElementById('galleryModalImg');
            if (modalImg && !modal.querySelector('.bms-wm-overlay')) {
                if (config.watermarkEnabled) {
                    applyWatermarkOverlay(modal);
                }
                hardenImage(modalImg);
            }
        });
        modalObserver.observe(modal, { attributes: true, attributeFilter: ['style'] });

        // Also protect immediately if already open
        const modalImg = document.getElementById('galleryModalImg');
        if (modalImg && config.watermarkEnabled) {
            applyWatermarkOverlay(modal);
            hardenImage(modalImg);
        }
    }

    // ── Public API ────────────────────────────────────────────────────────
    async function init() {
        if (initialized) return;
        initialized = true;

        // Load server settings first (non-blocking if it fails)
        await loadSettings();

        if (!config.enabled) {
            console.info('[BMS Protection] Content protection disabled by admin setting.');
            return;
        }

        injectProtectionStyles();
        setupContextMenuProtection();
        setupDragPrevention();
        setupTouchProtection();
        setupKeyboardDeterrence();
        hardenAllPublicImages();
        setupMutationObserver();
        setupGalleryModalProtection();
    }

    /**
     * Apply hero section watermark overlay.
     * Called by welcome-data.js after the hero photo is loaded.
     */
    function applyHeroWatermark() {
        if (!config.watermarkEnabled || !config.enabled) return;
        const heroSection = document.querySelector('.hero-photo-section');
        if (heroSection) {
            removeWatermarkOverlay(heroSection);
            applyWatermarkOverlay(heroSection);
        }
    }

    /**
     * Update protection config (used by admin panel save).
     */
    function updateConfig(newConfig) {
        config = { ...config, ...newConfig };
    }

    /**
     * Get current config (used by admin panel to display current state).
     */
    function getConfig() {
        return { ...config };
    }

    return {
        init,
        applyWatermarkOverlay,
        removeWatermarkOverlay,
        applyHeroWatermark,
        hardenImage,
        hardenAllPublicImages,
        updateConfig,
        getConfig,
        shouldProtect,
    };
})();
