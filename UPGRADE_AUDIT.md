# Barangay Management System Upgrade Audit

Date: 2026-08-25

## Stack Inventory

- Frontend: static HTML, inline CSS, and browser JavaScript in `index.html`.
- Backend: Python standard-library `http.server` with `ThreadingHTTPServer` in `server.py`.
- Database: SQLite (`bms.sqlite3`) accessed with Python's built-in `sqlite3` module.
- ORM, migrations, package manager, email provider, PDF library, and frontend framework: not present.
- Authentication: in-memory bearer tokens backed by seeded demo users.
- API: small JSON API under `/api`, plus an event-bus UI layer.

## Working Features

- Local server startup and static asset serving.
- SQLite initialization with seeded admin, staff, and resident accounts.
- Login and role checks for the existing API collections.
- Resident listing for authorized admin/staff users.
- Resident-owned request listing for resident users.
- Resident update and admin-only resident removal endpoint.
- Event persistence for residents, classifications, requests, tasks, announcements, certificates, and activity logs.
- Existing role-specific dashboard markup and responsive table wrappers.

## Partially Implemented

- Resident CRUD is split between static sample rows and API data; add/edit/delete UI is incomplete.
- Document requests are event payloads rather than a validated workflow with status transitions.
- Certificate issuance is browser-generated/print-based, not server-authorized PDF generation.
- Audit logs are persisted, but the browser also maintains a separate volatile log.
- Notifications are UI-only and admin-only; there is no durable notification table or read state.
- Announcements and classifications have persistence but no complete management lifecycle.
- Search, pagination, reporting, verification, registration, email verification, and password reset are absent.

## Broken or Risky Behavior Found

- Passwords were stored and checked with unsalted SHA-256.
- Tokens had no expiry or server-side logout revocation.
- Event POST accepted arbitrary event payloads and client role metadata.
- Residents could submit forged resident IDs and request IDs; the browser controlled identity fields.
- `INSERT OR REPLACE` could overwrite records unexpectedly.
- Resident deletion was destructive and did not report missing records.
- Archived records were not supported.
- Malformed JSON on some write paths could produce an unhandled server exception.
- Several views use `innerHTML` with event/user-provided values, creating an XSS risk if untrusted text reaches the UI.
- The demo credentials are embedded in the frontend and seeded by default, which is unsuitable for deployment.

## Upgrade Work Completed

- Replaced new password storage with PBKDF2-HMAC-SHA256 and automatic compatibility upgrade for existing SHA-256 demo hashes.
- Added eight-hour token expiry and `/api/logout` revocation.
- Added request size/type validation for JSON bodies.
- Added server-side event allowlists by role; client-supplied role metadata is no longer authorization.
- Server now generates request references and binds request ownership to the authenticated user.
- Replaced destructive resident deletion with soft archiving and hid archived residents from active listings.
- Added not-found handling for resident updates and archives.
- Added `UPGRADE_AUDIT.md` as the baseline audit and implementation record.
- Added Google OpenID Connect authorization-code registration, server-side verified-email checks, duplicate Google-link prevention, and persistent Google identity fields.
- Restricted CORS to the explicit `BMS_ALLOWED_ORIGINS` allowlist instead of reflecting arbitrary request origins.
- Removed password alias acceptance; PBKDF2 and legacy SHA-256 hashes now verify only the supplied password, with legacy hashes still upgraded after a successful login.
- Added baseline security headers, configurable HTTPS/HSTS signaling, endpoint rate limits, and safe malformed request-length handling.

## Prioritized Next Plan

1. Replace the generic event endpoint with typed request, resident, announcement, and certificate APIs using server-side validation.
2. Add durable account status, registration matching, email verification, password reset, login throttling, and secure deployment configuration.
3. Add request status transitions, staff/admin authorization, certificate verification codes, QR/PDF generation, and protected downloads.
4. Add durable notifications, programs, household relationships, audit metadata, and admin statistics endpoints.
5. Split the legacy inline frontend into reusable modules while preserving the current pages and visual identity.
6. Add automated API tests for authentication, ownership isolation, validation, status transitions, archiving, and document verification.
7. Add migration/backup documentation, production configuration, and responsive browser testing at the required viewport sizes.

## 2026-09-13 Security Update

- `config.py` now exposes `ALLOWED_ORIGINS`, defaulting to the local HTTP origins used by the launcher.
- `server.py` omits the CORS grant for unlisted origins and retains compatibility for non-browser clients without using `*`.
- Existing database rows were not deleted or rewritten. The database inspection found test-like records mixed with other resident data; manual review is required before any cleanup.
- The local application is SQLite-backed, not PostgreSQL-backed. HTTPS certificates, WAF/CDN/DDoS controls, firewall rules, secret management, and verified backup restoration still require deployment infrastructure and operational testing.

## Google Registration Status

The Google flow is implemented but requires deployment credentials from Google Cloud Console. This workspace intentionally has no client secret, so the server returns a safe configuration error until `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, and the registered `GOOGLE_REDIRECT_URI` are provided.

## Verification Snapshot

- Python syntax check: passed.
- Password compatibility check: passed.
- Live API smoke test: resident directory access denied with `403`; resident request accepted with server-controlled ownership/reference.
- Full lint, type-check, browser, PDF, email, and automated test suites: not available because the project currently has no configured tooling for them.
