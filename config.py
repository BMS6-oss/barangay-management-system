"""Barangay Management System (BMS) Configuration Module.

Provides safe, centralized configuration loading with pure Python standard library.
Reads settings from environment variables and an optional .env file.
"""
import os
from pathlib import Path

# Project root directory (absolute path)
ROOT_DIR = Path(__file__).resolve().parent

# Load simple key=value pairs from .env if present
def _load_dotenv():
    env_file = ROOT_DIR / '.env'
    if not env_file.is_file():
        return
    try:
        with open(env_file, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, val = line.split('=', 1)
                key = key.strip()
                val = val.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = val
    except Exception:
        pass

_load_dotenv()

# Network configuration
HOST = os.getenv('BMS_HOST', os.getenv('HOST', '0.0.0.0'))
PORT = int(os.getenv('PORT', os.getenv('BMS_PORT', '8000')))
API_BASE_URL = os.getenv('API_BASE_URL', os.getenv('BMS_API_BASE_URL', '')).rstrip('/')
_allowed_origins = os.getenv(
    'BMS_ALLOWED_ORIGINS',
    f'http://127.0.0.1:{PORT},http://localhost:{PORT}',
)
_origins_list = [origin.strip().rstrip('/') for origin in _allowed_origins.split(',') if origin.strip()]
if os.getenv('APP_URL'):
    _origins_list.append(os.getenv('APP_URL').strip().rstrip('/'))
ALLOWED_ORIGINS = frozenset(_origins_list)

# Database configuration
# The existing BMS uses SQLite. On Render, mount a Persistent Disk at /data
# and set BMS_DATABASE_PATH=/data/bms.sqlite3.
# DATABASE_URL is reserved for external database connection strings.
DATABASE_URL = os.getenv('DATABASE_URL', '')
_db_env = os.getenv('BMS_DATABASE_PATH', os.getenv('DATABASE_PATH', ''))
if _db_env:
    DATABASE_PATH = Path(_db_env).resolve()
else:
    DATABASE_PATH = (ROOT_DIR / 'bms.sqlite3').resolve()

try:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
except Exception:
    pass

# Browser auto-opening configuration (disabled by default in cloud/Docker environments)
_is_container_or_cloud = bool(os.getenv('RENDER') or os.getenv('DOCKER') or os.getenv('PORT'))
_default_auto_open = 'false' if _is_container_or_cloud else 'true'
AUTO_OPEN_BROWSER = os.getenv('BMS_AUTO_OPEN_BROWSER', os.getenv('AUTO_OPEN_BROWSER', _default_auto_open)).lower() in ('true', '1', 'yes')

# Logging configuration
LOG_FILE = (ROOT_DIR / 'bms_server.log').resolve()
LOG_LEVEL = os.getenv('BMS_LOG_LEVEL', 'INFO').upper()

# Security & Session tokens
TOKEN_TTL_SECONDS = int(os.getenv('TOKEN_TTL_SECONDS', str(8 * 60 * 60)))
GOOGLE_STATE_TTL_SECONDS = int(os.getenv('GOOGLE_STATE_TTL_SECONDS', str(10 * 60)))
MAX_BODY_BYTES = int(os.getenv('MAX_BODY_BYTES', '10485760'))
RATE_LIMIT_ENABLED = os.getenv('BMS_RATE_LIMIT_ENABLED', 'true').lower() in ('true', '1', 'yes')
LOGIN_RATE_LIMIT = int(os.getenv('BMS_LOGIN_RATE_LIMIT', '10'))
REGISTER_RATE_LIMIT = int(os.getenv('BMS_REGISTER_RATE_LIMIT', '5'))
WRITE_RATE_LIMIT = int(os.getenv('BMS_WRITE_RATE_LIMIT', '120'))
RATE_LIMIT_WINDOW_SECONDS = int(os.getenv('BMS_RATE_LIMIT_WINDOW_SECONDS', '60'))
REQUIRE_HTTPS = os.getenv('BMS_REQUIRE_HTTPS', 'false').lower() in ('true', '1', 'yes')

# Application base URL
APP_URL = os.getenv('APP_URL', f'http://{HOST}:{PORT}').rstrip('/')

# Google OAuth Credentials
GOOGLE_CLIENT_ID = os.getenv('GOOGLE_CLIENT_ID', '')
GOOGLE_CLIENT_SECRET = os.getenv('GOOGLE_CLIENT_SECRET', '')
GOOGLE_REDIRECT_URI = os.getenv('GOOGLE_REDIRECT_URI', f'http://{HOST}:{PORT}/api/google/callback')
