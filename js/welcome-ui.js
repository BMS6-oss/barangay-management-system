/**
 * BMS Welcome Page UI Interactions & Routing
 * Controls navigation, modal dialogs, loading screen, and portal entry transitions.
 */
window.BMSWelcomeUI = (() => {

    function init() {
        checkFileProtocol();
        setupPhilippineTime();
        setupNavigation();
        setupPortalModal();
        setupGallery();
        setupServerStatusMonitor();
        setupLoadingScreen();
    }

    // Live Philippine Standard Time (PST, UTC+8)
    function setupPhilippineTime() {
        const clockEl = document.getElementById('pstClock');
        if (!clockEl) return;

        function updateTime() {
            try {
                const now = new Date();
                const options = {
                    timeZone: 'Asia/Manila',
                    weekday: 'short',
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                    second: '2-digit',
                    hour12: true
                };
                clockEl.textContent = new Intl.DateTimeFormat('en-PH', options).format(now);
            } catch (_) {
                clockEl.textContent = new Date().toLocaleTimeString('en-PH');
            }
        }
        updateTime();
        setInterval(updateTime, 1000);
    }

    // Guard against file:/// protocol
    function checkFileProtocol() {
        if (window.location.protocol === 'file:') {
            const banner = document.getElementById('fileProtocolBanner');
            if (banner) {
                banner.style.display = 'block';
            }
        }
    }

    // Navigation & Mobile Drawer
    function setupNavigation() {
        const hamburger = document.getElementById('govHamburger');
        const navLinks = document.getElementById('govNavLinks');

        if (hamburger && navLinks) {
            hamburger.addEventListener('click', () => {
                navLinks.classList.toggle('open');
            });

            // Close menu when link is clicked
            navLinks.querySelectorAll('a').forEach(link => {
                link.addEventListener('click', () => {
                    navLinks.classList.remove('open');
                });
            });
        }
    }

    // Loading screen progression
    function setupLoadingScreen() {
        const screen = document.getElementById('bmsLoadingScreen');
        const bar = document.getElementById('bmsLoadingBar');
        const percentText = document.getElementById('loadingPercent');
        const statusText = document.getElementById('loadingStatus');

        if (!screen) return;

        let progress = 0;
        const messages = [
            'Connecting to Barangay Government Portal...',
            'Preparing Official Services & Records...',
            'Loading Official Services & Bulletins...',
            'Preparing Community Programs & Bulletins...',
            'Welcome to Barangay Poblacion!'
        ];

        const interval = setInterval(() => {
            progress += Math.floor(Math.random() * 20) + 15;
            if (progress > 100) progress = 100;

            if (bar) bar.style.width = `${progress}%`;
            if (percentText) percentText.textContent = `${progress}%`;
            
            const msgIdx = Math.min(Math.floor((progress / 100) * messages.length), messages.length - 1);
            if (statusText) statusText.textContent = messages[msgIdx];

            if (progress >= 100) {
                clearInterval(interval);
                setTimeout(() => {
                    screen.classList.add('hidden');
                }, 400);
            }
        }, 100);
    }

    // Portal Selector Modal
    function setupPortalModal() {
        const modal = document.getElementById('portalModal');
        const closeBtn = document.getElementById('portalModalClose');
        const residentBtn = document.getElementById('portalResidentBtn');
        const officialBtn = document.getElementById('portalOfficialBtn');

        document.querySelectorAll('.open-portal-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.preventDefault();
                openPortalModal();
            });
        });

        if (closeBtn) {
            closeBtn.addEventListener('click', closePortalModal);
        }

        if (modal) {
            modal.addEventListener('click', (e) => {
                if (e.target === modal) closePortalModal();
            });
        }

        if (residentBtn) {
            residentBtn.addEventListener('click', (e) => {
                e.preventDefault();
                closePortalModal();
                showLoginView('resident');
            });
        }

        if (officialBtn) {
            officialBtn.addEventListener('click', (e) => {
                e.preventDefault();
                closePortalModal();
                showLoginView('official');
            });
        }
    }

    function openPortalModal() {
        const modal = document.getElementById('portalModal');
        if (modal) {
            modal.style.display = 'flex';
        }
    }

    function closePortalModal() {
        const modal = document.getElementById('portalModal');
        if (modal) {
            modal.style.display = 'none';
        }
    }

    function showLoginView(type = 'resident') {
        // Hide welcome page and reveal existing login wrapper
        const welcomePage = document.getElementById('welcomeLandingPage');
        const loginPage = document.getElementById('loginPage');

        if (welcomePage) welcomePage.style.display = 'none';
        if (loginPage) {
            loginPage.style.display = 'flex';
            window.scrollTo(0, 0);

            // Update login title based on portal selected
            const loginTitle = document.getElementById('loginPortalHeading');
            if (loginTitle) {
                loginTitle.textContent = type === 'official' ? '🏛️ Official / Staff Portal Sign-In' : '👤 Resident Portal Sign-In';
            }
            const userInput = document.getElementById('username');
            if (userInput) userInput.focus();
        }
    }

    function showWelcomePage() {
        const welcomePage = document.getElementById('welcomeLandingPage');
        const loginPage = document.getElementById('loginPage');
        const appContainer = document.getElementById('appContainer');
        const mainHeader = document.getElementById('mainHeader');
        const mainFooter = document.getElementById('mainFooter');

        if (loginPage) loginPage.style.display = 'none';
        if (appContainer) appContainer.style.display = 'none';
        if (mainHeader) mainHeader.style.display = 'none';
        if (mainFooter) mainFooter.style.display = 'none';

        if (welcomePage) {
            welcomePage.style.display = 'block';
            window.scrollTo(0, 0);
        }
    }

    // Gallery Lightbox
    function setupGallery() {
        const modal = document.getElementById('galleryModal');
        const modalImg = document.getElementById('galleryModalImg');
        const closeBtn = document.getElementById('galleryModalClose');

        document.querySelectorAll('.gallery-item').forEach(item => {
            item.addEventListener('click', () => {
                const img = item.querySelector('img');
                if (img && modal && modalImg) {
                    modalImg.src = img.src;
                    modal.style.display = 'flex';
                }
            });
        });

        if (closeBtn && modal) {
            closeBtn.addEventListener('click', () => {
                modal.style.display = 'none';
            });
            modal.addEventListener('click', (e) => {
                if (e.target === modal) modal.style.display = 'none';
            });
        }
    }

    // Server offline reconnect monitor (active only within authenticated app portal)
    function setupServerStatusMonitor() {
        const banner = document.getElementById('bmsServerBanner');
        if (!banner) return;

        if (window.BMSSQLite && typeof window.BMSSQLite.onConnectionChange === 'function') {
            window.BMSSQLite.onConnectionChange((state, msg) => {
                const appContainer = document.getElementById('appContainer');
                const isInsideApp = appContainer && appContainer.style.display !== 'none';
                
                // Do not display reconnect banners on the public landing page
                if (!isInsideApp) {
                    banner.style.display = 'none';
                    return;
                }

                if (state === 'disconnected') {
                    banner.style.display = 'flex';
                    banner.querySelector('.status-text').textContent = 'Server connection interrupted. Attempting to reconnect...';
                } else if (state === 'connected') {
                    banner.querySelector('.status-text').textContent = 'Server connected!';
                    setTimeout(() => {
                        banner.style.display = 'none';
                    }, 2500);
                }
            });
        }
    }

    return {
        init,
        openPortalModal,
        closePortalModal,
        showLoginView,
        showWelcomePage
    };
})();
