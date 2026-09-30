"""Unified Database Adapter for Barangay Management System (BMS).

Provides transparent dual-engine database support:
- Production: PostgreSQL via DATABASE_URL (Render managed database)
- Local Development: SQLite via config.DATABASE_PATH (bms.sqlite3)

Preserves exact API compatibility with SQLite connection and cursor interfaces:
- Connection.execute(query, params)
- Connection.executescript(script)
- Context manager transaction support: with db() as connection:
- Row mapping: row['col'], row[0], dict(row), 'col' in row.keys()
- cursor.lastrowid for auto-incrementing primary keys
"""
import datetime
import hashlib
import logging
import os
import re
import secrets
import sqlite3
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

import config

logger = logging.getLogger(__name__)

# Native PostgreSQL Schema Definition matching the BMS architecture
POSTGRES_SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL CHECK(role IN ('admin', 'staff', 'resident', 'punong_barangay')),
    email TEXT,
    email_verified INTEGER NOT NULL DEFAULT 1,
    email_verified_at TEXT,
    verification_token_hash TEXT,
    verification_token_expires_at TEXT,
    verification_last_sent_at TEXT,
    resident_record_id INTEGER,
    account_status TEXT NOT NULL DEFAULT 'active' CHECK(account_status IN ('pending', 'verified', 'active', 'disabled', 'rejected', 'suspended', 'archived')),
    google_verified INTEGER NOT NULL DEFAULT 0,
    google_subject_id TEXT,
    google_email TEXT,
    google_verified_at TEXT,
    last_login_at TEXT,
    profile_image TEXT,
    position TEXT,
    department TEXT,
    contact_number TEXT,
    created_at TEXT NOT NULL,
    handler_user_id INTEGER,
    handler_assigned_at TEXT,
    handler_assigned_by INTEGER,
    archived_at TEXT,
    archived_by INTEGER,
    archive_reason TEXT,
    updated_at TEXT,
    updated_by INTEGER,
    created_by INTEGER
);

CREATE TABLE IF NOT EXISTS staff_assignment_history (
    id SERIAL PRIMARY KEY,
    staff_id INTEGER NOT NULL REFERENCES users(id),
    previous_handler_id INTEGER REFERENCES users(id),
    new_handler_id INTEGER REFERENCES users(id),
    changed_by INTEGER NOT NULL REFERENCES users(id),
    changed_at TEXT NOT NULL,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_staff_handler_history_staff ON staff_assignment_history(staff_id, changed_at);

CREATE TABLE IF NOT EXISTS residents (
    id SERIAL PRIMARY KEY,
    resident_id TEXT NOT NULL UNIQUE,
    household_no TEXT NOT NULL,
    last_name TEXT NOT NULL,
    first_name TEXT NOT NULL,
    middle_name TEXT,
    birth_date TEXT NOT NULL,
    gender TEXT NOT NULL,
    civil_status TEXT NOT NULL,
    address TEXT NOT NULL,
    contact TEXT NOT NULL,
    voter TEXT NOT NULL,
    classification TEXT NOT NULL DEFAULT 'Unclassified',
    owner_user_id INTEGER REFERENCES users(id),
    archived_at TEXT,
    profile_image TEXT,
    resident_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(resident_status IN ('PENDING', 'ACTIVE', 'SUSPENDED', 'ARCHIVED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resident_id_sequences (
    sequence_year INTEGER NOT NULL,
    prefix TEXT NOT NULL,
    next_number INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY(sequence_year, prefix)
);

CREATE TABLE IF NOT EXISTS resident_id_issuances (
    id SERIAL PRIMARY KEY,
    resident_record_id INTEGER NOT NULL REFERENCES residents(id),
    resident_id TEXT NOT NULL,
    issuance_number TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'Assigned' CHECK(status IN ('Assigned', 'Ready for Issuance', 'Issued', 'Released', 'Lost', 'Replaced', 'Cancelled')),
    issued_at TEXT,
    issued_by TEXT,
    remarks TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS requests (
    id SERIAL PRIMARY KEY,
    request_id TEXT NOT NULL UNIQUE,
    owner_user_id INTEGER REFERENCES users(id),
    resident_record_id INTEGER REFERENCES residents(id),
    type TEXT NOT NULL,
    purpose TEXT,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    date_needed TEXT,
    remarks TEXT,
    processed_at TEXT,
    approved_at TEXT,
    approved_by_user_id INTEGER REFERENCES users(id),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classifications (
    id SERIAL PRIMARY KEY,
    resident_record_id INTEGER REFERENCES residents(id),
    resident_id TEXT NOT NULL,
    full_name TEXT NOT NULL,
    classification TEXT NOT NULL,
    class_code TEXT,
    class_status TEXT NOT NULL DEFAULT 'Active',
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
    id SERIAL PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    title TEXT,
    task_type TEXT,
    priority TEXT,
    due_date TEXT,
    due_time TEXT,
    status TEXT NOT NULL DEFAULT 'assigned',
    assigned_to_user_id INTEGER REFERENCES users(id),
    created_by_user_id INTEGER,
    completion_date TEXT,
    completion_notes TEXT,
    related_record TEXT,
    progress_percent INTEGER NOT NULL DEFAULT 0,
    acknowledged_at TEXT,
    description TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS announcements (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    category TEXT,
    priority TEXT,
    archived_at TEXT,
    created_by_user_id INTEGER REFERENCES users(id),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS certificate_issuances (
    id SERIAL PRIMARY KEY,
    cert_no TEXT NOT NULL UNIQUE,
    request_record_id INTEGER REFERENCES requests(id),
    issued_by_user_id INTEGER REFERENCES users(id),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS programs (
    id SERIAL PRIMARY KEY,
    event_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    event_date TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    location TEXT,
    organizer TEXT,
    status TEXT NOT NULL DEFAULT 'Scheduled' CHECK(status IN ('Scheduled', 'Ongoing', 'Completed', 'Cancelled', 'Archived')),
    created_by_user_id INTEGER REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notifications (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    title TEXT NOT NULL,
    body TEXT,
    notification_type TEXT,
    related_id TEXT,
    related_type TEXT,
    sender_id INTEGER,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_logs (
    id SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    actor_user_id INTEGER REFERENCES users(id),
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS barangay_officials (
    id SERIAL PRIMARY KEY,
    first_name TEXT NOT NULL,
    middle_name TEXT,
    last_name TEXT NOT NULL,
    suffix TEXT,
    position TEXT NOT NULL,
    bio TEXT,
    contact_info TEXT,
    profile_image TEXT,
    display_order INTEGER NOT NULL DEFAULT 0,
    is_visible INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'ARCHIVED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_officials_order ON barangay_officials(display_order);
CREATE INDEX IF NOT EXISTS idx_officials_status ON barangay_officials(status, is_visible);

CREATE TABLE IF NOT EXISTS gallery_items (
    id SERIAL PRIMARY KEY,
    image_path TEXT NOT NULL,
    thumbnail_path TEXT,
    caption TEXT NOT NULL,
    description TEXT,
    display_order INTEGER NOT NULL DEFAULT 0,
    is_visible INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(status IN ('ACTIVE', 'ARCHIVED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_gallery_order ON gallery_items(display_order);
CREATE INDEX IF NOT EXISTS idx_gallery_status ON gallery_items(status, is_visible);

CREATE TABLE IF NOT EXISTS conversations (
    id SERIAL PRIMARY KEY,
    conversation_key TEXT UNIQUE NOT NULL,
    title TEXT,
    type TEXT NOT NULL DEFAULT 'direct' CHECK(type IN ('direct', 'group', 'announcement')),
    created_by INTEGER NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conversations_key ON conversations(conversation_key);
CREATE INDEX IF NOT EXISTS idx_conversations_updated ON conversations(updated_at);

CREATE TABLE IF NOT EXISTS messages (
    id SERIAL PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id INTEGER NOT NULL REFERENCES users(id),
    message_type TEXT NOT NULL DEFAULT 'NORMAL' CHECK(message_type IN ('NORMAL', 'IMPORTANT', 'URGENT', 'TASK', 'ANNOUNCEMENT')),
    subject TEXT,
    body TEXT NOT NULL,
    is_important INTEGER NOT NULL DEFAULT 0,
    task_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages(created_at);

CREATE TABLE IF NOT EXISTS message_recipients (
    id SERIAL PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    recipient_id INTEGER NOT NULL REFERENCES users(id),
    is_read INTEGER NOT NULL DEFAULT 0,
    read_at TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    archived_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_recipients_recip ON message_recipients(recipient_id, is_read);
CREATE INDEX IF NOT EXISTS idx_recipients_msg ON message_recipients(message_id);
CREATE INDEX IF NOT EXISTS idx_recipients_conv ON message_recipients(conversation_id);

CREATE TABLE IF NOT EXISTS task_assignments (
    id SERIAL PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    staff_id INTEGER NOT NULL REFERENCES users(id),
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'ACKNOWLEDGED', 'IN_PROGRESS', 'ON_HOLD', 'COMPLETED', 'CANCELLED', 'OVERDUE')),
    assigned_at TEXT NOT NULL,
    acknowledged_at TEXT,
    completed_at TEXT,
    completion_notes TEXT,
    UNIQUE(task_id, staff_id)
);
CREATE INDEX IF NOT EXISTS idx_task_assign_staff ON task_assignments(staff_id, status);
CREATE INDEX IF NOT EXISTS idx_task_assign_task ON task_assignments(task_id);

CREATE TABLE IF NOT EXISTS task_updates (
    id SERIAL PRIMARY KEY,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    staff_id INTEGER NOT NULL REFERENCES users(id),
    update_type TEXT NOT NULL DEFAULT 'progress' CHECK(update_type IN ('progress', 'clarification_request', 'clarification_response', 'hold', 'acknowledge', 'complete', 'note')),
    update_text TEXT NOT NULL,
    progress_percent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_updates_task ON task_updates(task_id);
'''


def is_postgres() -> bool:
    """Check whether PostgreSQL is configured as the active database engine."""
    url = (
        getattr(config, 'DATABASE_URL', '')
        or os.getenv('DATABASE_URL', '')
        or os.getenv('BMS_DATABASE_URL', '')
    )
    url = (url or '').strip()
    return bool(url and (url.startswith('postgres://') or url.startswith('postgresql://')))


def get_database_engine() -> str:
    """Return the name of the currently active database backend."""
    return 'PostgreSQL' if is_postgres() else 'SQLite'


def translate_query_to_postgres(sql: str) -> str:
    """Convert SQLite parameter placeholders '?' into PostgreSQL '%s' and adapt SQLite syntax differences."""
    clean = sql.strip()
    upper = clean.upper()

    # Translate INSERT OR REPLACE INTO task_assignments
    if 'INSERT OR REPLACE INTO TASK_ASSIGNMENTS' in upper:
        clean = re.sub(
            r'INSERT\s+OR\s+REPLACE\s+INTO\s+task_assignments\s*\(([^)]+)\)\s*VALUES\s*\(([^)]+)\)',
            r'INSERT INTO task_assignments (\1) VALUES (\2) ON CONFLICT (task_id, staff_id) DO UPDATE SET status = EXCLUDED.status, assigned_at = EXCLUDED.assigned_at',
            clean,
            flags=re.IGNORECASE
        )

    # Translate INSERT OR IGNORE INTO
    if 'INSERT OR IGNORE INTO' in clean.upper():
        clean = re.sub(r'INSERT\s+OR\s+IGNORE\s+INTO', 'INSERT INTO', clean, flags=re.IGNORECASE)
        if 'ON CONFLICT' not in clean.upper():
            clean += ' ON CONFLICT DO NOTHING'

    # Translate SQLite NULL-aware "IS NOT <col>" to PostgreSQL "IS DISTINCT FROM <col>".
    # Leaves standard SQL forms ("IS NOT NULL", "IS NOT TRUE", etc.) untouched.
    clean = re.sub(
        r'\bIS NOT\s+(?!(?:NULL|TRUE|FALSE|DISTINCT|UNKNOWN)\b)([A-Za-z_][A-Za-z0-9_]*)',
        r'IS DISTINCT FROM \1',
        clean,
        flags=re.IGNORECASE,
    )

    # Convert SQLite '?' parameter placeholders to PostgreSQL '%s'
    # Split by single-quoted string literals to preserve any literal '?' inside strings
    parts = clean.split("'")
    for i in range(0, len(parts), 2):
        parts[i] = parts[i].replace('?', '%s')
    translated = "'".join(parts)
    return translated


class RowWrapper:
    """Dictionary-like and tuple-like row object matching sqlite3.Row semantics."""

    def __init__(self, data_dict: Dict[str, Any], data_tuple: Tuple[Any, ...], col_names: List[str]):
        self._dict = data_dict
        self._tuple = data_tuple
        self._col_names = col_names

    def __getitem__(self, key: Union[int, str]) -> Any:
        if isinstance(key, int):
            return self._tuple[key]
        if key in self._dict:
            return self._dict[key]
        # Case-insensitive fallback
        k_lower = key.lower()
        for k, v in self._dict.items():
            if k.lower() == k_lower:
                return v
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        try:
            return self[key]
        except KeyError:
            return default

    def keys(self) -> List[str]:
        return list(self._col_names)

    def items(self) -> List[Tuple[str, Any]]:
        return [(col, self[col]) for col in self._col_names]

    def values(self) -> List[Any]:
        return list(self._tuple)

    def __iter__(self):
        return iter(self._col_names)

    def __len__(self) -> int:
        return len(self._col_names)

    def __contains__(self, key: str) -> bool:
        if key in self._dict:
            return True
        k_lower = str(key).lower()
        return any(k.lower() == k_lower for k in self._dict)

    def __repr__(self) -> str:
        return f"<Row {self._dict}>"


class PostgresCursorWrapper:
    """Cursor wrapper for PostgreSQL providing sqlite3.Cursor semantics."""

    def __init__(self, pg_cursor):
        self.cursor = pg_cursor
        self.lastrowid: Optional[int] = None
        self._results: Optional[List[RowWrapper]] = None
        self._index: int = 0
        self.rowcount: int = 0

    def execute(self, sql: str, params: Optional[Union[tuple, list, dict]] = None):
        clean_sql = sql.strip()
        upper = clean_sql.upper()

        if upper == 'BEGIN IMMEDIATE' or upper.startswith('BEGIN IMMEDIATE'):
            # In PostgreSQL via psycopg2, transactions are opened automatically on first statement.
            return self

        pg_sql = translate_query_to_postgres(clean_sql)
        pg_upper = pg_sql.upper()

        is_insert = pg_upper.startswith('INSERT INTO')
        has_returning = 'RETURNING' in pg_upper
        has_on_conflict = 'ON CONFLICT' in pg_upper

        table_match = re.search(r'INSERT\s+INTO\s+([a-zA-Z0-9_]+)', pg_sql, re.IGNORECASE)
        table_name = table_match.group(1).lower() if table_match else ''
        tables_without_id = {'system_settings', 'resident_id_sequences'}

        if is_insert and not has_returning and not has_on_conflict and table_name not in tables_without_id:
            # Automatically append RETURNING id to capture lastrowid.
            pg_sql += ' RETURNING id'

        if params is not None:
            if isinstance(params, list):
                params = tuple(params)
            self.cursor.execute(pg_sql, params)
        else:
            self.cursor.execute(pg_sql)

        self.rowcount = self.cursor.rowcount
        self.lastrowid = None
        self._results = None
        self._index = 0

        if self.cursor.description:
            col_names = [desc[0] for desc in self.cursor.description]
            raw_rows = self.cursor.fetchall()
            self._results = []
            for r in raw_rows:
                d = dict(zip(col_names, r))
                self._results.append(RowWrapper(d, tuple(r), col_names))

            if is_insert and not has_returning and not has_on_conflict and self._results:
                self.lastrowid = self._results[0].get('id')

        return self

    def executemany(self, sql: str, seq_of_params):
        pg_sql = translate_query_to_postgres(sql)
        self.cursor.executemany(pg_sql, seq_of_params)
        self.rowcount = self.cursor.rowcount
        return self

    def fetchone(self) -> Optional[RowWrapper]:
        if self._results is None:
            return None
        if self._index < len(self._results):
            row = self._results[self._index]
            self._index += 1
            return row
        return None

    def fetchall(self) -> List[RowWrapper]:
        if self._results is None:
            return []
        rows = self._results[self._index:]
        self._index = len(self._results)
        return rows

    def __iter__(self):
        while True:
            row = self.fetchone()
            if row is None:
                break
            yield row

    def close(self):
        self.cursor.close()


class PostgresConnectionWrapper:
    """Connection wrapper for PostgreSQL providing sqlite3.Connection semantics."""

    def __init__(self, raw_connection):
        self.raw_conn = raw_connection

    def execute(self, sql: str, params: Optional[Union[tuple, list]] = None) -> PostgresCursorWrapper:
        cur = self.raw_conn.cursor()
        wrapper = PostgresCursorWrapper(cur)
        wrapper.execute(sql, params)
        return wrapper

    def executescript(self, script: str):
        cur = self.raw_conn.cursor()
        for statement in script.split(';'):
            clean = statement.strip()
            if clean:
                cur.execute(clean)
        self.raw_conn.commit()
        cur.close()

    def commit(self):
        self.raw_conn.commit()

    def rollback(self):
        self.raw_conn.rollback()

    def close(self):
        self.raw_conn.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            self.rollback()
        else:
            self.commit()


def get_postgres_connection():
    """Create and return a PostgreSQL connection wrapped in sqlite-compatible interface."""
    if not PSYCOPG2_AVAILABLE:
        raise RuntimeError("psycopg2-binary is not installed. Please run: pip install psycopg2-binary")
    url = (
        getattr(config, 'DATABASE_URL', '')
        or os.getenv('DATABASE_URL', '')
        or os.getenv('BMS_DATABASE_URL', '')
    )
    if not url:
        raise RuntimeError("No PostgreSQL DATABASE_URL/BMS_DATABASE_URL configured.")
    url = url.strip()
    if url.startswith('postgres://'):
        # Normalize Render postgres:// to postgresql:// for compatibility
        url = 'postgresql://' + url[len('postgres://'):]
    conn = psycopg2.connect(url)
    return PostgresConnectionWrapper(conn)


def get_sqlite_connection():
    """Create and return an SQLite connection configured with Row factory."""
    db_path = getattr(config, 'DATABASE_PATH', 'bms.sqlite3')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def db():
    """Context manager providing the active database connection (PostgreSQL in production, SQLite in local dev)."""
    if is_postgres():
        return get_postgres_connection()
    return get_sqlite_connection()


def insert_or_ignore(connection, table, columns, values, conflict_cols=None):
    """Idempotent INSERT that works across both engines.

    SQLite uses ``INSERT OR IGNORE``; PostgreSQL uses ``ON CONFLICT ... DO NOTHING``.
    Placeholders are written as ``?``; the PostgreSQL cursor wrapper translates
    them to ``%s`` automatically.
    """
    cols_str = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    if is_postgres():
        if conflict_cols:
            conflict_target = "(" + ", ".join(conflict_cols) + ")"
        else:
            # Catch any unique / PK violation on the row.
            conflict_target = ""
        suffix = f"ON CONFLICT {conflict_target} DO NOTHING" if conflict_target else "ON CONFLICT DO NOTHING"
        sql = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) {suffix}"
    else:
        sql = f"INSERT OR IGNORE INTO {table} ({cols_str}) VALUES ({placeholders})"
    return connection.execute(sql, values)


def insert_or_replace(connection, table, columns, values, conflict_cols):
    """INSERT-or-replace that works across both engines.

    SQLite uses ``INSERT OR REPLACE``; PostgreSQL uses ``ON CONFLICT ... DO UPDATE``.
    """
    cols_str = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    if is_postgres():
        conflict_target = "(" + ", ".join(conflict_cols) + ")"
        updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns)
        sql = f"INSERT INTO {table} ({cols_str}) VALUES ({placeholders}) ON CONFLICT {conflict_target} DO UPDATE SET {updates}"
    else:
        sql = f"INSERT OR REPLACE INTO {table} ({cols_str}) VALUES ({placeholders})"
    return connection.execute(sql, values)



def init_db(connection=None):
    """Initialize database tables and default structures for the active database engine."""
    engine = get_database_engine()
    logger.info(f"Initializing BMS database using {engine} backend...")

    if is_postgres():
        if connection is None:
            with db() as conn:
                _init_postgres(conn)
        else:
            _init_postgres(connection)
    else:
        if connection is None:
            with db() as conn:
                _init_sqlite(conn)
        else:
            _init_sqlite(connection)


def _init_postgres(connection: PostgresConnectionWrapper):
    """Run PostgreSQL table initialization and ensure schema integrity."""
    connection.executescript(POSTGRES_SCHEMA)
    logger.info("PostgreSQL schema successfully verified.")

    # Idempotently seed initial admin user if none exists
    admin_row = connection.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
    if not admin_row:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        admin_pass = getattr(config, 'ADMIN_PASSWORD', 'admin123')
        salt = secrets.token_hex(16)
        digest = hashlib.pbkdf2_hmac('sha256', admin_pass.encode('utf-8'), salt.encode('ascii'), 240_000)
        admin_hash = f'pbkdf2_sha256$240000${salt}${digest.hex()}'
        connection.execute(
            """INSERT INTO users (username, password_hash, name, role, account_status, position, department, contact_number, created_at)
               VALUES (%s, %s, %s, 'admin', 'active', 'System Administrator', 'Administration', '0917-000-0000', %s)
               ON CONFLICT (username) DO NOTHING""",
            ('admin', admin_hash, 'Punong Barangay', now_iso)
        )
        logger.info("Initialized default administrator account.")

    # Idempotently seed default system settings
    default_settings = [
        ('barangay_name', 'Barangay Poblacion'),
        ('municipality', 'City of Manila'),
        ('resident_id_prefix', 'BRGY'),
        ('resident_id_sequence_length', '6'),
        ('welcome_badge', 'OFFICIAL COMMUNITY PORTAL'),
        ('welcome_title', 'SERVING OUR COMMUNITY WITH INTEGRITY & EXCELLENCE'),
        ('welcome_subtitle', 'Welcome to the official online portal of Barangay Poblacion. Access barangay certificates, view community announcements, track service requests, and stay connected with your local government.'),
        ('cp_enabled', 'true'),
        ('cp_watermark_enabled', 'true'),
        ('cp_watermark_text', 'OFFICIAL BARANGAY WEBSITE'),
        ('cp_right_click_protection', 'true'),
        ('cp_drag_prevention', 'true'),
        ('btn_portal_label', 'ENTER BMS PORTAL'),
        ('btn_services_label', 'E-GOVERNMENT SERVICES'),
        ('btn_announcements_label', 'VIEW ANNOUNCEMENTS'),
        ('btn_programs_label', 'COMMUNITY PROGRAMS'),
        ('contact_address', 'Barangay Hall, Main Street, Poblacion, City of Manila'),
        ('contact_phone', '(02) 8123-4567 / +63 917 123 4567'),
        ('contact_email', 'info@barangaypoblacion.gov.ph'),
        ('contact_office_hours', 'Monday - Friday: 8:00 AM - 5:00 PM'),
        ('contact_emergency', '117 / 911 / (02) 8999-9999'),
        ('contact_website', 'https://barangaypoblacion.gov.ph'),
        ('social_facebook', 'https://facebook.com/barangaypoblacion'),
    ]
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
    for key, val in default_settings:
        connection.execute(
            """INSERT INTO system_settings (setting_key, setting_value, updated_at)
               VALUES (%s, %s, %s)
               ON CONFLICT (setting_key) DO NOTHING""",
            (key, val, now_iso)
        )
    connection.commit()


def _init_sqlite(connection):
    """Run SQLite table initialization."""
    import server
    connection.executescript(server.SCHEMA)
    server.migrate_users_table(connection)
    for col, defn in [
        ('position', 'TEXT'),
        ('department', 'TEXT'),
        ('contact_number', 'TEXT'),
        ('email', 'TEXT'),
        ('email_verified', 'INTEGER NOT NULL DEFAULT 1'),
        ('email_verified_at', 'TEXT'),
        ('verification_token_hash', 'TEXT'),
        ('verification_token_expires_at', 'TEXT'),
        ('verification_last_sent_at', 'TEXT'),
        ('resident_record_id', 'INTEGER'),
        ('account_status', "TEXT NOT NULL DEFAULT 'active'"),
        ('google_verified', 'INTEGER NOT NULL DEFAULT 0'),
        ('google_subject_id', 'TEXT'),
        ('google_email', 'TEXT'),
        ('google_verified_at', 'TEXT'),
        ('last_login_at', 'TEXT'),
        ('profile_image', 'TEXT'),
        ('handler_user_id', 'INTEGER'),
        ('handler_assigned_at', 'TEXT'),
        ('handler_assigned_by', 'INTEGER'),
        ('archived_at', 'TEXT'),
        ('archived_by', 'INTEGER'),
        ('archive_reason', 'TEXT'),
        ('updated_at', 'TEXT'),
        ('updated_by', 'INTEGER'),
        ('created_by', 'INTEGER')
    ]:
        server.add_column(connection, 'users', col, defn)
    logger.info("SQLite schema successfully verified.")
