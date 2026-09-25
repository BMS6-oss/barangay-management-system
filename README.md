# Barangay Management System (BMS)

A robust, lightweight, and modern Barangay Management System providing resident directory services, certificate issuance, service requests, task management, public announcements, and administrative oversight.

---

## 🏛️ System Architecture

- **Backend:** Python standard-library `http.server.ThreadingHTTPServer` (`server.py`) serving both REST JSON APIs and the frontend static assets.
- **Frontend:** Single-page interface in `index.html` with vanilla JavaScript (`sqlite-api.js`), CSS styling, and responsive layout.
- **Database:** SQLite with automatic startup migration (`init_db()`), schema verification, and indexes. Configured via `BMS_DATABASE_PATH`.
- **Runtime:** Python 3.10+ (pure standard library + optional `Pillow` for server-side watermark processing).
- **Deployment:** Docker-containerized, Linux-compatible, and optimized for Render (with Persistent Disk).

```text
                    INTERNET
                        │
                        ▼
                RENDER WEB SERVICE
                        │
                     Docker (bmsuser)
                        │
                        ▼
                  BMS SERVER (server.py)
                        │
            ┌───────────┴───────────┐
            │                       │
            ▼                       ▼
       BMS Frontend              BMS API
       (index.html)              (/api/*)
                                    │
                                    ▼
                         Persistent Storage
                         (/data/bms.sqlite3)
```

---

## ⚙️ Requirements

- **Local Python Development:** Python 3.10 or newer (tested on 3.11 and 3.12).
- **Container Deployment:** Docker 20+ (Linux container).
- **Dependencies:** Listed in `requirements.txt` (`Pillow>=10.0.0` for image watermarking; all other core server capabilities use the Python standard library).

---

## 🚀 Local Development

### 1. Direct Python Execution

```bash
# Clone the repository
git clone https://github.com/your-username/barangay-management-system.git
cd barangay-management-system

# Install optional dependencies
pip install -r requirements.txt

# Start the server (binds to 0.0.0.0:8000 by default)
python server.py
```

Open `http://localhost:8000` in your web browser.

### 2. Default Seed Accounts

When initializing a fresh database, baseline administrative and staff accounts are automatically created:

| Role | Username | Default Password | Notes |
| :--- | :--- | :--- | :--- |
| **Admin** | `admin` | `admin123` | Full system oversight & configuration |
| **Punong Barangay** | `punong_barangay` | `captain123` | Executive command center & task oversight |
| **Staff** | `staff` | `staff123` | Record management & request processing |
| **Resident** | `resident` | `resident123` | Community portal member |

> [!IMPORTANT]
> Change all default administrative passwords immediately upon deploying to any staging or production environment.

---

## 🐳 Docker Deployment

### 1. Build the Docker Image

```bash
docker build -t bms:latest .
```

### 2. Run the Container Locally

```bash
# Run with local volume persistence for the SQLite database
docker run -d \
  --name bms-app \
  -p 8000:8000 \
  -v bms_data:/data \
  -e PORT=8000 \
  -e BMS_HOST=0.0.0.0 \
  bms:latest
```

Open `http://localhost:8000` to access the application.

---

## ☁️ Render Deployment

The repository includes a ready-to-use Render Blueprint (`render.yaml`).

### Option A: Deploy via Render Blueprint (Recommended)

1. Push your clean repository to GitHub.
2. In the [Render Dashboard](https://dashboard.render.com/), click **New** → **Blueprint**.
3. Connect your GitHub repository.
4. Render will parse `render.yaml` and configure:
   - **Environment:** Docker
   - **Health Check Path:** `/health`
   - **Persistent Disk:** 1 GB mounted at `/data`
   - **Port:** `10000` (Render standard)
5. Review the plan and click **Apply**.

### Option B: Deploy as a Manual Web Service

1. Create a **New Web Service** connected to your repository.
2. Choose **Docker** as the runtime.
3. Configure the following **Environment Variables**:
   - `PORT`: `10000`
   - `BMS_HOST`: `0.0.0.0`
   - `BMS_AUTO_OPEN_BROWSER`: `false`
   - `BMS_REQUIRE_HTTPS`: `true`
   - `BMS_DATABASE_PATH`: `/data/bms.sqlite3`
   - `APP_URL`: `https://your-service-name.onrender.com`
4. Under **Disks**, add a Persistent Disk:
   - **Name:** `bms-data`
   - **Mount Path:** `/data`
   - **Size:** `1 GB` (or larger depending on upload requirements)
5. Set **Health Check Path** to `/health`.
6. Deploy the service.

---

## 🗄️ Database Architecture & Persistence

### SQLite with Persistent Disk
The BMS uses an optimized SQLite database with Write-Ahead Logging (WAL) and foreign keys enabled.
- In Docker/Render, data is persisted by attaching a persistent disk mounted to `/data` and setting:
  ```env
  BMS_DATABASE_PATH=/data/bms.sqlite3
  ```
- All resident records, certificates, requests, announcements, audit logs, and user accounts are preserved across restarts and container redeployments.

### Schema Migrations
The server automatically runs safe, non-destructive schema migrations on startup via `init_db()` in `server.py`:
- Creates missing tables and indexes.
- Adds newly required columns dynamically (`add_column()`).
- Migrates existing accounts to verified status without deleting user records.
- Preserves all existing data.

### Future PostgreSQL Migration
The application architecture separates data persistence behind the `db()` context manager. `DATABASE_URL` is reserved in configuration for external PostgreSQL connection strings if future horizontal multi-instance scaling is required.

---

## 🔒 Environment Variables

See [.env.example](.env.example) for all supported settings:

| Variable | Default | Scope | Description |
| :--- | :--- | :--- | :--- |
| `PORT` | `8000` | Public | Network port to listen on (auto-set by Render) |
| `BMS_HOST` | `0.0.0.0` | Public | Network host interface binding |
| `BMS_DATABASE_PATH` | `bms.sqlite3` | Server | Path to database file (`/data/bms.sqlite3` on Render) |
| `APP_URL` | `http://0.0.0.0:8000` | Server | Canonical public URL of the deployed application |
| `BMS_ALLOWED_ORIGINS` | Local origins | Server | Allowed CORS origins for external API access |
| `BMS_REQUIRE_HTTPS` | `false` | Server | Enables HSTS header when accessed via HTTPS |
| `BMS_AUTO_OPEN_BROWSER` | `false` (cloud) | Server | Automatically launches browser on startup |
| `GOOGLE_CLIENT_ID` | `""` | Secret | Optional Google Cloud OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | `""` | Secret | Optional Google Cloud OAuth client secret |
| `GOOGLE_REDIRECT_URI` | `...` | Secret | Registered OAuth callback URI |

---

## 🩺 Health Check Endpoint

The server exposes a lightweight health check endpoint:

```http
GET /health
GET /api/health
```

**Sample Response (HTTP 200 OK):**
```json
{
  "success": true,
  "ok": true,
  "status": "ok",
  "database": "connected",
  "db_path": "bms.sqlite3",
  "timestamp": "2026-09-25T15:00:00.000000+00:00",
  "version": "2.0.0"
}
```

This endpoint verifies SQLite connectivity, returns no sensitive secrets or filesystem paths, and satisfies Render and Docker health check monitors.

---

## 🛡️ Security Features

- **PBKDF2 Password Hashing:** PBKDF2-HMAC-SHA256 password hashing with automatic salt generation and legacy hash upgrade.
- **Expiring Bearer Tokens:** In-memory cryptographically secure session tokens (`secrets.token_urlsafe(32)`) with 8-hour TTL and server-side revocation on logout.
- **Non-Root Container:** Runs as dedicated system user `bmsuser` (UID 1000).
- **Rate Limiting:** Process-local rate limiting on authentication and write endpoints with `X-Forwarded-For` proxy support.
- **Security Headers:** Enforces `Content-Security-Policy`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy`, `Permissions-Policy`, and configurable `Strict-Transport-Security`.
- **Zero Committed Secrets:** The repository contains no passwords, private keys, or API tokens; `.gitignore` and `.dockerignore` prevent tracking of runtime databases or `.env` files.
