/**
 * BMS Welcome Page Data Integration
 * Fetches real SQLite data via public backend endpoints and populates the UI.
 */
window.BMSWelcomeData = (() => {
    let publicInfoCache = null;

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function formatDate(dateStr) {
        if (!dateStr) return '—';
        const d = new Date(dateStr);
        if (Number.isNaN(d.getTime())) return dateStr;
        return d.toLocaleDateString('en-PH', { year: 'numeric', month: 'short', day: 'numeric' });
    }

    function applyHeroImage(imagePath, focalPosition = 'center', updatedAt = '') {
        const bgEl = document.getElementById('heroBgImage');
        if (!bgEl) return;

        const defaultHero = './assets/hero1.jpg';
        const targetSrc = imagePath || defaultHero;
        const cacheBuster = updatedAt ? `?v=${encodeURIComponent(updatedAt)}` : `?v=${Date.now()}`;
        const finalUrl = `${targetSrc}${cacheBuster}`;

        const img = new Image();
        img.onload = () => {
            bgEl.style.backgroundImage = `url('${finalUrl}')`;
            bgEl.style.backgroundPosition = `center ${focalPosition || 'center'}`;
            if (window.BMSContentProtection && typeof window.BMSContentProtection.applyHeroWatermark === 'function') {
                window.BMSContentProtection.applyHeroWatermark();
            }
        };
        img.onerror = () => {
            console.warn(`Custom hero image failed to load (${finalUrl}). Reverting to default hero photo.`);
            bgEl.style.backgroundImage = `url('${defaultHero}')`;
            bgEl.style.backgroundPosition = 'center center';
            if (window.BMSContentProtection && typeof window.BMSContentProtection.applyHeroWatermark === 'function') {
                window.BMSContentProtection.applyHeroWatermark();
            }
        };
        img.src = finalUrl;
    }

    // Embedded Safe Public Fallback Data (available even offline or when backend is temporarily unreachable)
    const EMBEDDED_FALLBACK_PROGRAMS = [
        {
            event_id: 'prog-pub-01',
            title: 'Community Health & Wellness Program',
            description: 'Comprehensive primary healthcare consultations, preventive check-ups, and maternal-child health advisories at the Barangay Health Station.',
            event_date: 'Regular Community Schedule',
            start_time: '8:00 AM',
            end_time: '5:00 PM',
            location: 'Barangay Health Center',
            organizer: 'Barangay Health Committee',
            status: 'Ongoing'
        },
        {
            event_id: 'prog-pub-02',
            title: 'Solid Waste Management & Green Initiative',
            description: 'Community-wide ecological waste segregation campaign, clean-up drives, and urban gardening workshops for residents.',
            event_date: 'Regular Community Schedule',
            start_time: '7:00 AM',
            end_time: '11:00 AM',
            location: 'Barangay Plaza & Key Streets',
            organizer: 'Committee on Environment',
            status: 'Scheduled'
        },
        {
            event_id: 'prog-pub-03',
            title: 'Youth Leadership & Skills Development Workshop',
            description: 'Educational development, digital literacy training, and civic engagement workshops organized by the Sangguniang Kabataan.',
            event_date: 'Regular Community Schedule',
            start_time: '9:00 AM',
            end_time: '3:00 PM',
            location: 'Barangay Multi-Purpose Hall',
            organizer: 'Sangguniang Kabataan (SK)',
            status: 'Scheduled'
        }
    ];

    const EMBEDDED_FALLBACK_ANNOUNCEMENTS = [
        {
            id: 'ann-pub-01',
            title: 'Barangay Public Assistance & Service Desk Hours',
            body: 'The Barangay Hall is open Monday through Friday from 8:00 AM to 5:00 PM for document clearances, barangay certifications, and citizen assistance.',
            category: 'Advisory',
            priority: 'Normal',
            created_at: 'Regular Advisory'
        },
        {
            id: 'ann-pub-02',
            title: 'Disaster Preparedness & 24/7 Emergency Hotlines',
            body: 'For urgent medical assistance, emergency response, or fire dispatch, please dial 117 / 911 or contact the Barangay Emergency Operations Desk.',
            category: 'Public Safety',
            priority: 'High',
            created_at: 'Official Bulletin'
        }
    ];

    async function loadFallbackPrograms() {
        try {
            const resp = await fetch('./data/public-programs.json');
            if (resp.ok) {
                const json = await resp.json();
                if (json && Array.isArray(json.programs) && json.programs.length > 0) {
                    return json.programs;
                }
            }
        } catch (_) {}
        return EMBEDDED_FALLBACK_PROGRAMS;
    }

    async function loadFallbackAnnouncements() {
        try {
            const resp = await fetch('./data/public-announcements.json');
            if (resp.ok) {
                const json = await resp.json();
                if (json && Array.isArray(json.announcements) && json.announcements.length > 0) {
                    return json.announcements;
                }
            }
        } catch (_) {}
        return EMBEDDED_FALLBACK_ANNOUNCEMENTS;
    }

    async function loadPublicData() {
        try {
            const info = await window.BMSSQLite.publicInfo();
            if (info && info.available !== false && typeof info === 'object') {
                publicInfoCache = info;

                // 1. Update Barangay Name & Municipality across UI
                const bName = info.barangayName || 'Barangay Poblacion';
                const mName = info.municipality || 'City of Manila';

                document.querySelectorAll('.dyn-barangay-name').forEach(el => {
                    el.textContent = bName;
                });
                document.querySelectorAll('.dyn-muni-name').forEach(el => {
                    el.textContent = mName;
                });

                // 2. Apply Dynamic Hero Photograph & Overlay Opacity
                applyHeroImage(info.heroImage, info.heroImageFocalPosition, info.heroImageUpdatedAt);

                if (info.heroOverlayOpacity !== undefined) {
                    const overlay = document.querySelector('.hero-gradient-overlay');
                    if (overlay) {
                        const op = Math.max(0, Math.min(100, Number(info.heroOverlayOpacity))) / 100;
                        overlay.style.opacity = op.toString();
                    }
                }

                // 3. Dynamic Hero Texts & Badge
                if (info.welcomeBadge) {
                    const badgeTextEl = document.getElementById('heroSealBadgeText');
                    if (badgeTextEl) badgeTextEl.textContent = info.welcomeBadge;
                }
                if (info.welcomeTitle) {
                    const titleEl = document.getElementById('heroMainTitle');
                    if (titleEl) {
                        titleEl.innerHTML = escapeHtml(info.welcomeTitle).replace(
                            escapeHtml(bName),
                            `<span class="dyn-barangay-name highlight-text">${escapeHtml(bName)}</span>`
                        );
                    }
                }
                if (info.welcomeSubtitle) {
                    const subtitleEl = document.getElementById('heroSubtitle');
                    if (subtitleEl) subtitleEl.textContent = `"${info.welcomeSubtitle}"`;
                }

                // 4. Dynamic Button Labels
                if (info.btnPortalLabel) {
                    const btnPrimary = document.getElementById('btnHeroPrimaryText');
                    if (btnPrimary) btnPrimary.textContent = info.btnPortalLabel;
                    const navLoginBtn = document.querySelector('.btn-portal-login');
                    if (navLoginBtn) {
                        const icon = navLoginBtn.querySelector('span');
                        navLoginBtn.innerHTML = '';
                        if (icon) navLoginBtn.appendChild(icon);
                        navLoginBtn.appendChild(document.createTextNode(' ' + info.btnPortalLabel));
                    }
                }
                if (info.btnServicesLabel) {
                    const btnSec = document.getElementById('btnHeroSecondaryText');
                    if (btnSec) btnSec.textContent = info.btnServicesLabel;
                }

                // 5. Update Hero Quick Stats
                if (info.stats) {
                    const resEl = document.getElementById('heroStatResidents');
                    const progEl = document.getElementById('heroStatPrograms');
                    const annEl = document.getElementById('heroStatAnnouncements');

                    if (resEl) resEl.textContent = Number(info.stats.residents || 0).toLocaleString('en-US');
                    if (progEl) progEl.textContent = Number(info.stats.programs || 0).toLocaleString('en-US');
                    if (annEl) annEl.textContent = Number(info.stats.announcements || 0).toLocaleString('en-US');
                }

                // 6. Update Contact Details across UI
                if (info.contactAddress) {
                    document.querySelectorAll('.dyn-contact-address').forEach(el => el.textContent = info.contactAddress);
                }
                if (info.contactPhone) {
                    document.querySelectorAll('.dyn-contact-phone').forEach(el => el.textContent = info.contactPhone);
                }
                if (info.contactEmail) {
                    document.querySelectorAll('.dyn-contact-email').forEach(el => {
                        el.textContent = info.contactEmail;
                        if (el.tagName === 'A') el.href = `mailto:${info.contactEmail}`;
                    });
                }
                if (info.contactHours) {
                    document.querySelectorAll('.dyn-contact-hours').forEach(el => el.textContent = info.contactHours);
                }
                if (info.emergencyHotline) {
                    document.querySelectorAll('.dyn-contact-emergency').forEach(el => el.textContent = info.emergencyHotline);
                }
            }
        } catch (_) {
            // Non-fatal: default UI content in HTML remains visible
        }

        // 7. Load & Render Officials
        try {
            const officials = await window.BMSSQLite.getPublicOfficials();
            const list = Array.isArray(officials) ? officials : (officials && Array.isArray(officials.data) ? officials.data : []);
            renderOfficials(list, publicInfoCache);
        } catch (_) {
            renderOfficials([], publicInfoCache);
        }

        // 8. Load & Render Community Gallery
        try {
            const gallery = await window.BMSSQLite.getPublicGallery();
            const list = Array.isArray(gallery) ? gallery : (gallery && Array.isArray(gallery.data) ? gallery.data : []);
            renderGallery(list);
        } catch (_) {
            renderGallery([]);
        }

        // 9. Render Announcements (Live API -> Fallback Data -> Clean Message)
        try {
            let announcements = null;
            if (window.BMSSQLite && typeof window.BMSSQLite.publicAnnouncements === 'function') {
                announcements = await window.BMSSQLite.publicAnnouncements();
            }
            if (Array.isArray(announcements)) {
                renderAnnouncements(announcements);
            } else if (announcements && announcements.available === false) {
                const fallback = await loadFallbackAnnouncements();
                renderAnnouncements(fallback);
            } else {
                renderAnnouncements([]);
            }
        } catch (_) {
            const fallback = await loadFallbackAnnouncements();
            renderAnnouncements(fallback);
        }

        // 10. Render Programs (Live API -> Fallback Data -> Clean Message)
        try {
            let programs = null;
            if (window.BMSSQLite && typeof window.BMSSQLite.publicPrograms === 'function') {
                programs = await window.BMSSQLite.publicPrograms();
            }
            if (Array.isArray(programs)) {
                renderPrograms(programs);
            } else if (programs && programs.available === false) {
                const fallback = await loadFallbackPrograms();
                renderPrograms(fallback);
            } else {
                renderPrograms([]);
            }
        } catch (_) {
            const fallback = await loadFallbackPrograms();
            renderPrograms(fallback);
        }

        // Setup Lightbox Event Listeners
        setupGalleryLightbox();

        // Apply content protection to newly rendered images
        if (window.BMSContentProtection && typeof window.BMSContentProtection.hardenAllPublicImages === 'function') {
            window.BMSContentProtection.hardenAllPublicImages();
        }
    }

    function renderOfficials(officials, info) {
        const leadCard = document.getElementById('officialLeadCard');
        const teamGrid = document.getElementById('officialsTeamGrid');
        if (!leadCard || !teamGrid) return;

        // If no officials returned from dedicated table, fallback to info.punongBarangay
        if (!officials || officials.length === 0) {
            const pbName = (info && info.punongBarangay) ? info.punongBarangay : '';
            leadCard.innerHTML = `
                <div class="official-card official-lead-card">
                    <div class="official-avatar-wrap">
                        <span class="official-avatar-placeholder">🏛️</span>
                    </div>
                    <span class="official-role-badge">Punong Barangay</span>
                    <h3 class="official-name">${escapeHtml(pbName)}</h3>
                    <p class="official-title">Barangay Captain / Chief Executive</p>
                </div>
            `;
            teamGrid.innerHTML = `
                <div class="feed-empty-state">
                    <span class="empty-icon">👥</span>
                    <p>Official directory records will appear here as registered by the administration.</p>
                </div>
            `;
            return;
        }

        // Find Punong Barangay or first official as lead
        const leadOfficial = officials.find(o => (o.position || '').toLowerCase().includes('punong') || (o.position || '').toLowerCase().includes('captain')) || officials[0];
        const otherOfficials = officials.filter(o => o.id !== leadOfficial.id);

        // Lead Card Rendering
        const leadPhoto = leadOfficial.public_path || leadOfficial.photo_path;
        leadCard.innerHTML = `
            <div class="official-card official-lead-card">
                <div class="official-avatar-wrap">
                    ${leadPhoto ? 
                        `<img src="${escapeHtml(leadPhoto)}" alt="${escapeHtml(leadOfficial.name)}" class="official-avatar-img" onerror="this.outerHTML='<span class=\\'official-avatar-placeholder\\'>🏛️</span>'">` :
                        `<span class="official-avatar-placeholder">🏛️</span>`
                    }
                </div>
                <span class="official-role-badge">${escapeHtml(leadOfficial.position)}</span>
                <h3 class="official-name">${escapeHtml(leadOfficial.name)}</h3>
                <p class="official-title">${escapeHtml(leadOfficial.committee || 'Barangay Captain / Chief Executive')}</p>
                ${leadOfficial.term_years ? `<span class="official-term-tag">Term: ${escapeHtml(leadOfficial.term_years)}</span>` : ''}
            </div>
        `;

        // Team Grid Rendering
        if (otherOfficials.length === 0) {
            teamGrid.innerHTML = '';
            return;
        }

        teamGrid.innerHTML = otherOfficials.map(official => {
            const photo = official.public_path || official.photo_path;
            const icon = (official.position || '').toLowerCase().includes('secretary') ? '📝' :
                         (official.position || '').toLowerCase().includes('treasurer') ? '💼' :
                         (official.position || '').toLowerCase().includes('sk') ? '🌟' : '👤';
            return `
                <div class="official-card">
                    <div class="official-avatar-wrap">
                        ${photo ? 
                            `<img src="${escapeHtml(photo)}" alt="${escapeHtml(official.name)}" class="official-avatar-img" onerror="this.outerHTML='<span class=\\'official-avatar-placeholder\\'>${icon}</span>'">` :
                            `<span class="official-avatar-placeholder">${icon}</span>`
                        }
                    </div>
                    <span class="official-role-badge">${escapeHtml(official.position)}</span>
                    <h4 class="official-name">${escapeHtml(official.name)}</h4>
                    <p class="official-title">${escapeHtml(official.committee || 'Sangguniang Barangay')}</p>
                    ${official.term_years ? `<span class="official-term-tag">Term: ${escapeHtml(official.term_years)}</span>` : ''}
                </div>
            `;
        }).join('');
    }

    function renderGallery(items) {
        const grid = document.querySelector('.gallery-grid') || document.getElementById('galleryGrid');
        if (!grid) return;

        if (!items || items.length === 0) {
            grid.innerHTML = `
                <div class="feed-empty-state" style="grid-column: 1 / -1; text-align: center; padding: 3rem;">
                    <span class="empty-icon" style="font-size: 2.5rem;">📸</span>
                    <h3>Community Gallery</h3>
                    <p>Photographs capturing community milestones, public projects, and civic events will be posted here.</p>
                </div>
            `;
            return;
        }

        grid.innerHTML = items.map(item => {
            const src = item.public_path || item.image_path;
            return `
                <div class="gallery-item" data-full="${escapeHtml(src)}" data-caption="${escapeHtml(item.caption)}" data-desc="${escapeHtml(item.description || '')}">
                    <img src="${escapeHtml(src)}" alt="${escapeHtml(item.caption)}" loading="lazy" onerror="this.src='./assets/logo.png'">
                    <div class="gallery-item-caption">
                        <strong>${escapeHtml(item.caption)}</strong>
                        ${item.description ? `<span class="gallery-item-desc">${escapeHtml(item.description)}</span>` : ''}
                    </div>
                </div>
            `;
        }).join('');
    }

    function setupGalleryLightbox() {
        const modal = document.getElementById('galleryModal');
        const modalImg = document.getElementById('galleryModalImg');
        const modalClose = document.getElementById('galleryModalClose');
        if (!modal || !modalImg) return;

        // Add caption box inside modal if not present
        let modalCaption = document.getElementById('galleryModalCaption');
        if (!modalCaption) {
            const content = modal.querySelector('.gallery-modal-content') || modal;
            modalCaption = document.createElement('div');
            modalCaption.id = 'galleryModalCaption';
            modalCaption.className = 'gallery-modal-caption';
            content.appendChild(modalCaption);
        }

        document.querySelectorAll('.gallery-item').forEach(item => {
            item.onclick = () => {
                const fullSrc = item.getAttribute('data-full') || item.querySelector('img')?.src;
                const caption = item.getAttribute('data-caption') || '';
                const desc = item.getAttribute('data-desc') || '';
                if (fullSrc) {
                    modalImg.src = fullSrc;
                    modalCaption.innerHTML = `<h4>${escapeHtml(caption)}</h4>${desc ? `<p>${escapeHtml(desc)}</p>` : ''}`;
                    modal.style.display = 'flex';
                }
            };
        });

        if (modalClose) {
            modalClose.onclick = () => {
                modal.style.display = 'none';
            };
        }
        modal.onclick = (e) => {
            if (e.target === modal) {
                modal.style.display = 'none';
            }
        };
    }

    function renderAnnouncements(items, error = null) {
        const container = document.getElementById('announcementsFeed');
        if (!container) return;

        if (error) {
            container.innerHTML = `
                <div class="feed-empty-state">
                    <span class="empty-icon">📢</span>
                    <h3>Announcements Temporarily Unavailable</h3>
                    <p>Official community announcements published by the barangay administration will be displayed here. Please check back shortly or visit the Barangay Hall.</p>
                </div>
            `;
            return;
        }

        if (!items || !Array.isArray(items) || items.length === 0) {
            container.innerHTML = `
                <div class="feed-empty-state">
                    <span class="empty-icon">📢</span>
                    <h3>No Announcements At This Time</h3>
                    <p>Official community announcements published by the barangay administration will be displayed here.</p>
                </div>
            `;
            return;
        }

        container.innerHTML = items.map(a => {
            const isUrgent = (a.priority || '').toLowerCase() === 'urgent' || (a.priority || '').toLowerCase() === 'high';
            return `
                <div class="feed-card">
                    <div class="feed-card-header">
                        <span class="feed-tag ${isUrgent ? 'urgent' : ''}">${escapeHtml(a.category || 'General')}</span>
                        <span class="feed-date">${formatDate(a.created_at)}</span>
                    </div>
                    <h3 class="feed-title">${escapeHtml(a.title)}</h3>
                    <div class="feed-body">${escapeHtml(a.body)}</div>
                </div>
            `;
        }).join('');
    }

    function renderPrograms(items, error = null) {
        const container = document.getElementById('programsFeed');
        if (!container) return;

        if (error) {
            container.innerHTML = `
                <div class="feed-empty-state">
                    <span class="empty-icon">📅</span>
                    <h3>Programs Temporarily Unavailable</h3>
                    <p>Community programs and event schedules are temporarily unavailable. Please check back later or contact the Barangay Hall for current schedules.</p>
                </div>
            `;
            return;
        }

        if (!items || !Array.isArray(items) || items.length === 0) {
            container.innerHTML = `
                <div class="feed-empty-state">
                    <span class="empty-icon">📅</span>
                    <h3>No Community Programs Scheduled</h3>
                    <p>Upcoming barangay programs, medical missions, and activities will be listed here.</p>
                </div>
            `;
            return;
        }

        container.innerHTML = items.map(p => {
            return `
                <div class="feed-card">
                    <div class="feed-card-header">
                        <span class="feed-tag">${escapeHtml(p.status || 'Scheduled')}</span>
                        <span class="feed-date">📅 ${formatDate(p.event_date)}</span>
                    </div>
                    <h3 class="feed-title">${escapeHtml(p.title)}</h3>
                    <p class="feed-body">${escapeHtml(p.description || '')}</p>
                    <div class="feed-meta-row">
                        ${p.start_time ? `<span>🕒 ${escapeHtml(p.start_time)}${p.end_time ? ' - ' + escapeHtml(p.end_time) : ''}</span>` : ''}
                        ${p.location ? `<span>📍 ${escapeHtml(p.location)}</span>` : ''}
                        ${p.organizer ? `<span>👤 ${escapeHtml(p.organizer)}</span>` : ''}
                    </div>
                </div>
            `;
        }).join('');
    }

    return {
        loadPublicData,
        applyHeroImage,
        getPublicInfo() { return publicInfoCache; }
    };
})();
