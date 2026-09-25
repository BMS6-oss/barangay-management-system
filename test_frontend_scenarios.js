/**
 * Frontend JavaScript Simulation & Scenario Validation
 * Tests BMSSQLite and BMSWelcomeData against:
 * 1. Live server
 * 2. Stopped / unreachable server
 * 3. Fallback loading and DOM insertion
 * 4. Error message cleanliness verification
 */

const fs = require('fs');
const path = require('path');

// Minimal DOM simulation for Node
class MockElement {
    constructor(id = '', tagName = 'div') {
        this.id = id;
        this.tagName = tagName.toUpperCase();
        this.style = {};
        this._text = '';
        this.innerHTML = '';
        this.children = [];
        this.classList = {
            add: () => {},
            remove: () => {},
            toggle: () => {}
        };
    }
    set textContent(val) {
        this._text = val;
        this.innerHTML = val ? String(val) : '';
    }
    get textContent() { return this._text; }
    querySelector() { return null; }
    querySelectorAll() { return []; }
    appendChild(c) { this.children.push(c); }
    addEventListener() {}
}

const elements = {
    heroBgImage: new MockElement('heroBgImage'),
    heroSealBadgeText: new MockElement('heroSealBadgeText'),
    heroMainTitle: new MockElement('heroMainTitle'),
    heroSubtitle: new MockElement('heroSubtitle'),
    btnHeroPrimaryText: new MockElement('btnHeroPrimaryText'),
    btnHeroSecondaryText: new MockElement('btnHeroSecondaryText'),
    heroStatResidents: new MockElement('heroStatResidents'),
    heroStatPrograms: new MockElement('heroStatPrograms'),
    heroStatAnnouncements: new MockElement('heroStatAnnouncements'),
    announcementsFeed: new MockElement('announcementsFeed'),
    programsFeed: new MockElement('programsFeed'),
    galleryGrid: new MockElement('galleryGrid'),
    officialLeadCard: new MockElement('officialLeadCard'),
    officialsTeamGrid: new MockElement('officialsTeamGrid'),
    fileProtocolBanner: new MockElement('fileProtocolBanner'),
    bmsServerBanner: new MockElement('bmsServerBanner'),
    appContainer: new MockElement('appContainer')
};

elements.bmsServerBanner.querySelector = (sel) => {
    if (sel === '.status-text') return elements.bmsServerBanner;
    return null;
};

// Global mock setup
global.window = {
    location: {
        protocol: 'http:',
        origin: 'http://127.0.0.1:8000'
    },
    sessionStorage: {
        getItem: () => '',
        setItem: () => {},
        removeItem: () => {}
    }
};

global.document = {
    getElementById: (id) => elements[id] || new MockElement(id),
    querySelector: (sel) => {
        if (sel === '.status-text') return new MockElement('status-text');
        return null;
    },
    querySelectorAll: () => [],
    createElement: (tag) => new MockElement('', tag)
};

global.fetch = globalThis.fetch;
global.Image = class {
    set src(val) {
        if (this.onload) setTimeout(() => this.onload(), 10);
    }
};

// Load client scripts
const sqliteApiCode = fs.readFileSync(path.join(__dirname, 'sqlite-api.js'), 'utf-8');
const welcomeDataCode = fs.readFileSync(path.join(__dirname, 'js', 'welcome-data.js'), 'utf-8');

eval(sqliteApiCode);
eval(welcomeDataCode);

async function runTests() {
    console.log("=== RUNNING FRONTEND JAVASCRIPT SIMULATION TESTS ===");
    let passed = 0;
    let failed = 0;

    function assert(cond, name, details = '') {
        if (cond) {
            console.log(`  [PASS] ${name}`);
            passed++;
        } else {
            console.log(`  [FAIL] ${name} ${details ? '(' + details + ')' : ''}`);
            failed++;
        }
    }

    // 1. Live Server Tests
    console.log("\n[Scenario 1] Live Server Request");
    try {
        const liveProgs = await window.BMSSQLite.publicPrograms();
        assert(Array.isArray(liveProgs), "Live publicPrograms returns Array");
        const liveAnn = await window.BMSSQLite.publicAnnouncements();
        assert(Array.isArray(liveAnn), "Live publicAnnouncements returns Array");
    } catch (e) {
        assert(false, "Live server requests should not throw", e.message);
    }

    // 2. Unreachable / Stopped Server Tests
    console.log("\n[Scenario 2] Server Stopped / Unreachable");
    window.BMSSQLite.setBaseUrl('http://127.0.0.1:9999'); // Point to nonexistent port

    let stoppedProgs, stoppedAnn, stoppedInfo;
    try {
        stoppedProgs = await window.BMSSQLite.publicPrograms();
        stoppedAnn = await window.BMSSQLite.publicAnnouncements();
        stoppedInfo = await window.BMSSQLite.publicInfo();

        assert(stoppedProgs && stoppedProgs.available === false, "Unreachable server returns normalized available: false for programs");
        assert(stoppedAnn && stoppedAnn.available === false, "Unreachable server returns normalized available: false for announcements");
        assert(stoppedInfo && stoppedInfo.available === false, "Unreachable server returns normalized available: false for info");
    } catch (e) {
        assert(false, "Unreachable server must NOT throw unhandled rejection", e.message);
    }

    // 3. UI Fallback Rendering with Unreachable Server
    console.log("\n[Scenario 3] Welcome Page Fallback Rendering with Unreachable Server");
    // Mock local fetch for fallback JSON files to simulate reading local JSON files
    const origFetch = global.fetch;
    global.fetch = async (url, opts) => {
        if (url === './data/public-programs.json') {
            const data = fs.readFileSync(path.join(__dirname, 'data', 'public-programs.json'), 'utf-8');
            return { ok: true, json: async () => JSON.parse(data) };
        }
        if (url === './data/public-announcements.json') {
            const data = fs.readFileSync(path.join(__dirname, 'data', 'public-announcements.json'), 'utf-8');
            return { ok: true, json: async () => JSON.parse(data) };
        }
        return origFetch(url, opts);
    };

    try {
        await window.BMSWelcomeData.loadPublicData();
        
        const progsHtml = elements.programsFeed.innerHTML;
        const annHtml = elements.announcementsFeed.innerHTML;

        assert(progsHtml.length > 50, "Programs feed is populated with safe content");
        assert(annHtml.length > 50, "Announcements feed is populated with safe content");

        // Verify zero technical leakages in HTML
        const allHtml = progsHtml + annHtml;
        const forbidden = ["start_bms.bat", "SERVER_UNAVAILABLE", "Unable to connect to the BMS server", "status: 0", "D:/Antigravetty/BMS"];
        for (const word of forbidden) {
            assert(!allHtml.includes(word), `Rendered HTML does NOT contain '${word}'`);
        }

        // Verify friendly content exists
        assert(allHtml.includes("Community Health") || allHtml.includes("Programs"), "Rendered HTML contains safe community program info");
        assert(allHtml.includes("Public Assistance") || allHtml.includes("Announcements"), "Rendered HTML contains safe community announcement info");

    } catch (e) {
        assert(false, "loadPublicData failed unexpectedly", e.message);
    } finally {
        global.fetch = origFetch;
    }

    console.log(`\nSimulation Results: ${passed} passed, ${failed} failed`);
    if (failed > 0) {
        process.exitCode = 1;
    }
}

runTests();
