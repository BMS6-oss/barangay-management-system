/**
 * BMS SQLite API Client Layer
 * Centralized API configuration and request handler.
 * Communicates with the local server via HTTP.
 */
window.BMSSQLite = (() => {
    let token = (typeof sessionStorage !== 'undefined' && typeof sessionStorage.getItem === 'function')
        ? (sessionStorage.getItem('bmsToken') || '')
        : '';
    
    // Dynamic Origin Detection:
    // 1. Configured dynamically via localStorage ('bmsApiBaseUrl')
    // 2. Configured via window.BMS_API_BASE_URL or window.__BMS_CONFIG__.API_BASE_URL
    // 3. Configured via <meta name="bms-api-base-url" content="...">
    // 4. Detected static hosting (e.g. GitHub Pages) -> falls back to production cloud API
    // 5. Defaults to window.location.origin when served over same-origin HTTP/HTTPS
    // 6. Defaults to http://127.0.0.1:8000 when opened via local file protocol or local static dev servers
    function resolveApiBaseUrl() {
        if (typeof window !== 'undefined') {
            // 1. Check dynamic runtime override in localStorage
            try {
                const stored = window.localStorage && window.localStorage.getItem('bmsApiBaseUrl');
                if (stored && typeof stored === 'string' && stored.trim()) {
                    return stored.trim().replace(/\/+$/, '');
                }
            } catch (_) {}

            // 2. Check window.BMS_API_BASE_URL global
            if (window.BMS_API_BASE_URL && typeof window.BMS_API_BASE_URL === 'string' && window.BMS_API_BASE_URL.trim()) {
                return window.BMS_API_BASE_URL.trim().replace(/\/+$/, '');
            }

            // 3. Check window.__BMS_CONFIG__.API_BASE_URL (must be non-empty)
            if (window.__BMS_CONFIG__ && typeof window.__BMS_CONFIG__.API_BASE_URL === 'string' && window.__BMS_CONFIG__.API_BASE_URL.trim()) {
                return window.__BMS_CONFIG__.API_BASE_URL.trim().replace(/\/+$/, '');
            }

            // 4. Check <meta name="bms-api-base-url"> tag
            const metaTag = document.querySelector('meta[name="bms-api-base-url"]');
            if (metaTag && metaTag.content && metaTag.content.trim()) {
                return metaTag.content.trim().replace(/\/+$/, '');
            }

            // 5. Intelligent Environment & Origin Detection
            if (window.location) {
                const hostname = (window.location.hostname || '').toLowerCase();
                const protocol = (window.location.protocol || '').toLowerCase();

                // Static host detection (e.g., GitHub Pages or raw git CDN):
                // Static web servers cannot execute Python or accept POST requests.
                // Redirect API calls to the production cloud backend (Render).
                if (hostname.endsWith('github.io') || hostname === 'raw.githubusercontent.com') {
                    const cloudUrl = (window.__BMS_CONFIG__ && window.__BMS_CONFIG__.CLOUD_API_URL)
                        || 'https://barangay-management-system.onrender.com';
                    return cloudUrl.replace(/\/+$/, '');
                }

                // If running locally from file:// protocol, connect to local backend server
                if (protocol === 'file:') {
                    return 'http://127.0.0.1:8000';
                }

                // If running from a local static dev server (like VS Code Live Server on port 5500, 3000, 5173, etc.)
                if ((hostname === 'localhost' || hostname === '127.0.0.1') && window.location.port && window.location.port !== '8000') {
                    return 'http://127.0.0.1:8000';
                }

                // Standard same-origin hosting (e.g., Render Docker web service or local server.py)
                if (protocol === 'http:' || protocol === 'https:') {
                    return window.location.origin;
                }
            }
        }
        return 'http://127.0.0.1:8000';
    }

    let apiBaseUrl = resolveApiBaseUrl();
    let isServerReconnecting = false;
    let reconnectListeners = [];

    function notifyState(state, message) {
        reconnectListeners.forEach(fn => {
            try { fn(state, message); } catch(e) {}
        });
    }

    async function request(path, options = {}) {
        const headers = { 'Content-Type': 'application/json', ...(options.headers || {}) };
        if (token) headers.Authorization = `Bearer ${token}`;

        const baseUrl = apiBaseUrl || resolveApiBaseUrl();
        const url = path.startsWith('http') ? path : `${baseUrl}${path}`;

        let response;
        try {
            response = await fetch(url, { ...options, headers });
            if (isServerReconnecting) {
                isServerReconnecting = false;
                notifyState('connected', 'Server connection restored.');
            }
        } catch (error) {
            isServerReconnecting = true;
            notifyState('disconnected', 'BMS server connection temporarily interrupted.');
            const connErr = new Error('Unable to connect to the BMS server.');
            connErr.code = 'NETWORK_ERROR';
            connErr.status = 0;
            throw connErr;
        }

        let payload = {};
        try {
            payload = await response.json();
        } catch (e) {}

        if (!response.ok) {
            let msg = payload.error || '';
            let code = 'HTTP_ERROR';
            if (response.status === 401) {
                msg = 'Invalid username or password.';
                code = 'UNAUTHORIZED';
            } else if (response.status === 403) {
                msg = msg || 'Access denied. You do not have permission for this action.';
                code = 'FORBIDDEN';
            } else if (response.status === 404) {
                msg = 'The requested service endpoint was not found.';
                code = 'ENDPOINT_NOT_FOUND';
            } else if (response.status === 405) {
                msg = 'The authentication service received an unsupported method or request was routed to a static host. Please ensure the BMS backend server is connected.';
                code = 'METHOD_NOT_ALLOWED';
            } else if (response.status === 503) {
                msg = msg || 'BMS server is starting or database is initializing...';
                code = payload.code || 'SERVICE_UNAVAILABLE';
            } else if (response.status >= 500) {
                msg = 'The BMS server encountered an internal error.';
                code = 'DATABASE_ERROR';
            } else if (!msg) {
                msg = `Unable to complete request (${response.status}). Please try again.`;
            }

            const httpErr = new Error(msg);
            httpErr.code = payload.code || code;
            httpErr.status = response.status;
            throw httpErr;
        }
        return payload;
    }

    /**
     * Safe request wrapper for public-facing endpoints.
     * Prevents technical error leakage to public visitors
     * and normalizes network/backend failure into safe application-level responses.
     */
    async function publicRequest(path, fallbackData = null) {
        try {
            return await request(path);
        } catch (error) {
            return {
                success: false,
                available: false,
                code: error.code || 'UNAVAILABLE',
                error: 'Service temporarily unavailable',
                data: fallbackData
            };
        }
    }

    return {
        getBaseUrl() {
            return apiBaseUrl;
        },
        setBaseUrl(url) {
            if (typeof url === 'string') {
                apiBaseUrl = url.trim().replace(/\/+$/, '');
                try {
                    if (window.localStorage) {
                        if (apiBaseUrl) window.localStorage.setItem('bmsApiBaseUrl', apiBaseUrl);
                        else window.localStorage.removeItem('bmsApiBaseUrl');
                    }
                } catch (_) {}
            }
        },
        onConnectionChange(callback) {
            if (typeof callback === 'function') reconnectListeners.push(callback);
        },
        async health() {
            return request('/api/health');
        },
        async publicInfo() {
            return publicRequest('/api/public/info', null);
        },
        async publicAnnouncements() {
            return publicRequest('/api/public/announcements', []);
        },
        async publicPrograms() {
            return publicRequest('/api/public/programs', []);
        },
        async login(identifier, password) {
            const result = await request('/api/login', { method: 'POST', body: JSON.stringify({ identifier, username: identifier, password }) });
            token = result.token;
            if (typeof sessionStorage !== 'undefined' && typeof sessionStorage.setItem === 'function') {
                sessionStorage.setItem('bmsToken', token);
            }
            return result.user;
        },
        me() {
            return request('/api/me');
        },
        register(details) {
            return request('/api/register', { method: 'POST', body: JSON.stringify(details) });
        },
        async beginGoogleRegistration(details) {
            const result = await request('/api/google/start', { method: 'POST', body: JSON.stringify(details) });
            window.location.assign(result.authorization_url);
        },
        async logout() {
            try { await request('/api/logout', { method: 'POST' }); } catch (_) { /* local cleanup still logs out */ }
            token = '';
            if (typeof sessionStorage !== 'undefined' && typeof sessionStorage.removeItem === 'function') {
                sessionStorage.removeItem('bmsToken');
            }
        },
        saveEvent(event) {
            return request('/api/events', { method: 'POST', body: JSON.stringify(event) });
        },
        list(collection) {
            return request(`/api/${collection}`);
        },
        dashboardSummary() {
            return request('/api/dashboard-summary');
        },
        staffSummary() {
            return request('/api/staff-summary');
        },
        mySummary() {
            return request('/api/my-summary');
        },
        myProfile() {
            return request('/api/my-profile');
        },
        performance() {
            return request('/api/performance');
        },
        recentActivity() {
            return request('/api/recent-activity');
        },
        notifications() {
            return request('/api/notifications');
        },
        markNotificationsRead(ids) {
            return request('/api/notifications/read', { method: 'POST', body: JSON.stringify(ids ? { ids } : { all: true }) });
        },
        listUsers() {
            return request('/api/users');
        },
        listStaff() {
            return request('/api/staff');
        },
        listHouseholds() {
            return request('/api/households');
        },
        listCertificates() {
            return request('/api/certificates');
        },
        listPrograms(scope = '') {
            return request(`/api/programs${scope ? `?scope=${encodeURIComponent(scope)}` : ''}`);
        },
        createProgram(program) {
            return request('/api/programs', { method: 'POST', body: JSON.stringify(program) });
        },
        updateProgram(ref, changes) {
            return request(`/api/programs/${encodeURIComponent(ref)}`, { method: 'PUT', body: JSON.stringify(changes) });
        },
        archiveProgram(ref) {
            return request(`/api/programs/${encodeURIComponent(ref)}`, { method: 'DELETE' });
        },
        createAnnouncement(announcement) {
            return request('/api/announcements', { method: 'POST', body: JSON.stringify(announcement) });
        },
        archiveAnnouncement(id) {
            return request(`/api/announcements/${id}`, { method: 'DELETE' });
        },
        updateRequestStatus(ref, status, remarks = '') {
            return request(`/api/requests/${encodeURIComponent(ref)}`, { method: 'PUT', body: JSON.stringify({ status, remarks }) });
        },
        lguSettings() {
            return request('/api/lgu-settings');
        },
        updateLguSettings(settings) {
            return request('/api/lgu-settings', { method: 'PUT', body: JSON.stringify(settings) });
        },
        integrityCheck() {
            return request('/api/integrity-check');
        },
        listRegistrations() {
            return request('/api/registrations');
        },
        updateRegistration(id, status) {
            return request(`/api/registrations/${id}`, { method: 'PUT', body: JSON.stringify({ status }) });
        },
        listResidentIds() {
            return request('/api/resident-ids');
        },
        getClassificationSummary(query) {
            return request(`/api/classification-summary?${query.toString()}`);
        },
        getResidentIdSettings() {
            return request('/api/resident-id-settings');
        },
        updateResidentIdSettings(settings) {
            return request('/api/resident-id-settings', { method: 'PUT', body: JSON.stringify(settings) });
        },
        updateResidentIdStatus(id, status, remarks = '') {
            return request(`/api/resident-ids/${id}`, { method: 'PUT', body: JSON.stringify({ status, remarks }) });
        },
        replaceResidentId(id, remarks = '') {
            return request(`/api/resident-ids/${id}/replace`, { method: 'POST', body: JSON.stringify({ remarks }) });
        },
        updateResident(id, record) {
            return request(`/api/residents/${id}`, { method: 'PUT', body: JSON.stringify(record) });
        },
        deleteResident(id) {
            return request(`/api/residents/${id}`, { method: 'DELETE' });
        },
        getHeroImageSettings() {
            return request('/api/admin/hero-image');
        },
        deleteResidentProfilePhoto() {
            return this.removeResidentProfilePhoto();
        },
        uploadHeroImage(payload) {
            return request('/api/admin/hero-image', { method: 'POST', body: JSON.stringify(payload) });
        },
        resetHeroImage() {
            return request('/api/admin/hero-image', { method: 'POST', body: JSON.stringify({ reset: true }) });
        },
        updateHeroFocalPosition(focalPosition) {
            return request('/api/admin/hero-image', { method: 'POST', body: JSON.stringify({ focalPosition }) });
        },
        // ── Content Protection Settings ──────────────────────────────────
        /** Fetch protection display settings — public, no auth required. */
        getPublicProtectionSettings() {
            return publicRequest('/api/public/protection-settings', null);
        },
        /** Fetch full protection settings — admin only. */
        getAdminProtectionSettings() {
            return request('/api/admin/protection-settings');
        },
        /** Save content protection settings — admin only. */
        saveProtectionSettings(settings) {
            return request('/api/admin/protection-settings', { method: 'PUT', body: JSON.stringify(settings) });
        },
        // ── Public Dynamic Data ──────────────────────────────────────────
        getPublicOfficials() {
            return publicRequest('/api/public/officials', []);
        },
        getPublicGallery() {
            return publicRequest('/api/public/gallery', []);
        },
        // ── Admin Barangay Officials CRUD ────────────────────────────────
        getAdminOfficials(search = '', status = 'all') {
            const qs = new URLSearchParams();
            if (search) qs.set('search', search);
            if (status && status !== 'all') qs.set('status', status);
            const query = qs.toString() ? `?${qs.toString()}` : '';
            return request(`/api/admin/officials${query}`);
        },
        createOfficial(payload) {
            return request('/api/admin/officials', { method: 'POST', body: JSON.stringify(payload) });
        },
        createAdminOfficial(payload) {
            return this.createOfficial(payload);
        },
        updateOfficial(id, payload) {
            return request(`/api/admin/officials/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        },
        updateAdminOfficial(id, payload) {
            return this.updateOfficial(id, payload);
        },
        archiveOfficial(id) {
            return request(`/api/admin/officials/${id}/archive`, { method: 'POST', body: JSON.stringify({}) });
        },
        archiveAdminOfficial(id) {
            return this.archiveOfficial(id);
        },
        restoreOfficial(id) {
            return request(`/api/admin/officials/${id}/restore`, { method: 'POST', body: JSON.stringify({}) });
        },
        restoreAdminOfficial(id) {
            return this.restoreOfficial(id);
        },
        deleteOfficial(id) {
            return request(`/api/admin/officials/${id}`, { method: 'DELETE' });
        },
        deleteAdminOfficial(id) {
            return this.deleteOfficial(id);
        },
        reorderAdminOfficials(items) {
            return request('/api/admin/officials/reorder', { method: 'PUT', body: JSON.stringify({ items }) });
        },
        // ── Admin Gallery CRUD ───────────────────────────────────────────
        getAdminGallery(filter = '') {
            const qs = filter ? `?${new URLSearchParams(typeof filter === 'string' ? { filter } : filter).toString()}` : '';
            return request(`/api/admin/gallery${qs}`);
        },
        createGalleryItem(payload) {
            return request('/api/admin/gallery', { method: 'POST', body: JSON.stringify(payload) });
        },
        createAdminGalleryItem(payload) {
            return this.createGalleryItem(payload);
        },
        updateGalleryItem(id, payload) {
            return request(`/api/admin/gallery/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        },
        updateAdminGalleryItem(id, payload) {
            return this.updateGalleryItem(id, payload);
        },
        archiveGalleryItem(id) {
            return request(`/api/admin/gallery/${id}/archive`, { method: 'POST', body: JSON.stringify({}) });
        },
        archiveAdminGalleryItem(id) {
            return this.archiveGalleryItem(id);
        },
        restoreGalleryItem(id) {
            return request(`/api/admin/gallery/${id}/restore`, { method: 'POST', body: JSON.stringify({}) });
        },
        restoreAdminGalleryItem(id) {
            return this.restoreGalleryItem(id);
        },
        deleteGalleryItem(id) {
            return request(`/api/admin/gallery/${id}`, { method: 'DELETE' });
        },
        deleteAdminGalleryItem(id) {
            return this.deleteGalleryItem(id);
        },
        reorderGallery(order) {
            return request('/api/admin/gallery/reorder', { method: 'PUT', body: JSON.stringify({ order }) });
        },
        reorderAdminGallery(items) {
            return this.reorderGallery(items);
        },
        // ── Admin Website Settings (CMS) ─────────────────────────────────
        getWebsiteSettings() {
            return request('/api/admin/website-settings');
        },
        getAdminWebsiteSettings() {
            return this.getWebsiteSettings();
        },
        saveWebsiteSettings(settings) {
            return request('/api/admin/website-settings', { method: 'PUT', body: JSON.stringify(settings) });
        },
        updateAdminWebsiteSettings(settings) {
            return this.saveWebsiteSettings(settings);
        },
        // ── Resident Profile Picture ─────────────────────────────────────
        uploadResidentProfilePhoto(dataUri) {
            return request('/api/resident/profile-photo', { method: 'POST', body: JSON.stringify({ data: dataUri }) });
        },
        removeResidentProfilePhoto() {
            return request('/api/resident/profile-photo', { method: 'DELETE' });
        },
        adminRemoveResidentPhoto(residentId) {
            return request(`/api/admin/residents/${residentId}/profile-photo`, { method: 'DELETE' });
        },
        // ── Punong Barangay Command Center & Staff ───────────────────────
        pbDashboard() {
            return request('/api/punong-barangay/dashboard');
        },
        pbStaff() {
            return request('/api/punong-barangay/staff');
        },
        // ── Task Assignment & Workflow ───────────────────────────────────
        pbTasks() {
            return request('/api/punong-barangay/tasks');
        },
        createPbTask(payload) {
            return request('/api/punong-barangay/tasks', { method: 'POST', body: JSON.stringify(payload) });
        },
        staffTasks() {
            return request('/api/staff/tasks');
        },
        getTaskDetails(id) {
            return request(`/api/tasks/${id}`);
        },
        updateTaskStatus(id, status) {
            return request(`/api/tasks/${id}/status`, { method: 'POST', body: JSON.stringify({ status }) });
        },
        acknowledgeTask(id) {
            return request(`/api/tasks/${id}/acknowledge`, { method: 'POST', body: JSON.stringify({}) });
        },
        addTaskUpdate(id, payload) {
            return request(`/api/tasks/${id}/updates`, { method: 'POST', body: JSON.stringify(payload) });
        },
        holdTask(id, payload) {
            return request(`/api/tasks/${id}/hold`, { method: 'POST', body: JSON.stringify(payload) });
        },
        completeTask(id, payload) {
            return request(`/api/tasks/${id}/complete`, { method: 'POST', body: JSON.stringify(payload) });
        },
        // ── Admin PB & Staff Oversight ───────────────────────────────────
        adminGetPb() {
            return request('/api/admin/punong-barangay');
        },
        adminUpdatePb(payload) {
            return request('/api/admin/punong-barangay', { method: 'PUT', body: JSON.stringify(payload) });
        },
        adminArchiveStaff(id, reason = '') {
            return request(`/api/admin/staff/${id}/archive`, { method: 'PUT', body: JSON.stringify({ reason }) });
        },
        adminRestoreStaff(id) {
            return request(`/api/admin/staff/${id}/restore`, { method: 'PUT', body: JSON.stringify({}) });
        },
        adminStaffList(filters = {}) {
            const qs = new URLSearchParams(filters);
            return request(`/api/admin/staff${qs.toString() ? `?${qs}` : ''}`);
        },
        createAdminStaff(payload) {
            return request('/api/admin/staff', { method: 'POST', body: JSON.stringify(payload) });
        },
        updateAdminStaff(id, payload) {
            return request(`/api/admin/staff/${id}`, { method: 'PUT', body: JSON.stringify(payload) });
        },
        updateStaff(id, payload) {
            return this.updateAdminStaff(id, payload);
        },
        assignStaffHandler(id, handlerUserId, reason = '') {
            return request(`/api/admin/staff/${id}/assign-handler`, { method: 'POST', body: JSON.stringify({ handlerUserId, reason }) });
        },
        activateAdminStaff(id) {
            return request(`/api/admin/staff/${id}/activate`, { method: 'POST', body: JSON.stringify({}) });
        },
        deactivateAdminStaff(id) {
            return request(`/api/admin/staff/${id}/deactivate`, { method: 'POST', body: JSON.stringify({}) });
        },
        getAdminStaffHistory(id) {
            return request(`/api/admin/staff/${id}/history`);
        },
        adminTaskOversight() {
            return request('/api/admin/task-oversight');
        }
    };
})();