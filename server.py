"""Barangay Management System server.

SQLite-backed JSON API + static frontend host.
All displayed application data originates from this database; there are no
hard-coded records, sample rows, or client-generated statistics.
"""
import base64
import hashlib
import io
import json
import logging
import mimetypes
import os
import posixpath
import secrets
import signal
import sqlite3
import sys
import threading
import time
from html import escape
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import config

# Optional: Pillow for server-side image watermarking.
# Falls back gracefully if not installed (CSS overlay used instead).
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False
    logging.warning('Pillow not installed. Server-side watermarking disabled. '
                    'Run: pip install pillow  to enable.')

ROOT = config.ROOT_DIR
DB_PATH = config.DATABASE_PATH
TOKENS = {}
GOOGLE_STATES = {}
RATE_LIMIT_STATE = {}
RATE_LIMIT_LOCK = threading.Lock()
TOKEN_TTL_SECONDS = config.TOKEN_TTL_SECONDS
GOOGLE_STATE_TTL_SECONDS = config.GOOGLE_STATE_TTL_SECONDS
MAX_BODY_BYTES = config.MAX_BODY_BYTES
RATE_LIMIT_WINDOW_SECONDS = config.RATE_LIMIT_WINDOW_SECONDS
VALID_CLASSIFICATIONS = ('Unclassified', 'Regular Resident', 'Senior Citizen', 'PWD', 'Solo Parent', '4Ps Beneficiary', 'Student', 'Out-of-School Youth', 'Child', 'Youth', 'Working Adult', 'Unemployed', 'Business Owner', 'Indigenous/Tribal Member', 'Other')
REQUEST_STATUSES = ('pending', 'under review', 'processing', 'approved', 'rejected', 'ready for release', 'completed', 'cancelled')
PROGRAM_STATUSES = ('Scheduled', 'Ongoing', 'Completed', 'Cancelled', 'Archived')
PENDING_STATUSES = ('pending', 'under review', 'processing')
APPROVED_STATUSES = ('approved', 'ready for release', 'completed')
ROLE_LABELS = {
    'admin': 'Administrator',
    'staff': 'Staff',
    'resident': 'Resident',
    'punong_barangay': 'Punong Barangay',
}

SCHEMA = '''
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        staff_id INTEGER NOT NULL,
        previous_handler_id INTEGER,
        new_handler_id INTEGER,
        changed_by INTEGER NOT NULL,
        changed_at TEXT NOT NULL,
        reason TEXT,
        FOREIGN KEY(staff_id) REFERENCES users(id),
        FOREIGN KEY(previous_handler_id) REFERENCES users(id),
        FOREIGN KEY(new_handler_id) REFERENCES users(id),
        FOREIGN KEY(changed_by) REFERENCES users(id)
    );
    CREATE INDEX IF NOT EXISTS idx_staff_handler_history_staff ON staff_assignment_history(staff_id, changed_at);
CREATE TABLE IF NOT EXISTS residents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    owner_user_id INTEGER,
    archived_at TEXT,
    resident_status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK(resident_status IN ('PENDING', 'ACTIVE', 'SUSPENDED', 'ARCHIVED')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(owner_user_id) REFERENCES users(id)
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_record_id INTEGER NOT NULL,
    resident_id TEXT NOT NULL,
    issuance_number TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'Assigned' CHECK(status IN ('Assigned', 'Ready for Issuance', 'Issued', 'Released', 'Lost', 'Replaced', 'Cancelled')),
    issued_at TEXT,
    issued_by TEXT,
    remarks TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(resident_record_id) REFERENCES residents(id)
);
CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    owner_user_id INTEGER,
    resident_record_id INTEGER,
    type TEXT NOT NULL,
    purpose TEXT,
    category TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    date_needed TEXT,
    remarks TEXT,
    processed_at TEXT,
    approved_at TEXT,
    approved_by_user_id INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(owner_user_id) REFERENCES users(id),
    FOREIGN KEY(resident_record_id) REFERENCES residents(id),
    FOREIGN KEY(approved_by_user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS classifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resident_record_id INTEGER,
    resident_id TEXT NOT NULL,
    full_name TEXT NOT NULL,
    classification TEXT NOT NULL,
    class_code TEXT,
    class_status TEXT NOT NULL DEFAULT 'Active',
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(resident_record_id) REFERENCES residents(id)
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL UNIQUE,
    title TEXT,
    task_type TEXT,
    priority TEXT,
    due_date TEXT,
    status TEXT NOT NULL DEFAULT 'assigned',
    assigned_to_user_id INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(assigned_to_user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS announcements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    category TEXT,
    priority TEXT,
    archived_at TEXT,
    created_by_user_id INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS certificate_issuances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_no TEXT NOT NULL UNIQUE,
    request_record_id INTEGER,
    issued_by_user_id INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(request_record_id) REFERENCES requests(id),
    FOREIGN KEY(issued_by_user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS programs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    event_date TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    location TEXT,
    organizer TEXT,
    status TEXT NOT NULL DEFAULT 'Scheduled' CHECK(status IN ('Scheduled', 'Ongoing', 'Completed', 'Cancelled', 'Archived')),
    created_by_user_id INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(created_by_user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    body TEXT,
    notification_type TEXT,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS activity_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    actor_user_id INTEGER,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(actor_user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS barangay_officials (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    staff_id INTEGER NOT NULL REFERENCES users(id),
    update_type TEXT NOT NULL DEFAULT 'progress' CHECK(update_type IN ('progress', 'clarification_request', 'clarification_response', 'hold', 'acknowledge', 'complete', 'note')),
    update_text TEXT NOT NULL,
    progress_percent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_task_updates_task ON task_updates(task_id);
'''


def now():
    return datetime.now(timezone.utc).isoformat()


def today():
    return datetime.now(timezone.utc).date().isoformat()


def next_resident_id(connection):
    year = datetime.now(timezone.utc).year
    settings = {row['setting_key']: row['setting_value'] for row in connection.execute('SELECT setting_key, setting_value FROM system_settings')}
    prefix = settings.get('resident_id_prefix', 'BRGY').strip().upper() or 'BRGY'
    length = max(4, min(12, int(settings.get('resident_id_sequence_length', '6'))))
    connection.execute('BEGIN IMMEDIATE')
    row = connection.execute('SELECT next_number FROM resident_id_sequences WHERE sequence_year = ? AND prefix = ?', (year, prefix)).fetchone()
    if not row:
        existing = connection.execute('SELECT MAX(CAST(substr(resident_id, ?) AS INTEGER)) AS sequence_no FROM residents WHERE resident_id LIKE ?', (len(f'{prefix}-{year}-') + 1, f'{prefix}-{year}-%')).fetchone()
        sequence_no = (existing['sequence_no'] or 0) + 1
        connection.execute('INSERT INTO resident_id_sequences (sequence_year, prefix, next_number) VALUES (?, ?, ?)', (year, prefix, sequence_no + 1))
    else:
        sequence_no = row['next_number']
        connection.execute('UPDATE resident_id_sequences SET next_number = ? WHERE sequence_year = ? AND prefix = ?', (sequence_no + 1, year, prefix))
    return f'{prefix}-{year}-{sequence_no:0{length}d}'


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), 240_000)
    return f'pbkdf2_sha256$240000${salt}${digest.hex()}'


def password_matches(password, stored):
    if not stored or not password:
        return False
    if stored.startswith('pbkdf2_sha256$'):
        try:
            _, rounds, salt, expected = stored.split('$', 3)
            actual = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), int(rounds)).hex()
            return secrets.compare_digest(actual, expected)
        except Exception:
            return False
    # Existing databases used SHA-256; permit the actual password and upgrade it on login.
    return secrets.compare_digest(hashlib.sha256(password.encode('utf-8')).hexdigest(), stored)


def email_is_valid(value):
    value = str(value or '').strip().lower()
    if len(value) > 254 or value.count('@') != 1:
        return False
    local, domain = value.rsplit('@', 1)
    return bool(local and domain and '.' in domain and not value.isspace())


# Email verification helpers removed.
# Email verification is no longer required for registration or login.


def db():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute('PRAGMA foreign_keys = ON')
    return connection


def add_column(connection, table, column, definition):
    columns = {row['name'] for row in connection.execute(f'PRAGMA table_info({table})')}
    if column not in columns:
        connection.execute(f'ALTER TABLE {table} ADD COLUMN {column} {definition}')



def migrate_users_table(connection):
    row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='users'").fetchone()
    if row and 'punong_barangay' not in (row[0] or ''):
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("""
            CREATE TABLE users_migrated (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                name TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('admin', 'staff', 'resident', 'punong_barangay')),
                email TEXT,
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
                created_at TEXT NOT NULL
            );
        """)
        cols = [r['name'] for r in connection.execute("PRAGMA table_info(users)").fetchall()]
        common_cols = [c for c in cols if c in ('id', 'username', 'password_hash', 'name', 'role', 'email', 'resident_record_id', 'account_status', 'google_verified', 'google_subject_id', 'google_email', 'google_verified_at', 'last_login_at', 'profile_image', 'position', 'department', 'contact_number', 'created_at')]
        cols_str = ', '.join(common_cols)
        connection.execute(f"INSERT INTO users_migrated ({cols_str}) SELECT {cols_str} FROM users")
        connection.execute("DROP TABLE users")
        connection.execute("ALTER TABLE users_migrated RENAME TO users")
        connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_resident_record ON users(resident_record_id) WHERE resident_record_id IS NOT NULL")
        connection.execute("PRAGMA foreign_keys = ON")


def check_and_update_overdue_tasks(connection):
    current_iso = now()
    now_dt = datetime.now(timezone.utc)
    tasks = connection.execute("SELECT id, task_id, title, created_by_user_id, due_date, due_time, status FROM tasks WHERE status NOT IN ('COMPLETED', 'done', 'CANCELLED', 'cancelled', 'OVERDUE') AND due_date IS NOT NULL AND due_date != ''").fetchall()
    for t in tasks:
        due_str = str(t['due_date']).strip()
        time_part = str(t['due_time']).strip() if t['due_time'] and str(t['due_time']).strip() else '23:59:59'
        full_str = f"{due_str} {time_part}" if ' ' not in due_str else due_str
        try:
            if len(due_str) >= 10:
                due_dt = None
                try:
                    due_dt = datetime.fromisoformat(full_str).replace(tzinfo=timezone.utc)
                except Exception:
                    try:
                        due_dt = datetime.strptime(full_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                    except Exception:
                        due_dt = datetime.strptime(full_str, '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
                if due_dt and now_dt > due_dt:
                    connection.execute("UPDATE tasks SET status = 'OVERDUE', updated_at = ? WHERE id = ?", (current_iso, t['id']))
                    connection.execute("UPDATE task_assignments SET status = 'OVERDUE' WHERE task_id = ? AND status NOT IN ('COMPLETED', 'CANCELLED')", (t['id'],))
                    
                    # Notify assigned staff
                    assignees = connection.execute("SELECT staff_id FROM task_assignments WHERE task_id = ?", (t['id'],)).fetchall()
                    for a in assignees:
                        already = connection.execute("SELECT 1 FROM notifications WHERE user_id = ? AND related_id = ? AND notification_type = 'task_overdue'", (a['staff_id'], str(t['id']))).fetchone()
                        if not already:
                            connection.execute(
                                "INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id) VALUES (?, ?, ?, 'task_overdue', 0, ?, ?, 'task', ?)",
                                (a['staff_id'], f"⚠️ TASK OVERDUE: {t['title'] or 'Command'}", f"Deadline was {due_str}. Please submit an immediate update or request clarification.", current_iso, str(t['id']), t['created_by_user_id'])
                            )
                    if t['created_by_user_id']:
                        already_pb = connection.execute("SELECT 1 FROM notifications WHERE user_id = ? AND related_id = ? AND notification_type = 'task_overdue'", (t['created_by_user_id'], str(t['id']))).fetchone()
                        if not already_pb:
                            connection.execute(
                                "INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id) VALUES (?, ?, ?, 'task_overdue', 0, ?, ?, 'task', ?)",
                                (t['created_by_user_id'], f"⚠️ TASK OVERDUE: {t['title'] or 'Command'}", f"Task deadline ({due_str}) has passed without completion.", current_iso, str(t['id']), t['created_by_user_id'])
                            )
        except Exception:
            pass

def init_db():
    with db() as connection:
        connection.executescript(SCHEMA)
        # Column migrations for databases created by older versions.
        migrate_users_table(connection)
        add_column(connection, 'users', 'position', 'TEXT')
        add_column(connection, 'users', 'department', 'TEXT')
        add_column(connection, 'users', 'contact_number', 'TEXT')
        add_column(connection, 'tasks', 'description', 'TEXT')
        add_column(connection, 'tasks', 'created_by_user_id', 'INTEGER')
        add_column(connection, 'tasks', 'due_time', 'TEXT')
        add_column(connection, 'tasks', 'completion_date', 'TEXT')
        add_column(connection, 'tasks', 'completion_notes', 'TEXT')
        add_column(connection, 'tasks', 'related_record', 'TEXT')
        add_column(connection, 'tasks', 'progress_percent', 'INTEGER NOT NULL DEFAULT 0')
        add_column(connection, 'tasks', 'acknowledged_at', 'TEXT')
        add_column(connection, 'notifications', 'related_id', 'TEXT')
        add_column(connection, 'notifications', 'related_type', 'TEXT')
        add_column(connection, 'notifications', 'sender_id', 'INTEGER')
        add_column(connection, 'users', 'email', 'TEXT')
        add_column(connection, 'users', 'email_verified', 'INTEGER NOT NULL DEFAULT 1')
        add_column(connection, 'users', 'email_verified_at', 'TEXT')
        add_column(connection, 'users', 'verification_token_hash', 'TEXT')
        add_column(connection, 'users', 'verification_token_expires_at', 'TEXT')
        add_column(connection, 'users', 'verification_last_sent_at', 'TEXT')
        add_column(connection, 'users', 'resident_record_id', 'INTEGER')
        add_column(connection, 'users', "account_status", "TEXT NOT NULL DEFAULT 'active'")
        add_column(connection, 'users', 'google_verified', 'INTEGER NOT NULL DEFAULT 0')
        add_column(connection, 'users', 'google_subject_id', 'TEXT')
        add_column(connection, 'users', 'google_email', 'TEXT')
        add_column(connection, 'users', 'google_verified_at', 'TEXT')
        add_column(connection, 'users', 'last_login_at', 'TEXT')
        add_column(connection, 'residents', 'archived_at', 'TEXT')
        add_column(connection, 'residents', 'resident_status', "TEXT NOT NULL DEFAULT 'ACTIVE'")
        add_column(connection, 'residents', 'classification', "TEXT NOT NULL DEFAULT 'Unclassified'")
        add_column(connection, 'requests', 'resident_record_id', 'INTEGER')
        add_column(connection, 'requests', 'date_needed', 'TEXT')
        add_column(connection, 'requests', 'remarks', 'TEXT')
        add_column(connection, 'requests', 'processed_at', 'TEXT')
        add_column(connection, 'requests', 'approved_at', 'TEXT')
        add_column(connection, 'requests', 'approved_by_user_id', 'INTEGER')
        add_column(connection, 'tasks', 'title', 'TEXT')
        add_column(connection, 'tasks', 'task_type', 'TEXT')
        add_column(connection, 'tasks', 'priority', 'TEXT')
        add_column(connection, 'tasks', 'due_date', 'TEXT')
        add_column(connection, 'tasks', 'assigned_to_user_id', 'INTEGER')
        add_column(connection, 'announcements', 'category', 'TEXT')
        add_column(connection, 'announcements', 'archived_at', 'TEXT')
        add_column(connection, 'announcements', 'created_by_user_id', 'INTEGER')
        add_column(connection, 'certificate_issuances', 'request_record_id', 'INTEGER')
        add_column(connection, 'certificate_issuances', 'issued_by_user_id', 'INTEGER')
        add_column(connection, 'classifications', 'resident_record_id', 'INTEGER')
        add_column(connection, 'residents', 'profile_image', 'TEXT')
        add_column(connection, 'users', 'profile_image', 'TEXT')
        add_column(connection, 'users', 'handler_user_id', 'INTEGER')
        add_column(connection, 'users', 'handler_assigned_at', 'TEXT')
        add_column(connection, 'users', 'handler_assigned_by', 'INTEGER')
        add_column(connection, 'users', 'archived_at', 'TEXT')
        add_column(connection, 'users', 'archived_by', 'INTEGER')
        add_column(connection, 'users', 'archive_reason', 'TEXT')
        add_column(connection, 'users', 'updated_at', 'TEXT')
        add_column(connection, 'users', 'updated_by', 'INTEGER')
        add_column(connection, 'users', 'created_by', 'INTEGER')
        connection.execute('''
            CREATE TABLE IF NOT EXISTS staff_assignment_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                staff_id INTEGER NOT NULL,
                previous_handler_id INTEGER,
                new_handler_id INTEGER,
                changed_by INTEGER NOT NULL,
                changed_at TEXT NOT NULL,
                reason TEXT,
                FOREIGN KEY(staff_id) REFERENCES users(id),
                FOREIGN KEY(previous_handler_id) REFERENCES users(id),
                FOREIGN KEY(new_handler_id) REFERENCES users(id),
                FOREIGN KEY(changed_by) REFERENCES users(id)
            )
        ''')
        connection.execute('CREATE INDEX IF NOT EXISTS idx_staff_handler_history_staff ON staff_assignment_history(staff_id, changed_at)')
        connection.execute("UPDATE residents SET resident_status = 'ARCHIVED' WHERE archived_at IS NOT NULL")
        connection.execute("UPDATE users SET email_verified = 1 WHERE email_verified = 0 OR email_verified IS NULL")
        created = now()
        connection.execute('INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)', ('resident_id_prefix', 'BRGY', created))
        connection.execute('INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)', ('resident_id_sequence_length', '6', created))
        connection.execute('INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)', ('barangay_name', 'Barangay Poblacion', created))
        connection.execute('INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)', ('municipality', 'City of Manila', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('punong_barangay', '', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('barangay_secretary', '', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('hero_image_path', './assets/hero1.jpg', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('hero_image_focal_position', 'center', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('hero_image_updated_at', created, created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('hero_image_protected_path', '', created))
        # Dynamic CMS Settings
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('welcome_badge', 'OFFICIAL COMMUNITY PORTAL', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('welcome_title', 'SERVING OUR COMMUNITY WITH INTEGRITY & EXCELLENCE', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('welcome_subtitle', 'Welcome to the official online portal of Barangay Poblacion. Access barangay certificates, view community announcements, track service requests, and stay connected with your local government.', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('hero_overlay_opacity', '0.45', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('hero_visible', 'true', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('contact_address', 'Barangay Hall, Main Street, Poblacion, City of Manila', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('contact_phone', '(02) 8123-4567 / +63 917 123 4567', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('contact_email', 'info@barangaypoblacion.gov.ph', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('contact_office_hours', 'Monday - Friday: 8:00 AM - 5:00 PM', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('contact_emergency', '117 / 911 / (02) 8999-9999', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('contact_website', 'https://barangaypoblacion.gov.ph', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('social_facebook', 'https://facebook.com/barangaypoblacion', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('social_twitter', '', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('social_youtube', '', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('btn_portal_label', 'ENTER BMS PORTAL', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('btn_services_label', 'E-GOVERNMENT SERVICES', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('btn_announcements_label', 'VIEW ANNOUNCEMENTS', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('btn_programs_label', 'COMMUNITY PROGRAMS', created))
        # Content Protection Settings
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('cp_enabled', 'true', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('cp_watermark_enabled', 'true', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('cp_watermark_text', 'OFFICIAL BARANGAY WEBSITE', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('cp_watermark_opacity', '0.30', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('cp_watermark_position', 'bottom-right', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('cp_right_click_protection', 'true', created))
        connection.execute("INSERT OR IGNORE INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?)", ('cp_drag_prevention', 'true', created))

        # Seed initial official if none exist
        if not connection.execute('SELECT 1 FROM barangay_officials LIMIT 1').fetchone():
            connection.execute('''
                INSERT INTO barangay_officials (first_name, middle_name, last_name, suffix, position, bio, contact_info, profile_image, display_order, is_visible, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', ('Barangay', '', 'Official', '', 'Punong Barangay', 'Official profile pending administrator configuration.', '', '', 1, 0, 'ACTIVE', created, created))
            connection.execute('''
                INSERT INTO barangay_officials (first_name, middle_name, last_name, suffix, position, bio, contact_info, profile_image, display_order, is_visible, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', ('Maria', 'Santos', 'Reyes', '', 'Barangay Kagawad', 'Chairperson on Peace & Order and Public Safety.', 'reyes@barangaypoblacion.gov.ph', '', 2, 1, 'ACTIVE', created, created))
            connection.execute('''
                INSERT INTO barangay_officials (first_name, middle_name, last_name, suffix, position, bio, contact_info, profile_image, display_order, is_visible, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', ('Jose', 'P.', 'Bautista', 'Jr.', 'Barangay Secretary', 'Managing official records, certifications, and municipal administrative liaison.', 'secretary@barangaypoblacion.gov.ph', '', 3, 1, 'ACTIVE', created, created))

        # Seed initial gallery items if none exist
        if not connection.execute('SELECT 1 FROM gallery_items LIMIT 1').fetchone():
            connection.execute('''
                INSERT INTO gallery_items (image_path, thumbnail_path, caption, description, display_order, is_visible, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', ('./logo.png', '', 'Official Barangay Identity & Seal', 'Official insignia representing our barangay history and mission.', 1, 1, 'ACTIVE', created, created))
            connection.execute('''
                INSERT INTO gallery_items (image_path, thumbnail_path, caption, description, display_order, is_visible, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', ('./compressed_b2f0e3ddc22210e534db1aade7c52180.png', '', 'Community Hall & Assembly', 'Public gathering space for barangay assemblies and civic sessions.', 2, 1, 'ACTIVE', created, created))
            connection.execute('''
                INSERT INTO gallery_items (image_path, thumbnail_path, caption, description, display_order, is_visible, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', ('./assets/logo.png', '', 'E-Governance & Public Services', 'Digital service processing and community resource centers.', 3, 1, 'ACTIVE', created, created))
        existing_residents = connection.execute('SELECT id, resident_id FROM residents').fetchall()
        for resident in existing_residents:
            if not connection.execute('SELECT 1 FROM resident_id_issuances WHERE resident_record_id = ?', (resident['id'],)).fetchone():
                status = 'Cancelled' if connection.execute('SELECT 1 FROM residents WHERE id = ? AND archived_at IS NOT NULL', (resident['id'],)).fetchone() else 'Assigned'
                connection.execute('INSERT INTO resident_id_issuances (resident_record_id, resident_id, issuance_number, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)', (resident['id'], resident['resident_id'], f'CARD-MIGRATED-{resident["id"]:06d}', status, created, created))
        # Backfill structured task columns from stored payloads (legacy rows).
        user_rows = connection.execute('SELECT id, name FROM users').fetchall()
        name_to_id = {row['name'].strip().lower(): row['id'] for row in user_rows}
        for task in connection.execute('SELECT id, payload_json FROM tasks').fetchall():
            try:
                payload = json.loads(task['payload_json'])
            except (TypeError, ValueError):
                continue
            assignee = str(payload.get('assignedTo', '')).strip().lower()
            updates, params = [], []
            if not connection.execute('SELECT title FROM tasks WHERE id = ?', (task['id'],)).fetchone()['title'] and payload.get('title'):
                updates.append('title = ?'); params.append(payload.get('title'))
            if payload.get('taskType'):
                updates.append('task_type = ?'); params.append(payload.get('taskType'))
            if payload.get('priority'):
                updates.append('priority = ?'); params.append(payload.get('priority'))
            if payload.get('dueDate'):
                updates.append('due_date = ?'); params.append(payload.get('dueDate'))
            if assignee and assignee in name_to_id:
                updates.append('assigned_to_user_id = ?'); params.append(name_to_id[assignee])
            if updates:
                connection.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", [*params, task['id']])
        # Ensure unique index on users(resident_record_id) to prevent duplicate accounts per resident
        connection.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_users_resident_record ON users(resident_record_id) WHERE resident_record_id IS NOT NULL')
        connection.execute('CREATE INDEX IF NOT EXISTS idx_users_verification_token ON users(verification_token_hash) WHERE verification_token_hash IS NOT NULL')

        # Seed standard official resident records if none exist
        seed_residents = [
            ('BRGY-2026-000001', 'HH-0001', 'Dela Cruz', 'Juan', 'Aquino', '1995-01-01', 'Male', 'Single', 'Purok 1, Main Street', '09171234567', 'Yes', 'Senior Citizen'),
            ('BRGY-2026-000002', 'HH-0002', 'Santos', 'Maria Clara', 'Reyes', '1992-05-12', 'Female', 'Single', 'Purok 2, Rizal Avenue', '09181234567', 'Yes', 'PWD'),
            ('BRGY-2026-000003', 'HH-0003', 'Cruz', 'Pedro', 'Bautista', '1988-11-20', 'Male', 'Widowed', 'Purok 3, Mabini Street', '09191234567', 'Yes', 'Solo Parent'),
            ('BRGY-2026-000004', 'HH-0004', 'Reyes', 'Ana', 'Santos', '2004-08-15', 'Female', 'Single', 'Purok 4, Bonifacio Street', '09201234567', 'No', 'Student'),
            ('BRGY-2026-000005', 'HH-0005', 'Garcia', 'Carlos', 'Mendoza', '1985-03-30', 'Male', 'Married', 'Purok 5, Luna Street', '09211234567', 'Yes', 'Regular Resident'),
        ]
        for rid, hh, ln, fn, mn, bd, g, cs, addr, cont, vot, cls in seed_residents:
            if not connection.execute('SELECT 1 FROM residents WHERE resident_id = ?', (rid,)).fetchone():
                connection.execute('''
                    INSERT INTO residents (resident_id, household_no, last_name, first_name, middle_name, birth_date, gender, civil_status, address, contact, voter, classification, resident_status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                ''', (rid, hh, ln, fn, mn, bd, g, cs, addr, cont, vot, cls, created, created))
                rec = connection.execute('SELECT id FROM residents WHERE resident_id = ?', (rid,)).fetchone()
                connection.execute('INSERT OR IGNORE INTO resident_id_issuances (resident_record_id, resident_id, issuance_number, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)',
                    (rec['id'], rid, f'CARD-{datetime.now(timezone.utc).year}-{rec["id"]:06d}', 'Assigned', created, created))

        for username, password, name, role, pos, dept, phone in (
            ('admin', 'admin123', 'System Administrator', 'admin', 'System Administrator', 'Administration', '0917-000-0000'),
            ('staff', 'staff123', 'Maria Clara Santos', 'staff', 'Barangay Staff Officer', 'Operations & Public Service', '0918-123-4567'),
            ('staff2', 'staff123', 'Juan Dela Cruz', 'staff', 'Community Welfare Officer', 'Social Services & Welfare', '0918-234-5678'),
            ('staff3', 'staff123', 'Liza Reyes', 'staff', 'Administrative & Records Assistant', 'Secretariat & Records', '0918-345-6789'),
            ('resident', 'resident123', 'Juan A. Dela Cruz', 'resident', 'Resident Citizen', 'Community Member', '0919-000-0000'),
        ):
            if not connection.execute('SELECT 1 FROM users WHERE username = ?', (username,)).fetchone():
                connection.execute(
                    'INSERT INTO users (username, password_hash, name, role, account_status, position, department, contact_number, created_at) VALUES (?, ?, ?, ?, "active", ?, ?, ?, ?)',
                    (username, password_hash(password), name, role, pos, dept, phone, created)
                )

        # Ensure dedicated Punong Barangay account
        pb_row = connection.execute("SELECT id FROM users WHERE role = 'punong_barangay'").fetchone()
        if not pb_row:
            connection.execute(
                "INSERT INTO users (username, password_hash, name, role, account_status, position, department, contact_number, created_at) VALUES (?, ?, ?, 'punong_barangay', 'active', 'Punong Barangay', 'Executive Leadership', '0917-888-0001', ?)",
                ('punong_barangay', password_hash('captain123'), 'Punong Barangay', created)
            )
        # Ensure staff position and department
        connection.execute("UPDATE users SET position = COALESCE(NULLIF(position, ''), 'Barangay Staff Officer'), department = COALESCE(NULLIF(department, ''), 'Operations & Public Service'), contact_number = COALESCE(NULLIF(contact_number, ''), '0918-123-4567') WHERE role = 'staff'")
        # Backfill task_assignments for existing tasks
        for t in connection.execute("SELECT id, assigned_to_user_id, status, created_at FROM tasks").fetchall():
            if t['assigned_to_user_id']:
                t_status = str(t['status']).upper()
                assign_status = 'COMPLETED' if t_status in ('DONE', 'COMPLETED') else 'IN_PROGRESS' if t_status in ('IN PROGRESS', 'ASSIGNED') else 'PENDING'
                connection.execute("INSERT OR IGNORE INTO task_assignments (task_id, staff_id, status, assigned_at) VALUES (?, ?, ?, ?)",
                    (t['id'], t['assigned_to_user_id'], assign_status, t['created_at']))
        connection.execute("UPDATE users SET account_status = 'active' WHERE account_status IS NULL OR account_status = ''")
        # Link default demo resident account to BRGY-2026-000001 (Juan Dela Cruz, Senior Citizen)
        juan_res = connection.execute("SELECT id FROM residents WHERE resident_id = 'BRGY-2026-000001'").fetchone()
        if juan_res:
            connection.execute("UPDATE users SET resident_record_id = ? WHERE username = 'resident' AND (resident_record_id IS NULL OR resident_record_id != ?)", (juan_res['id'], juan_res['id']))
            connection.execute("UPDATE residents SET owner_user_id = (SELECT id FROM users WHERE username = 'resident') WHERE id = ?", (juan_res['id'],))


def add_cors_headers(handler):
    """Grant CORS only to explicitly configured browser origins."""
    origin = handler.headers.get('Origin', '').strip().rstrip('/')
    if origin and origin in config.ALLOWED_ORIGINS:
        handler.send_header('Access-Control-Allow-Origin', origin)
        handler.send_header('Vary', 'Origin')
    elif not origin and config.ALLOWED_ORIGINS:
        # Keep non-browser API clients compatible without granting arbitrary origins.
        handler.send_header('Access-Control-Allow-Origin', next(iter(config.ALLOWED_ORIGINS)))
    handler.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
    handler.send_header('Access-Control-Allow-Headers', 'Content-Type, Authorization')
    handler.send_header('Access-Control-Allow-Private-Network', 'true')
    handler.send_header('Access-Control-Max-Age', '86400')


def add_security_headers(handler):
    """Apply browser protections that are compatible with the legacy frontend."""
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
    handler.send_header('Permissions-Policy', 'camera=(), microphone=(), geolocation=()')
    handler.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
    handler.send_header('X-Frame-Options', 'DENY')
    if config.REQUIRE_HTTPS:
        handler.send_header('Strict-Transport-Security', 'max-age=31536000; includeSubDomains')


def client_ip(handler):
    forwarded = handler.headers.get('X-Forwarded-For')
    if forwarded:
        return forwarded.split(',')[0].strip()
    return handler.client_address[0]


def allow_rate(handler, bucket, limit, window=RATE_LIMIT_WINDOW_SECONDS):
    """Apply a small process-local abuse guard; use an edge limiter for multi-instance deployments."""
    if not config.RATE_LIMIT_ENABLED or limit <= 0:
        return True
    key = (bucket, client_ip(handler))
    current = time.monotonic()
    with RATE_LIMIT_LOCK:
        timestamps = [stamp for stamp in RATE_LIMIT_STATE.get(key, []) if current - stamp < window]
        if len(timestamps) >= limit:
            RATE_LIMIT_STATE[key] = timestamps
            retry_after = max(1, int(window - (current - timestamps[0])))
            json_response(handler, 429, {'error': 'Too many requests. Please try again later.', 'code': 'RATE_LIMITED'}, extra_headers={'Retry-After': str(retry_after)})
            return False
        timestamps.append(current)
        RATE_LIMIT_STATE[key] = timestamps
    return True


def json_response(handler, status, value, extra_headers=None):
    data = json.dumps(value).encode('utf-8')
    handler.send_response(status)
    handler.send_header('Content-Type', 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(data)))
    add_security_headers(handler)
    add_cors_headers(handler)
    for key, header_value in (extra_headers or {}).items():
        handler.send_header(key, header_value)
    handler.end_headers()
    handler.wfile.write(data)


def row_payload(row):
    value = dict(row)
    if 'payload_json' in value:
        try:
            payload = json.loads(value.pop('payload_json'))
        except (TypeError, ValueError):
            payload = {}
        payload.update(value)
        return payload
    return value


def role_label(role):
    return ROLE_LABELS.get(role, str(role or '').replace('_', ' ').title())


def get_setting(connection, key, default=''):
    row = connection.execute('SELECT setting_value FROM system_settings WHERE setting_key = ?', (key,)).fetchone()
    return row['setting_value'] if row else default


def set_setting(connection, key, value):
    connection.execute('INSERT INTO system_settings (setting_key, setting_value, updated_at) VALUES (?, ?, ?) ON CONFLICT(setting_key) DO UPDATE SET setting_value = excluded.setting_value, updated_at = excluded.updated_at', (key, value, now()))


def get_protection_settings(connection):
    """Return content protection settings from system_settings as a dict."""
    year = datetime.now(timezone.utc).year
    b_name = get_setting(connection, 'barangay_name', 'Barangay Poblacion')
    wm_text_raw = get_setting(connection, 'cp_watermark_text', 'OFFICIAL BARANGAY WEBSITE')
    # Substitute dynamic tokens
    wm_text = wm_text_raw.replace('{barangay}', b_name).replace('{year}', str(year))
    return {
        'enabled': get_setting(connection, 'cp_enabled', 'true').lower() == 'true',
        'watermarkEnabled': get_setting(connection, 'cp_watermark_enabled', 'true').lower() == 'true',
        'watermarkText': wm_text,
        'watermarkTextRaw': wm_text_raw,
        'watermarkOpacity': float(get_setting(connection, 'cp_watermark_opacity', '0.30')),
        'watermarkPosition': get_setting(connection, 'cp_watermark_position', 'bottom-right'),
        'rightClickProtection': get_setting(connection, 'cp_right_click_protection', 'true').lower() == 'true',
        'dragPrevention': get_setting(connection, 'cp_drag_prevention', 'true').lower() == 'true',
        'pillowAvailable': PILLOW_AVAILABLE,
    }


def apply_watermark(image_bytes, watermark_text, opacity=0.30, position='bottom-right'):
    """Apply a visible text watermark to an image using Pillow.

    Returns the watermarked image as bytes (same format as input).
    Falls back to returning the original bytes if Pillow is unavailable
    or an error occurs — ensuring uploads always succeed.

    Args:
        image_bytes: Raw image bytes (JPG, PNG, or WebP).
        watermark_text: Text to render as watermark.
        opacity: Float 0.0–1.0 (default 0.30).
        position: One of 'bottom-right', 'bottom-left', 'center', 'bottom-center'.

    Returns:
        bytes: Watermarked image bytes (PNG for transparency support).
    """
    if not PILLOW_AVAILABLE:
        return image_bytes

    try:
        opacity = max(0.05, min(0.70, float(opacity)))

        # Open source image and ensure RGBA for transparency compositing
        src = Image.open(io.BytesIO(image_bytes)).convert('RGBA')
        w, h = src.size

        # ── Watermark layer ────────────────────────────────────────────────
        wm_layer = Image.new('RGBA', src.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(wm_layer)

        # Responsive font size: ~2.5% of the shorter dimension
        font_size = max(14, int(min(w, h) * 0.028))

        # Try to use a system font; fall back to default bitmap font
        font = None
        font_candidates = [
            'arialbd.ttf', 'arial.ttf', 'DejaVuSans-Bold.ttf',
            'DejaVuSans.ttf', 'LiberationSans-Bold.ttf', 'Roboto-Bold.ttf',
        ]
        for candidate in font_candidates:
            try:
                font = ImageFont.truetype(candidate, font_size)
                break
            except (IOError, OSError):
                continue
        if font is None:
            try:
                font = ImageFont.load_default(size=font_size)
            except TypeError:
                font = ImageFont.load_default()

        # Measure text bounding box
        bbox = draw.textbbox((0, 0), watermark_text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]

        # Padding around text
        pad_x, pad_y = max(12, int(w * 0.012)), max(8, int(h * 0.010))

        # ── Position calculation ───────────────────────────────────────────
        margin_x = max(16, int(w * 0.020))
        margin_y = max(14, int(h * 0.020))

        if position == 'bottom-right':
            x = w - text_w - pad_x * 2 - margin_x
            y = h - text_h - pad_y * 2 - margin_y
        elif position == 'bottom-left':
            x = margin_x
            y = h - text_h - pad_y * 2 - margin_y
        elif position == 'bottom-center':
            x = (w - text_w - pad_x * 2) // 2
            y = h - text_h - pad_y * 2 - margin_y
        else:  # center / diagonal
            x = (w - text_w - pad_x * 2) // 2
            y = (h - text_h - pad_y * 2) // 2

        alpha_int = int(opacity * 255)

        # ── Draw pill-shaped semi-transparent background ───────────────────
        bg_x0 = x - 2
        bg_y0 = y - 2
        bg_x1 = x + text_w + pad_x * 2 + 2
        bg_y1 = y + text_h + pad_y * 2 + 2
        draw.rounded_rectangle(
            [bg_x0, bg_y0, bg_x1, bg_y1],
            radius=6,
            fill=(0, 0, 0, max(30, alpha_int // 2)),
        )

        # ── Draw watermark text ────────────────────────────────────────────
        text_x = x + pad_x
        text_y = y + pad_y

        # Subtle shadow for legibility on both light and dark photos
        for dx, dy in ((1, 1), (1, -1), (-1, 1), (-1, -1)):
            draw.text(
                (text_x + dx, text_y + dy),
                watermark_text, font=font,
                fill=(0, 0, 0, min(255, alpha_int + 40)),
            )

        # Main text — white with configured opacity
        draw.text(
            (text_x, text_y),
            watermark_text, font=font,
            fill=(255, 255, 255, alpha_int),
        )

        # ── Composite and export ───────────────────────────────────────────
        composited = Image.alpha_composite(src, wm_layer)

        # Determine output format — keep JPG as JPG (no transparency), rest as PNG
        orig_fmt = 'JPEG'
        try:
            orig = Image.open(io.BytesIO(image_bytes))
            orig_fmt = orig.format or 'JPEG'
        except Exception:
            pass

        buf = io.BytesIO()
        if orig_fmt == 'JPEG':
            composited = composited.convert('RGB')
            composited.save(buf, format='JPEG', quality=88, optimize=True)
        elif orig_fmt == 'WEBP':
            composited.save(buf, format='WEBP', quality=88)
        else:
            composited.save(buf, format='PNG', optimize=True)
        return buf.getvalue()

    except Exception as exc:
        logging.error('apply_watermark failed: %s', exc)
        return image_bytes  # graceful fallback — never lose the original


def process_and_save_image(base64_data, category='officials', connection=None, apply_wm=True, max_dim=1600):
    """
    Validates, processes, and safely saves an image upload.
    Returns (original_rel_path, public_rel_path).
    Raises ValueError with a user-friendly message on invalid data.
    """
    if not base64_data:
        raise ValueError("No image data provided.")

    data_str = str(base64_data).strip()
    if ',' in data_str:
        header, raw_b64 = data_str.split(',', 1)
    else:
        header, raw_b64 = '', data_str

    try:
        raw_bytes = base64.b64decode(raw_b64)
    except Exception:
        raise ValueError("Invalid base64 image data.")

    if len(raw_bytes) > 5 * 1024 * 1024:
        raise ValueError("Image file exceeds maximum allowed size of 5MB.")
    if len(raw_bytes) < 12:
        raise ValueError("Image file is too small or corrupt.")

    # Validate header magic bytes
    ext = None
    if raw_bytes.startswith(b'\xff\xd8\xff'):
        ext = '.jpg'
    elif raw_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
        ext = '.png'
    elif raw_bytes.startswith(b'RIFF') and b'WEBP' in raw_bytes[:16]:
        ext = '.webp'
    else:
        raise ValueError("Invalid image format. Only JPG, PNG, and WebP files are permitted.")

    timestamp_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    token_hex = secrets.token_hex(4)
    filename = f"{category}_{timestamp_str}_{token_hex}{ext}"

    if category == 'residents':
        dest_dir = ROOT / 'uploads' / 'residents'
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / filename).write_bytes(raw_bytes)
        rel_path = f"uploads/residents/{filename}"
        return rel_path, rel_path

    # Public categories: officials, gallery
    orig_dir = ROOT / 'uploads' / category / 'original'
    orig_dir.mkdir(parents=True, exist_ok=True)
    (orig_dir / filename).write_bytes(raw_bytes)
    orig_rel_path = f"./uploads/{category}/original/{filename}"

    pub_dir = ROOT / 'uploads' / category / 'public'
    pub_dir.mkdir(parents=True, exist_ok=True)
    pub_filename = f"{category}_{timestamp_str}_{token_hex}_wm{ext}"
    pub_path = pub_dir / pub_filename

    # Watermark if enabled
    ps = get_protection_settings(connection) if connection else {
        'watermarkEnabled': True,
        'watermarkText': 'OFFICIAL BARANGAY WEBSITE',
        'watermarkOpacity': 0.30,
        'watermarkPosition': 'bottom-right'
    }
    if apply_wm and ps.get('watermarkEnabled', True) and PILLOW_AVAILABLE:
        try:
            wm_bytes = apply_watermark(
                raw_bytes,
                watermark_text=ps.get('watermarkText', 'OFFICIAL BARANGAY WEBSITE'),
                opacity=ps.get('watermarkOpacity', 0.30),
                position=ps.get('watermarkPosition', 'bottom-right')
            )
            pub_path.write_bytes(wm_bytes)
            pub_rel_path = f"./uploads/{category}/public/{pub_filename}"
        except Exception as e:
            logging.error("Failed to watermark %s: %s", category, e)
            pub_path.write_bytes(raw_bytes)
            pub_rel_path = f"./uploads/{category}/public/{pub_filename}"
    else:
        pub_path.write_bytes(raw_bytes)
        pub_rel_path = f"./uploads/{category}/public/{pub_filename}"

    return orig_rel_path, pub_rel_path


def notify_users(connection, user_ids, title, body, notification_type, exclude_user_id=None):
    created = now()
    unique_ids = {uid for uid in user_ids if uid and uid != exclude_user_id}
    for uid in unique_ids:
        connection.execute(
            'INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at) VALUES (?, ?, ?, ?, 0, ?)',
            (uid, title, body, notification_type, created),
        )


def staff_user_ids(connection):
    return [row['id'] for row in connection.execute("SELECT id FROM users WHERE role IN ('admin', 'staff') AND account_status IN ('active', 'verified')")]


def resident_user_ids(connection):
    return [row['id'] for row in connection.execute("SELECT id FROM users WHERE role = 'resident' AND account_status IN ('active', 'verified')")]


def log_activity(connection, event_type, actor_user_id, payload):
    connection.execute(
        'INSERT INTO activity_logs (event_type, actor_user_id, payload_json, created_at) VALUES (?, ?, ?, ?)',
        (event_type, actor_user_id, json.dumps(payload), now()),
    )


def staff_payload(connection, row):
    value = dict(row)
    value.pop('password_hash', None)
    handler = None
    if value.get('handler_user_id'):
        handler = connection.execute('SELECT id, name, username FROM users WHERE id = ? AND role = \'admin\'', (value['handler_user_id'],)).fetchone()
    value['handler'] = dict(handler) if handler else None
    value['handlerSince'] = value.get('handler_assigned_at')
    value['status'] = 'INACTIVE' if value.get('account_status') == 'disabled' else str(value.get('account_status') or 'active').upper()
    value['createdByName'] = None
    value['updatedByName'] = None
    if value.get('created_by'):
        created_by = connection.execute('SELECT name FROM users WHERE id = ?', (value['created_by'],)).fetchone()
        value['createdByName'] = created_by['name'] if created_by else None
    if value.get('updated_by'):
        updated_by = connection.execute('SELECT name FROM users WHERE id = ?', (value['updated_by'],)).fetchone()
        value['updatedByName'] = updated_by['name'] if updated_by else None
    value['taskCount'] = connection.execute('SELECT COUNT(*) AS n FROM task_assignments WHERE staff_id = ?', (value['id'],)).fetchone()['n']
    value['completedTaskCount'] = connection.execute("SELECT COUNT(*) AS n FROM task_assignments WHERE staff_id = ? AND upper(status) IN ('COMPLETED', 'DONE')", (value['id'],)).fetchone()['n']
    return value


def google_configured():
    return all(os.environ.get(key) for key in ('GOOGLE_CLIENT_ID', 'GOOGLE_CLIENT_SECRET'))


def google_redirect_uri(handler):
    configured = os.environ.get('GOOGLE_REDIRECT_URI')
    if configured:
        return configured
    return f'http://{handler.headers.get("Host", "127.0.0.1:8000")}/api/google/callback'


def google_request(url, data=None, headers=None):
    body = urlencode(data).encode() if data is not None else None
    request = Request(url, data=body, headers=headers or {})
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode('utf-8'))


def google_result_page(handler, message, success=False):
    title = 'Registration submitted' if success else 'Google verification unavailable'
    color = '#2F855A' if success else '#C53030'
    target = '/?registration=success' if success else '/?registration=error'
    body = f'''<!doctype html><html><head><meta charset="utf-8"><title>{escape(title)}</title>
<meta http-equiv="refresh" content="3;url={target}"></head><body style="font-family:Segoe UI,sans-serif;text-align:center;padding:4rem">
<h1 style="color:{color}">{escape(title)}</h1><p>{escape(message)}</p><p>You will return to the Barangay portal shortly.</p></body></html>'''.encode()
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/html; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    add_security_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


def verification_result_page(handler, result):
    """Legacy email-verification endpoint — email verification is no longer required."""
    body = b'''<!doctype html><html><head><meta charset="utf-8"><title>Email Verification</title>
<meta http-equiv="refresh" content="3;url=/"></head><body style="font-family:Segoe UI,sans-serif;text-align:center;padding:4rem">
<h1 style="color:#2F855A">Email Verification</h1>
<p>Email verification is no longer required. You may sign in directly.</p>
<p>You will return to the Barangay portal shortly.</p></body></html>'''
    handler.send_response(200)
    handler.send_header('Content-Type', 'text/html; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    add_security_headers(handler)
    add_cors_headers(handler)
    handler.end_headers()
    handler.wfile.write(body)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        logging.info("%s - %s", self.client_address[0], format % args)

    def do_OPTIONS(self):
        """Handle CORS preflight requests from the browser."""
        self.send_response(204)
        add_security_headers(self)
        add_cors_headers(self)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def read_json(self):
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except (TypeError, ValueError):
            raise ValueError('Invalid Content-Length')
        if length < 0:
            raise ValueError('Invalid Content-Length')
        if length > MAX_BODY_BYTES:
            raise ValueError('Request body is too large')
        value = json.loads(self.rfile.read(length) or '{}')
        if not isinstance(value, dict):
            raise ValueError('JSON object expected')
        return value

    def user(self):
        token = self.headers.get('Authorization', '').removeprefix('Bearer ').strip()
        session = TOKENS.get(token)
        if not session:
            return None
        if session['expires_at'] <= time.time():
            TOKENS.pop(token, None)
            return None
        with db() as connection:
            row = connection.execute('SELECT * FROM users WHERE id = ?', (session['user_id'],)).fetchone()
            if not row or row['account_status'] in ('archived', 'disabled', 'suspended', 'rejected'):
                return None
            return dict(row)

    def require_user(self, roles=()):
        user = self.user()
        if not user:
            json_response(self, 401, {'error': 'Unauthorized'})
            return None
        if roles and user['role'] not in roles:
            json_response(self, 403, {'error': 'Forbidden'})
            return None
        return user

    # ------------------------------------------------------------------
    # Google registration flow
    # ------------------------------------------------------------------
    def google_callback(self, query):
        error = query.get('error', [None])[0]
        state = query.get('state', [None])[0]
        if error:
            return google_result_page(self, 'Google verification was cancelled or denied.')
        pending = GOOGLE_STATES.pop(state, None) if state else None
        if not pending or pending['expires_at'] <= time.time() or not query.get('code', [None])[0]:
            return google_result_page(self, 'The Google verification session expired. Please start registration again.')
        try:
            token = google_request('https://oauth2.googleapis.com/token', {
                'code': query['code'][0], 'client_id': os.environ['GOOGLE_CLIENT_ID'],
                'client_secret': os.environ['GOOGLE_CLIENT_SECRET'], 'redirect_uri': google_redirect_uri(self),
                'grant_type': 'authorization_code',
            })
            identity = google_request('https://openidconnect.googleapis.com/v1/userinfo', headers={
                'Authorization': f"Bearer {token['access_token']}"
            })
        except Exception:
            return google_result_page(self, 'Google could not verify this account. Please try again.')
        if not identity.get('sub') or not identity.get('email') or identity.get('email_verified') is not True:
            return google_result_page(self, 'Google must confirm a verified email address before registration can continue.')
        details = pending['details']
        google_email = identity['email'].strip().lower()
        supplied_email = str(details.get('email', '')).strip().lower()
        if supplied_email and supplied_email != google_email:
            return google_result_page(self, 'The Google email does not match the email entered during registration.')
        with db() as connection:
            if connection.execute('SELECT 1 FROM users WHERE google_subject_id = ? OR lower(google_email) = ?', (identity['sub'], google_email)).fetchone():
                return google_result_page(self, 'This Google account is already associated with a Barangay account.')
            classification_val = str(details.get('classification', '')).strip().lower()
            resident = connection.execute(
                '''SELECT * FROM residents 
                   WHERE resident_id = ? 
                     AND lower(trim(last_name)) = ? 
                     AND lower(trim(first_name)) = ? 
                     AND trim(birth_date) = ? 
                     AND lower(trim(classification)) = ? 
                     AND archived_at IS NULL''',
                (str(details['residentId']).strip(), str(details['lastName']).strip().lower(), str(details['firstName']).strip().lower(), str(details['birthDate']).strip(), classification_val),
            ).fetchone()
            if not resident:
                return google_result_page(self, 'Resident verification failed: The provided details (including classification) do not match our official barangay resident records.')
            if connection.execute('SELECT 1 FROM users WHERE resident_record_id = ?', (resident['id'],)).fetchone():
                return google_result_page(self, 'An online account already exists for this resident. Please sign in instead.')
            cursor = connection.execute(
                'INSERT INTO users (username, password_hash, name, role, email, email_verified, email_verified_at, resident_record_id, account_status, google_verified, google_subject_id, google_email, google_verified_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (str(details['username']).strip().lower(), password_hash(details['password']), f"{resident['first_name']} {resident['last_name']}", 'resident', google_email, 1, now(), resident['id'], 'active', 1, identity['sub'], google_email, now(), now()),
            )
            user_id = cursor.lastrowid
            connection.execute('UPDATE residents SET owner_user_id = ?, updated_at = ? WHERE id = ?', (user_id, now(), resident['id']))
            log_activity(connection, 'user:registered', user_id, {'username': str(details['username']).strip().lower(), 'residentId': resident['resident_id'], 'classification': resident['classification'], 'method': 'google'})
        return google_result_page(self, 'Your Google account was verified and your resident account is active. You may now sign in.', success=True)

    # ------------------------------------------------------------------
    # GET routes
    # ------------------------------------------------------------------
    def do_GET(self):
        raw_path = urlparse(self.path).path
        path = posixpath.normpath(raw_path)
        if path == '/api/verify-email':
            # Email verification is no longer required. Redirect to portal.
            return verification_result_page(self, 'verified')
        if path in ('/health', '/api/health'):
            try:
                with db() as connection:
                    connection.execute('SELECT 1').fetchone()
                return json_response(self, 200, {
                    'success': True,
                    'ok': True,
                    'status': 'ok',
                    'database': 'connected',
                    'db_path': str(DB_PATH.name),
                    'timestamp': now(),
                    'version': '2.0.0'
                })
            except Exception as e:
                logging.error('Health check database failure: %s', e)
                return json_response(self, 503, {
                    'success': False,
                    'ok': False,
                    'status': 'error',
                    'database': 'disconnected',
                    'error': 'Database unavailable'
                })
        if path.startswith('/uploads/residents/'):
            user = self.user()
            if not user:
                return json_response(self, 401, {'error': 'Authentication required to view resident profile photos'})
            return self.serve_static()
        if path == '/api/public/info':
            with db() as connection:
                admin_row = connection.execute("SELECT name FROM users WHERE role = 'admin' AND account_status IN ('active', 'verified') ORDER BY id LIMIT 1").fetchone()
                staff_row = connection.execute("SELECT name FROM users WHERE role = 'staff' AND account_status IN ('active', 'verified') ORDER BY id LIMIT 1").fetchone()
                b_name = get_setting(connection, 'barangay_name', 'Barangay Poblacion')
                m_name = get_setting(connection, 'municipality', 'City of Manila')
                pb = get_setting(connection, 'punong_barangay', '')
                sec = get_setting(connection, 'barangay_secretary', '') or (staff_row['name'] if staff_row else 'Barangay Secretary')
                hero_image_orig = get_setting(connection, 'hero_image_path', './assets/hero1.jpg')
                hero_protected = get_setting(connection, 'hero_image_protected_path', '')
                hero_image = hero_protected if hero_protected else hero_image_orig
                hero_focal = get_setting(connection, 'hero_image_focal_position', 'center')
                hero_updated = get_setting(connection, 'hero_image_updated_at', '')
                hero_overlay = get_setting(connection, 'hero_overlay_opacity', '0.45')
                hero_visible = get_setting(connection, 'hero_visible', 'true').lower() == 'true'
                welcome_badge = get_setting(connection, 'welcome_badge', 'OFFICIAL COMMUNITY PORTAL')
                welcome_title = get_setting(connection, 'welcome_title', 'SERVING OUR COMMUNITY WITH INTEGRITY & EXCELLENCE')
                welcome_subtitle = get_setting(connection, 'welcome_subtitle', 'Welcome to the official online portal of Barangay Poblacion. Access barangay certificates, view community announcements, track service requests, and stay connected with your local government.')
                contact_addr = get_setting(connection, 'contact_address', 'Barangay Hall, Main Street, Poblacion, City of Manila')
                contact_phone = get_setting(connection, 'contact_phone', '(02) 8123-4567 / +63 917 123 4567')
                contact_email = get_setting(connection, 'contact_email', 'info@barangaypoblacion.gov.ph')
                contact_hours = get_setting(connection, 'contact_office_hours', 'Monday - Friday: 8:00 AM - 5:00 PM')
                contact_emerg = get_setting(connection, 'contact_emergency', '117 / 911 / (02) 8999-9999')
                contact_web = get_setting(connection, 'contact_website', 'https://barangaypoblacion.gov.ph')
                social_fb = get_setting(connection, 'social_facebook', 'https://facebook.com/barangaypoblacion')
                social_tw = get_setting(connection, 'social_twitter', '')
                social_yt = get_setting(connection, 'social_youtube', '')
                btn_portal = get_setting(connection, 'btn_portal_label', 'ENTER BMS PORTAL')
                btn_services = get_setting(connection, 'btn_services_label', 'E-GOVERNMENT SERVICES')
                btn_announcements = get_setting(connection, 'btn_announcements_label', 'VIEW ANNOUNCEMENTS')
                btn_programs = get_setting(connection, 'btn_programs_label', 'COMMUNITY PROGRAMS')
                officials_rows = connection.execute("SELECT name, role FROM users WHERE role IN ('admin', 'staff') AND account_status IN ('active', 'verified') ORDER BY role ASC, name ASC").fetchall()
                res_count = connection.execute("SELECT COUNT(*) AS n FROM residents WHERE archived_at IS NULL").fetchone()['n']
                ann_count = connection.execute("SELECT COUNT(*) AS n FROM announcements WHERE archived_at IS NULL").fetchone()['n']
                prog_count = connection.execute("SELECT COUNT(*) AS n FROM programs WHERE status IN ('Scheduled', 'Ongoing')").fetchone()['n']
                gallery_rows = connection.execute("SELECT id, image_path, thumbnail_path, caption, description, display_order FROM gallery_items WHERE status = 'ACTIVE' AND is_visible = 1 ORDER BY display_order ASC, id ASC LIMIT 12").fetchall()
                try:
                    val = float(hero_overlay)
                    hero_overlay_num = int(round(val * 100)) if 0 < val <= 1.0 else int(round(val))
                except (ValueError, TypeError):
                    hero_overlay_num = 75
            return json_response(self, 200, {
                'barangayName': b_name,
                'municipality': m_name,
                'punongBarangay': pb,
                'barangaySecretary': sec,
                'welcomeBadge': welcome_badge,
                'welcomeTitle': welcome_title,
                'welcomeSubtitle': welcome_subtitle,
                'heroImage': hero_image,
                'heroImageFocalPosition': hero_focal,
                'heroImageUpdatedAt': hero_updated,
                'heroOverlayOpacity': hero_overlay_num,
                'heroVisible': hero_visible,
                'btnPortalLabel': btn_portal,
                'btnServicesLabel': btn_services,
                'btnAnnouncementsLabel': btn_announcements,
                'btnProgramsLabel': btn_programs,
                'contactAddress': contact_addr,
                'contactPhone': contact_phone,
                'contactEmail': contact_email,
                'contactHours': contact_hours,
                'emergencyHotline': contact_emerg,
                'contact': {
                    'address': contact_addr,
                    'phone': contact_phone,
                    'email': contact_email,
                    'officeHours': contact_hours,
                    'emergency': contact_emerg,
                    'website': contact_web,
                    'facebook': social_fb,
                    'twitter': social_tw,
                    'youtube': social_yt,
                },
                'buttons': {
                    'portal': btn_portal,
                    'services': btn_services,
                    'announcements': btn_announcements,
                    'programs': btn_programs,
                },
                'officials': [{'name': r['name'], 'role': r['role']} for r in officials_rows],
                'gallery': [{
                    'id': g['id'],
                    'caption': g['caption'],
                    'description': g['description'] or '',
                    'image': g['image_path'],
                    'thumbnail': g['thumbnail_path'] or g['image_path'],
                    'public_path': g['image_path'],
                    'display_order': g['display_order']
                } for g in gallery_rows],
                'stats': {
                    'residents': res_count,
                    'announcements': ann_count,
                    'programs': prog_count
                }
            })
        if path == '/api/public/officials':
            with db() as connection:
                rows = connection.execute('''
                    SELECT id, first_name, middle_name, last_name, suffix, position, bio, contact_info, profile_image, display_order, status
                    FROM barangay_officials
                    WHERE UPPER(status) = 'ACTIVE' AND is_visible = 1
                    ORDER BY display_order ASC, id ASC
                ''').fetchall()
            officials = []
            for r in rows:
                mn_part = f" {r['middle_name']}" if r['middle_name'] else ''
                sfx_part = f" {r['suffix']}" if r['suffix'] else ''
                full_name = f"{r['first_name']}{mn_part} {r['last_name']}{sfx_part}".strip()
                officials.append({
                    'id': r['id'],
                    'firstName': r['first_name'],
                    'middleName': r['middle_name'] or '',
                    'lastName': r['last_name'],
                    'suffix': r['suffix'] or '',
                    'name': full_name,
                    'position': r['position'],
                    'committee': r['bio'] or '',
                    'bio': r['bio'] or '',
                    'contactInfo': r['contact_info'] or '',
                    'profileImage': r['profile_image'] or '',
                    'photo_path': r['profile_image'] or '',
                    'public_path': r['profile_image'] or '',
                    'displayOrder': r['display_order'],
                    'display_order': r['display_order'],
                    'status': (r['status'] or 'active').lower()
                })
            return json_response(self, 200, officials)
        if path == '/api/public/gallery':
            with db() as connection:
                rows = connection.execute('''
                    SELECT id, image_path, thumbnail_path, caption, description, display_order
                    FROM gallery_items
                    WHERE UPPER(status) = 'ACTIVE' AND is_visible = 1
                    ORDER BY display_order ASC, id ASC
                ''').fetchall()
            return json_response(self, 200, [{
                'id': r['id'],
                'image': r['image_path'],
                'image_path': r['image_path'],
                'public_path': r['image_path'],
                'thumbnail': r['thumbnail_path'] or r['image_path'],
                'thumbnail_path': r['thumbnail_path'] or r['image_path'],
                'caption': r['caption'],
                'description': r['description'] or '',
                'displayOrder': r['display_order'],
                'display_order': r['display_order']
            } for r in rows])
        if path == '/api/admin/officials':
            user = self.require_user(('admin',))
            if not user:
                return
            params = parse_qs(urlparse(self.path).query)
            search = params.get('search', [''])[0].strip().lower()
            status_filter = params.get('status', ['all'])[0].strip().upper()
            query = "SELECT * FROM barangay_officials WHERE 1=1"
            sql_params = []
            if status_filter in ('ACTIVE', 'ARCHIVED'):
                query += " AND UPPER(status) = ?"
                sql_params.append(status_filter)
            if search:
                query += " AND (lower(first_name) LIKE ? OR lower(last_name) LIKE ? OR lower(position) LIKE ?)"
                term = f"%{search}%"
                sql_params.extend([term, term, term])
            query += " ORDER BY display_order ASC, id ASC"
            with db() as connection:
                rows = connection.execute(query, sql_params).fetchall()
            res_officials = []
            for r in rows:
                d = dict(r)
                d['name'] = f"{r['first_name']} {r['last_name']}".strip()
                d['photo_path'] = r['profile_image']
                d['public_path'] = r['profile_image']
                d['status'] = (r['status'] or 'active').lower()
                res_officials.append(d)
            return json_response(self, 200, res_officials)
        if path == '/api/admin/gallery':
            user = self.require_user(('admin',))
            if not user:
                return
            with db() as connection:
                rows = connection.execute("SELECT * FROM gallery_items ORDER BY display_order ASC, id ASC").fetchall()
            res_gallery = []
            for r in rows:
                d = dict(r)
                d['public_path'] = r['image_path']
                d['thumbnail'] = r['thumbnail_path'] or r['image_path']
                d['status'] = (r['status'] or 'active').lower()
                res_gallery.append(d)
            return json_response(self, 200, res_gallery)
        if path == '/api/admin/website-settings':
            user = self.require_user(('admin',))
            if not user:
                return
            with db() as connection:
                keys = [
                    'barangay_name', 'municipality', 'punong_barangay', 'barangay_secretary',
                    'welcome_badge', 'welcome_title', 'welcome_subtitle', 'hero_overlay_opacity', 'hero_visible',
                    'contact_address', 'contact_phone', 'contact_email', 'contact_office_hours',
                    'contact_emergency', 'contact_website', 'social_facebook', 'social_twitter', 'social_youtube',
                    'btn_portal_label', 'btn_services_label', 'btn_announcements_label', 'btn_programs_label'
                ]
                settings = {k: get_setting(connection, k, '') for k in keys}
            return json_response(self, 200, settings)
        if path == '/api/public/protection-settings':
            with db() as connection:
                ps = get_protection_settings(connection)
            # Only expose non-sensitive display settings to the public
            return json_response(self, 200, {
                'enabled': ps['enabled'],
                'watermarkEnabled': ps['watermarkEnabled'],
                'watermarkText': ps['watermarkText'],
                'watermarkOpacity': ps['watermarkOpacity'],
                'watermarkPosition': ps['watermarkPosition'],
                'rightClickProtection': ps['rightClickProtection'],
                'dragPrevention': ps['dragPrevention'],
            })
        if path == '/api/admin/protection-settings':
            user = self.require_user(('admin',))
            if not user:
                return
            with db() as connection:
                ps = get_protection_settings(connection)
            return json_response(self, 200, ps)
        if path == '/api/admin/hero-image':
            user = self.require_user(('admin',))
            if not user:
                return
            with db() as connection:
                hero_image = get_setting(connection, 'hero_image_path', './assets/hero1.jpg')
                hero_focal = get_setting(connection, 'hero_image_focal_position', 'center')
                hero_updated = get_setting(connection, 'hero_image_updated_at', '')
                hero_protected = get_setting(connection, 'hero_image_protected_path', '')
            return json_response(self, 200, {
                'heroImage': hero_image,
                'heroImageFocalPosition': hero_focal,
                'heroImageUpdatedAt': hero_updated,
                'heroImageProtectedPath': hero_protected or hero_image,
                'defaultHeroImage': './assets/hero1.jpg'
            })
        if path == '/api/public/announcements':
            with db() as connection:
                rows = connection.execute("SELECT id, title, body, category, priority, created_at FROM announcements WHERE archived_at IS NULL ORDER BY id DESC LIMIT 10").fetchall()
            return json_response(self, 200, [dict(r) for r in rows])
        if path == '/api/public/programs':
            with db() as connection:
                rows = connection.execute("SELECT event_id, title, description, event_date, start_time, end_time, location, organizer, status FROM programs WHERE status != 'Archived' ORDER BY event_date DESC, id DESC LIMIT 10").fetchall()
            return json_response(self, 200, [dict(r) for r in rows])
        if path == '/api/admin/staff':
            user = self.require_user(('admin',))
            if not user:
                return
            params = parse_qs(urlparse(self.path).query)
            status_filter = params.get('status', ['all'])[0].strip().lower()
            search = params.get('search', [''])[0].strip().lower()
            query = "SELECT * FROM users WHERE role = 'staff'"
            values = []
            if status_filter in ('active', 'disabled', 'archived'):
                query += ' AND account_status = ?'; values.append(status_filter)
            if search:
                query += ' AND (lower(name) LIKE ? OR lower(username) LIKE ? OR lower(coalesce(position, \'\')) LIKE ? OR lower(coalesce(department, \'\')) LIKE ?)'
                term = f'%{search}%'; values.extend([term, term, term, term])
            query += ' ORDER BY name ASC, id ASC'
            with db() as connection:
                rows = connection.execute(query, values).fetchall()
                result = [staff_payload(connection, row) for row in rows]
            return json_response(self, 200, result)
        if path.startswith('/api/admin/staff/') and path.endswith('/history'):
            user = self.require_user(('admin',))
            if not user:
                return
            staff_id = path.split('/')[4]
            with db() as connection:
                staff = connection.execute("SELECT id FROM users WHERE id = ? AND role = 'staff'", (staff_id,)).fetchone()
                if not staff:
                    return json_response(self, 404, {'error': 'Staff user not found'})
                history = connection.execute('''
                    SELECT h.*, p.name AS previous_handler_name, n.name AS new_handler_name, c.name AS changed_by_name
                    FROM staff_assignment_history h
                    LEFT JOIN users p ON p.id = h.previous_handler_id
                    LEFT JOIN users n ON n.id = h.new_handler_id
                    LEFT JOIN users c ON c.id = h.changed_by
                    WHERE h.staff_id = ? ORDER BY h.id DESC
                ''', (staff_id,)).fetchall()
            return json_response(self, 200, [dict(row) for row in history])
        if path == '/api/me':
            user = self.user()
            if not user:
                return json_response(self, 401, {'error': 'Not signed in'})
            resident_info = None
            user_dict = dict(user)
            if user_dict.get('resident_record_id'):
                with db() as connection:
                    res_row = connection.execute('SELECT resident_id, classification FROM residents WHERE id = ? AND archived_at IS NULL', (user_dict['resident_record_id'],)).fetchone()
                    if res_row:
                        resident_info = {'residentId': res_row['resident_id'], 'classification': res_row['classification']}
            return json_response(self, 200, {
                'id': user['id'],
                'name': user['name'],
                'role': user['role'],
                'roleLabel': role_label(user['role']),
                'initials': ''.join(part[0] for part in user['name'].split()[:2]).upper(),
                'username': user['username'],
                'position': user['position'] if 'position' in user.keys() else None,
                'department': user['department'] if 'department' in user.keys() else None,
                'accountStatus': user['account_status'],
                'residentId': resident_info['residentId'] if resident_info else None,
                'classification': resident_info['classification'] if resident_info else None
            })
        if path == '/api/dashboard-summary':
            if user := self.user():
                with db() as connection:
                    residents = connection.execute("SELECT COUNT(*) AS n FROM residents WHERE archived_at IS NULL").fetchone()['n']
                    households = connection.execute("SELECT COUNT(DISTINCT household_no) AS n FROM residents WHERE archived_at IS NULL").fetchone()['n']
                    users = connection.execute("SELECT COUNT(*) AS n FROM users").fetchone()['n']
                    requests = connection.execute("SELECT COUNT(*) AS n FROM requests").fetchone()['n']
                    pending = connection.execute(f"SELECT COUNT(*) AS n FROM requests WHERE lower(status) IN ({','.join('?' * len(PENDING_STATUSES))})", PENDING_STATUSES).fetchone()['n']
                    approved = connection.execute(f"SELECT COUNT(*) AS n FROM requests WHERE lower(status) IN ({','.join('?' * len(APPROVED_STATUSES))})", APPROVED_STATUSES).fetchone()['n']
                    announcements = connection.execute("SELECT COUNT(*) AS n FROM announcements WHERE archived_at IS NULL").fetchone()['n']
                    tasks = connection.execute("SELECT COUNT(*) AS n FROM tasks WHERE lower(status) NOT IN ('done', 'cancelled', 'completed')").fetchone()['n']
                    issued = connection.execute("SELECT COUNT(*) AS n FROM certificate_issuances").fetchone()['n']
                    programs_upcoming = connection.execute("SELECT COUNT(*) AS n FROM programs WHERE status IN ('Scheduled', 'Ongoing') AND event_date >= ?", (today(),)).fetchone()['n']
                return json_response(self, 200, {'residents': residents, 'households': households, 'users': users, 'requests': requests, 'pending': pending, 'approved': approved, 'announcements': announcements, 'tasks': tasks, 'issued': issued, 'programs': programs_upcoming})
            return json_response(self, 403, {'error': 'Forbidden'})
        if path == '/api/staff-summary':
            user = self.require_user(('admin', 'staff'))
            if not user:
                return
            with db() as connection:
                my_tasks = connection.execute("SELECT COUNT(*) AS n FROM tasks WHERE assigned_to_user_id = ? AND lower(status) NOT IN ('done', 'cancelled', 'completed')", (user['id'],)).fetchone()['n']
                my_certs = connection.execute("SELECT COUNT(*) AS n FROM activity_logs WHERE event_type = 'cert:issued' AND actor_user_id = ?", (user['id'],)).fetchone()['n']
                my_processed_today = connection.execute("SELECT COUNT(*) AS n FROM activity_logs WHERE event_type = 'request:status-updated' AND actor_user_id = ? AND substr(created_at, 1, 10) = ?", (user['id'], today())).fetchone()['n']
                my_urgent = connection.execute("SELECT COUNT(*) AS n FROM tasks WHERE assigned_to_user_id = ? AND lower(priority) IN ('high', 'critical', 'urgent') AND lower(status) NOT IN ('done', 'cancelled', 'completed')", (user['id'],)).fetchone()['n']
            return json_response(self, 200, {'myTasks': my_tasks, 'myCerts': my_certs, 'myProcessed': my_processed_today, 'myUrgent': my_urgent})
        if path == '/api/my-summary':
            user = self.require_user(('resident',))
            if not user:
                return
            with db() as connection:
                my_total = connection.execute('SELECT COUNT(*) AS n FROM requests WHERE owner_user_id = ?', (user['id'],)).fetchone()['n']
                my_pending = connection.execute(f'SELECT COUNT(*) AS n FROM requests WHERE owner_user_id = ? AND lower(status) IN ({",".join("?" * len(PENDING_STATUSES))})', [user['id'], *PENDING_STATUSES]).fetchone()['n']
                my_approved = connection.execute(f'SELECT COUNT(*) AS n FROM requests WHERE owner_user_id = ? AND lower(status) IN ({",".join("?" * len(APPROVED_STATUSES))})', [user['id'], *APPROVED_STATUSES]).fetchone()['n']
            return json_response(self, 200, {'myTotal': my_total, 'myPending': my_pending, 'myApproved': my_approved})
        if path == '/api/my-profile':
            user = self.require_user(('resident',))
            if not user:
                return
            with db() as connection:
                row = connection.execute('SELECT * FROM residents WHERE id = ? AND archived_at IS NULL', (user['resident_record_id'],)).fetchone() if user['resident_record_id'] else None
            return json_response(self, 200, {'profile': row_payload(row) if row else None})
        if path == '/api/performance':
            user = self.require_user(('admin',))
            if not user:
                return
            with db() as connection:
                rows = connection.execute('''
                    SELECT u.name AS staff_name,
                        SUM(CASE WHEN l.event_type = 'cert:issued' THEN 1 ELSE 0 END) AS certs_issued,
                        SUM(CASE WHEN l.event_type = 'request:status-updated' THEN 1 ELSE 0 END) AS requests_processed,
                        SUM(CASE WHEN l.event_type = 'resident:added' THEN 1 ELSE 0 END) AS residents_registered,
                        SUM(CASE WHEN l.event_type = 'announcement:posted' THEN 1 ELSE 0 END) AS announcements_posted
                    FROM users u LEFT JOIN activity_logs l ON l.actor_user_id = u.id
                    WHERE u.role IN ('admin', 'staff')
                    GROUP BY u.id ORDER BY u.name''').fetchall()
            return json_response(self, 200, [{**dict(row), 'roleLabel': role_label(row['role'])} for row in rows])
        if path == '/api/recent-activity':
            user = self.require_user()
            if not user:
                return
            with db() as connection:
                rows = connection.execute('''SELECT l.id, l.event_type, l.payload_json, l.created_at, u.name AS actor_name
                    FROM activity_logs l LEFT JOIN users u ON u.id = l.actor_user_id ORDER BY l.id DESC LIMIT 12''').fetchall()
            items = []
            for row in rows:
                try:
                    payload = json.loads(row['payload_json'])
                except (TypeError, ValueError):
                    payload = {}
                items.append({'id': row['id'], 'eventType': row['event_type'], 'actor': row['actor_name'] or 'System', 'summary': activity_summary(row['event_type'], payload), 'createdAt': row['created_at']})
            return json_response(self, 200, items)
        if path.startswith('/api/verify-resident/'):
            resident_id = path.removeprefix('/api/verify-resident/')
            with db() as connection:
                row = connection.execute('SELECT resident_id, resident_status FROM residents WHERE resident_id = ? AND archived_at IS NULL', (resident_id,)).fetchone()
                barangay = get_setting(connection, 'barangay_name', 'Barangay Poblacion')
            if not row:
                return json_response(self, 404, {'error': 'Resident ID not found', 'valid': False})
            return json_response(self, 200, {'valid': row['resident_status'] == 'ACTIVE', 'residentId': row['resident_id'], 'status': row['resident_status'], 'barangay': barangay})
        if path == '/api/google/callback':
            return self.google_callback(parse_qs(urlparse(self.path).query))
        if not path.startswith('/api/'):
            return self.serve_static()
        user = self.require_user()
        if not user:
            return

        # --- PUNONG BARANGAY & MESSAGING & TASK ROUTES (GET) ---
        if path == '/api/punong-barangay/dashboard':
            user = self.require_user(('punong_barangay', 'admin'))
            if not user:
                return
            with db() as connection:
                check_and_update_overdue_tasks(connection)
                total_staff = connection.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'staff' AND account_status = 'active'").fetchone()['n']
                assigned_tasks = connection.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()['n']
                pending_tasks = connection.execute("SELECT COUNT(*) AS n FROM tasks WHERE upper(status) IN ('PENDING', 'ASSIGNED')").fetchone()['n']
                in_progress_tasks = connection.execute("SELECT COUNT(*) AS n FROM tasks WHERE upper(status) IN ('IN_PROGRESS', 'ACKNOWLEDGED')").fetchone()['n']
                completed_tasks = connection.execute("SELECT COUNT(*) AS n FROM tasks WHERE upper(status) IN ('COMPLETED', 'DONE')").fetchone()['n']
                overdue_tasks = connection.execute("SELECT COUNT(*) AS n FROM tasks WHERE upper(status) = 'OVERDUE'").fetchone()['n']
                
                busy_staff = {row['staff_id'] for row in connection.execute("SELECT staff_id FROM task_assignments WHERE upper(status) IN ('PENDING', 'ACKNOWLEDGED', 'IN_PROGRESS') GROUP BY staff_id HAVING COUNT(*) > 1").fetchall()}
                available_staff = max(0, total_staff - len(busy_staff))
                
                unread_messages = connection.execute("SELECT COUNT(*) AS n FROM message_recipients WHERE recipient_id = ? AND is_read = 0", (user['id'],)).fetchone()['n']
                important_messages = connection.execute("""
                    SELECT COUNT(DISTINCT m.id) AS n 
                    FROM messages m 
                    JOIN message_recipients mr ON mr.message_id = m.id 
                    WHERE mr.recipient_id = ? AND (m.is_important = 1 OR upper(m.message_type) IN ('IMPORTANT', 'URGENT'))
                """, (user['id'],)).fetchone()['n']
                
                activity_rows = connection.execute("""
                    SELECT l.*, u.name AS actor_name, u.role AS actor_role
                    FROM activity_logs l
                    LEFT JOIN users u ON u.id = l.actor_user_id
                    WHERE l.event_type LIKE 'task:%' OR l.event_type LIKE 'message:%'
                    ORDER BY l.id DESC LIMIT 10
                """).fetchall()
                recent_activity = []
                for ar in activity_rows:
                    try:
                        p_data = json.loads(ar['payload_json'])
                    except Exception:
                        p_data = {}
                    recent_activity.append({
                        'id': ar['id'],
                        'eventType': ar['event_type'],
                        'actor': ar['actor_name'] or 'Staff',
                        'role': ar['actor_role'] or 'staff',
                        'createdAt': ar['created_at'],
                        'summary': activity_summary(ar['event_type'], p_data)
                    })
            return json_response(self, 200, {
                'totalStaff': total_staff,
                'availableStaff': available_staff,
                'assignedTasks': assigned_tasks,
                'pendingTasks': pending_tasks,
                'inProgressTasks': in_progress_tasks,
                'completedTasks': completed_tasks,
                'overdueTasks': overdue_tasks,
                'unreadMessages': unread_messages,
                'importantMessages': important_messages,
                'recentStaffActivity': recent_activity
            })

        if path == '/api/punong-barangay/staff':
            user = self.require_user(('punong_barangay', 'admin'))
            if not user:
                return
            with db() as connection:
                check_and_update_overdue_tasks(connection)
                staff_rows = connection.execute("""
                    SELECT u.id, u.username, u.name, u.role, u.position, u.department, u.contact_number, u.email,
                           u.account_status, u.profile_image, u.last_login_at
                    FROM users u
                    WHERE u.role = 'staff'
                    ORDER BY u.name ASC
                """).fetchall()
                staff_list = []
                for s in staff_rows:
                    sid = s['id']
                    task_count = connection.execute("SELECT COUNT(*) AS n FROM task_assignments WHERE staff_id = ?", (sid,)).fetchone()['n']
                    pending_count = connection.execute("SELECT COUNT(*) AS n FROM task_assignments WHERE staff_id = ? AND upper(status) IN ('PENDING', 'ASSIGNED')", (sid,)).fetchone()['n']
                    active_count = connection.execute("SELECT COUNT(*) AS n FROM task_assignments WHERE staff_id = ? AND upper(status) IN ('ACKNOWLEDGED', 'IN_PROGRESS')", (sid,)).fetchone()['n']
                    overdue_count = connection.execute("SELECT COUNT(*) AS n FROM task_assignments WHERE staff_id = ? AND upper(status) = 'OVERDUE'", (sid,)).fetchone()['n']
                    last_act_row = connection.execute("SELECT created_at FROM activity_logs WHERE actor_user_id = ? ORDER BY id DESC LIMIT 1", (sid,)).fetchone()
                    last_act = (last_act_row['created_at'] if last_act_row else s['last_login_at']) or 'None yet'
                    staff_list.append({
                        'id': s['id'],
                        'name': s['name'],
                        'username': s['username'],
                        'role': s['role'],
                        'position': s['position'] or 'Barangay Staff Officer',
                        'department': s['department'] or 'Operations & Public Service',
                        'contact': s['contact_number'] or s['email'] or 'None provided',
                        'status': s['account_status'] or 'active',
                        'taskCount': task_count,
                        'pendingTaskCount': pending_count,
                        'activeTaskCount': active_count,
                        'overdueTaskCount': overdue_count,
                        'lastActivity': last_act,
                        'profileImage': s['profile_image'] or ''
                    })
            return json_response(self, 200, staff_list)

        if path == '/api/messages/unread-count':
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            with db() as connection:
                check_and_update_overdue_tasks(connection)
                msg_unread = connection.execute("SELECT COUNT(*) AS n FROM message_recipients WHERE recipient_id = ? AND is_read = 0", (user['id'],)).fetchone()['n']
                notif_unread = connection.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND is_read = 0", (user['id'],)).fetchone()['n']
                
                # Active commands/tasks for staff
                task_pending = 0
                if user['role'] == 'staff':
                    task_pending = connection.execute("SELECT COUNT(*) AS n FROM task_assignments WHERE staff_id = ? AND upper(status) IN ('PENDING', 'ASSIGNED', 'OVERDUE')", (user['id'],)).fetchone()['n']
                elif user['role'] in ('punong_barangay', 'admin'):
                    task_pending = connection.execute("SELECT COUNT(*) AS n FROM tasks WHERE upper(status) IN ('PENDING', 'OVERDUE')").fetchone()['n']

            return json_response(self, 200, {
                'unreadMessages': msg_unread,
                'unreadNotifications': notif_unread,
                'pendingCommands': task_pending,
                'totalUnread': msg_unread
            })

        if path == '/api/notifications/unread-count':
            user = self.require_user()
            if not user:
                return
            with db() as connection:
                notif_unread = connection.execute("SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND is_read = 0", (user['id'],)).fetchone()['n']
            return json_response(self, 200, {'unreadCount': notif_unread})

        if path == '/api/notifications':
            user = self.require_user()
            if not user:
                return
            with db() as connection:
                rows = connection.execute("""
                    SELECT n.*, u.name AS sender_name, u.role AS sender_role
                    FROM notifications n
                    LEFT JOIN users u ON u.id = n.sender_id
                    WHERE n.user_id = ?
                    ORDER BY n.id DESC LIMIT 50
                """, (user['id'],)).fetchall()
            return json_response(self, 200, [dict(r) for r in rows])

        if path in ('/api/messages', '/api/messages/conversations'):
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            with db() as connection:
                check_and_update_overdue_tasks(connection)
                rows = connection.execute("""
                    SELECT DISTINCT c.*
                    FROM conversations c
                    LEFT JOIN messages m ON m.conversation_id = c.id
                    LEFT JOIN message_recipients mr ON mr.conversation_id = c.id
                    WHERE c.created_by = ? OR m.sender_id = ? OR mr.recipient_id = ?
                    ORDER BY c.updated_at DESC
                """, (user['id'], user['id'], user['id'])).fetchall()
                conv_list = []
                for r in rows:
                    cid = r['id']
                    last_msg = connection.execute("""
                        SELECT m.*, u.name AS sender_name
                        FROM messages m
                        JOIN users u ON u.id = m.sender_id
                        WHERE m.conversation_id = ?
                        ORDER BY m.id DESC LIMIT 1
                    """, (cid,)).fetchone()
                    
                    unread_count = connection.execute("""
                        SELECT COUNT(*) AS n FROM message_recipients
                        WHERE conversation_id = ? AND recipient_id = ? AND is_read = 0
                    """, (cid, user['id'])).fetchone()['n']
                    
                    other_user = None
                    if r['type'] == 'direct':
                        other_row = connection.execute("""
                            SELECT u.id, u.name, u.role, u.position, u.profile_image, u.account_status
                            FROM users u
                            WHERE u.id != ? AND (
                                u.id IN (SELECT sender_id FROM messages WHERE conversation_id = ?)
                                OR u.id IN (SELECT recipient_id FROM message_recipients WHERE conversation_id = ?)
                                OR u.id = ?
                            )
                            LIMIT 1
                        """, (user['id'], cid, cid, r['created_by'])).fetchone()
                        if other_row:
                            other_user = dict(other_row)
                    
                    conv_title = r['title']
                    if r['type'] == 'direct' and other_user:
                        conv_title = other_user['name']
                    
                    last_body = last_msg['body'] if last_msg else ''
                    last_subj = last_msg['subject'] if last_msg and last_msg['subject'] else ''
                    last_type = last_msg['message_type'] if last_msg else 'NORMAL'
                    for c_type in ('CLARIFICATION', 'TASK_UPDATE', 'TASK_COMPLETED'):
                        if f"[{c_type}]" in last_subj:
                            last_type = c_type
                            break
                    last_ts = last_msg['created_at'] if last_msg else r['created_at']
                    task_id_val = last_msg['task_id'] if last_msg else None
                    conv_list.append({
                        'id': r['id'],
                        'key': r['conversation_key'],
                        'title': conv_title,
                        'type': r['type'],
                        'otherUser': other_user,
                        'otherParticipantName': other_user['name'] if other_user else conv_title,
                        'otherParticipantRole': other_user['role'] if other_user else '',
                        'otherParticipantAvatar': other_user.get('profile_image', '') if other_user else '',
                        'otherParticipantStatus': other_user.get('account_status', 'active') if other_user else 'active',
                        'unreadCount': unread_count,
                        'lastMessageBody': last_body,
                        'lastMessageType': last_type,
                        'lastMessageAt': last_ts,
                        'taskId': task_id_val,
                        'lastMessage': {
                            'body': last_body,
                            'messageType': last_type,
                            'isImportant': bool(last_msg['is_important']) if last_msg else False,
                            'createdAt': last_ts,
                            'senderName': last_msg['sender_name'] if last_msg else '',
                            'taskId': task_id_val
                        } if last_msg else None,
                        'updatedAt': r['updated_at']
                    })
            return json_response(self, 200, conv_list)

        if path.startswith('/api/messages/') and not any(path.endswith(sfx) for sfx in ('/reply', '/read', '/important', '/archive')):
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            conv_id_str = path.removeprefix('/api/messages/')
            if not conv_id_str.isdigit():
                return json_response(self, 400, {'error': 'Invalid conversation ID'})
            conv_id = int(conv_id_str)
            with db() as connection:
                check_and_update_overdue_tasks(connection)
                conv = connection.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
                if not conv:
                    return json_response(self, 404, {'error': 'Conversation not found'})
                is_participant = connection.execute("""
                    SELECT 1 FROM conversations c
                    LEFT JOIN messages m ON m.conversation_id = c.id
                    LEFT JOIN message_recipients mr ON mr.conversation_id = c.id
                    WHERE c.id = ? AND (c.created_by = ? OR m.sender_id = ? OR mr.recipient_id = ?)
                """, (conv_id, user['id'], user['id'], user['id'])).fetchone()
                if not is_participant and user['role'] != 'admin':
                    return json_response(self, 403, {'error': 'Forbidden: You are not a participant in this conversation'})
                
                messages_rows = connection.execute("""
                    SELECT m.*, u.name AS sender_name, u.role AS sender_role, u.position AS sender_position, u.profile_image AS sender_avatar
                    FROM messages m
                    JOIN users u ON u.id = m.sender_id
                    WHERE m.conversation_id = ?
                    ORDER BY m.id ASC
                """, (conv_id,)).fetchall()
                
                enriched_msgs = []
                for m in messages_rows:
                    md = dict(m)
                    m_subj = md.get('subject') or ''
                    for c_type in ('CLARIFICATION', 'TASK_UPDATE', 'TASK_COMPLETED'):
                        if f"[{c_type}]" in m_subj:
                            md['message_type'] = c_type
                            break
                    if md.get('task_id'):
                        t_row = connection.execute("SELECT * FROM tasks WHERE id = ?", (md['task_id'],)).fetchone()
                        if t_row:
                            t_dict = dict(t_row)
                            assignees = connection.execute("""
                                SELECT ta.*, u.name AS staff_name, u.position AS staff_position, u.account_status
                                FROM task_assignments ta
                                JOIN users u ON u.id = ta.staff_id
                                WHERE ta.task_id = ?
                            """, (t_dict['id'],)).fetchall()
                            assignees_list = [dict(a) for a in assignees]
                            t_dict['assignees'] = assignees_list
                            t_dict['assignments'] = assignees_list
                            done_count = sum(1 for a in assignees_list if a.get('status') == 'COMPLETED')
                            t_dict['completedCount'] = done_count
                            t_dict['totalAssignees'] = len(assignees_list)
                            t_dict['completionSummary'] = f"{done_count} / {len(assignees_list)} Completed" if assignees_list else ""
                            my_a = next((a for a in assignees_list if a['staff_id'] == user['id']), None)
                            t_dict['my_status'] = my_a['status'] if my_a else t_dict['status']
                            t_dict['myStatus'] = t_dict['my_status']
                            md['task'] = t_dict
                    enriched_msgs.append(md)
                
                connection.execute("""
                    UPDATE message_recipients
                    SET is_read = 1, read_at = ?
                    WHERE conversation_id = ? AND recipient_id = ? AND is_read = 0
                """, (now(), conv_id, user['id']))
                
                recip_users = connection.execute("""
                    SELECT DISTINCT u.id, u.name, u.role, u.position, u.profile_image, u.account_status
                    FROM users u
                    WHERE u.id IN (SELECT sender_id FROM messages WHERE conversation_id = ?)
                       OR u.id IN (SELECT recipient_id FROM message_recipients WHERE conversation_id = ?)
                       OR u.id = ?
                """, (conv_id, conv_id, conv['created_by'])).fetchall()
                
            return json_response(self, 200, {
                'conversation': dict(conv),
                'participants': [dict(p) for p in recip_users],
                'messages': enriched_msgs
            })

        if path in ('/api/tasks', '/api/punong-barangay/tasks'):
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            if path == '/api/punong-barangay/tasks' and user['role'] not in ('punong_barangay', 'admin'):
                return json_response(self, 403, {'error': 'Forbidden: Only Punong Barangay or Admin can access this endpoint.'})
            with db() as connection:
                check_and_update_overdue_tasks(connection)
                if user['role'] in ('punong_barangay', 'admin'):
                    rows = connection.execute("""
                        SELECT t.*, u.name AS creator_name
                        FROM tasks t
                        LEFT JOIN users u ON u.id = t.created_by_user_id
                        ORDER BY t.id DESC
                    """).fetchall()
                else:
                    rows = connection.execute("""
                        SELECT t.*, u.name AS creator_name
                        FROM tasks t
                        JOIN task_assignments ta ON ta.task_id = t.id
                        LEFT JOIN users u ON u.id = t.created_by_user_id
                        WHERE ta.staff_id = ?
                        ORDER BY t.id DESC
                    """, (user['id'],)).fetchall()
                
                task_list = []
                now_dt = datetime.now(timezone.utc)
                for r in rows:
                    tid = r['id']
                    assignees = connection.execute("""
                        SELECT ta.*, u.name AS staff_name, u.position AS staff_position, u.account_status
                        FROM task_assignments ta
                        JOIN users u ON u.id = ta.staff_id
                        WHERE ta.task_id = ?
                    """, (tid,)).fetchall()
                    assignees_list = [dict(a) for a in assignees]
                    
                    latest_update = connection.execute("""
                        SELECT progress_percent, update_text, created_at, staff_id
                        FROM task_updates
                        WHERE task_id = ? AND update_type = 'progress'
                        ORDER BY id DESC LIMIT 1
                    """, (tid,)).fetchone()
                    
                    deadline_alert = 'normal'
                    if r['due_date'] and r['status'] not in ('COMPLETED', 'done', 'CANCELLED', 'cancelled'):
                        try:
                            due_str = str(r['due_date']).strip()
                            time_part = str(r['due_time']).strip() if r['due_time'] and str(r['due_time']).strip() else '23:59:59'
                            full_str = f"{due_str} {time_part}" if ' ' not in due_str else due_str
                            due_dt = None
                            try:
                                due_dt = datetime.fromisoformat(full_str).replace(tzinfo=timezone.utc)
                            except Exception:
                                due_dt = datetime.strptime(full_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                            if due_dt:
                                if now_dt > due_dt:
                                    deadline_alert = 'overdue'
                                elif (due_dt - now_dt).total_seconds() <= 86400:
                                    deadline_alert = 'approaching'
                        except Exception:
                            pass
                    
                    done_count = sum(1 for a in assignees_list if a.get('status') == 'COMPLETED')
                    my_a = next((a for a in assignees_list if a['staff_id'] == user['id']), None)
                    task_dict = dict(r)
                    task_dict['assignees'] = assignees_list
                    task_dict['assignments'] = assignees_list
                    task_dict['my_status'] = my_a['status'] if my_a else r['status']
                    task_dict['myStatus'] = task_dict['my_status']
                    task_dict['completedCount'] = done_count
                    task_dict['totalAssignees'] = len(assignees_list)
                    task_dict['completionSummary'] = f"{done_count} / {len(assignees_list)} Completed" if assignees_list else ""
                    task_dict['progress'] = latest_update['progress_percent'] if latest_update else (100 if r['status'] in ('COMPLETED', 'done') else 0)
                    task_dict['latestUpdate'] = dict(latest_update) if latest_update else None
                    task_dict['deadlineAlert'] = deadline_alert
                    task_list.append(task_dict)
            return json_response(self, 200, task_list)

        if path == '/api/staff/tasks':
            user = self.require_user(('staff', 'punong_barangay', 'admin'))
            if not user:
                return
            with db() as connection:
                check_and_update_overdue_tasks(connection)
                rows = connection.execute("""
                    SELECT t.*, ta.status AS my_status, ta.assigned_at, ta.acknowledged_at,
                           ta.completed_at, ta.completion_notes, u.name AS creator_name
                    FROM tasks t
                    JOIN task_assignments ta ON ta.task_id = t.id
                    LEFT JOIN users u ON u.id = t.created_by_user_id
                    WHERE ta.staff_id = ?
                    ORDER BY t.id DESC
                """, (user['id'],)).fetchall()
                
                today_str = today()
                categorized = {'today': [], 'pending': [], 'inProgress': [], 'completed': [], 'overdue': [], 'all': []}
                for r in rows:
                    tid = r['id']
                    latest_up = connection.execute("""
                        SELECT progress_percent, update_text, created_at
                        FROM task_updates
                        WHERE task_id = ? AND staff_id = ? AND update_type = 'progress'
                        ORDER BY id DESC LIMIT 1
                    """, (tid, user['id'])).fetchone()
                    
                    t_item = dict(r)
                    t_item['progress'] = latest_up['progress_percent'] if latest_up else (100 if r['my_status'] in ('COMPLETED', 'done') else 0)
                    t_item['latestUpdateText'] = latest_up['update_text'] if latest_up else ''
                    
                    st = str(r['my_status']).upper()
                    if st in ('OVERDUE',) or str(r['status']).upper() == 'OVERDUE':
                        categorized['overdue'].append(t_item)
                    elif st in ('COMPLETED', 'DONE'):
                        categorized['completed'].append(t_item)
                    elif st in ('ACKNOWLEDGED', 'IN_PROGRESS', 'IN PROGRESS'):
                        categorized['inProgress'].append(t_item)
                    else:
                        categorized['pending'].append(t_item)
                    
                    if r['due_date'] and str(r['due_date']).strip() == today_str and st not in ('COMPLETED', 'DONE', 'CANCELLED'):
                        categorized['today'].append(t_item)
                    
                    categorized['all'].append(t_item)
            return json_response(self, 200, categorized)

        if path.startswith('/api/tasks/') and not any(path.endswith(sfx) for sfx in ('/acknowledge', '/updates', '/hold', '/complete')):
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            task_ref = path.removeprefix('/api/tasks/')
            with db() as connection:
                check_and_update_overdue_tasks(connection)
                t = connection.execute("SELECT t.*, u.name AS creator_name FROM tasks t LEFT JOIN users u ON u.id = t.created_by_user_id WHERE t.id = ? OR t.task_id = ?", (task_ref, task_ref)).fetchone()
                if not t:
                    return json_response(self, 404, {'error': 'Task not found'})
                tid = t['id']
                if user['role'] == 'staff':
                    is_assigned = connection.execute("SELECT 1 FROM task_assignments WHERE task_id = ? AND staff_id = ?", (tid, user['id'])).fetchone()
                    if not is_assigned:
                        return json_response(self, 403, {'error': 'Forbidden: You are not assigned to this task'})
                
                assignments = connection.execute("""
                    SELECT ta.*, u.name AS staff_name, u.position AS staff_position, u.account_status
                    FROM task_assignments ta
                    JOIN users u ON u.id = ta.staff_id
                    WHERE ta.task_id = ?
                """, (tid,)).fetchall()
                
                updates = connection.execute("""
                    SELECT tu.*, u.name AS actor_name, u.role AS actor_role, u.position AS actor_position
                    FROM task_updates tu
                    JOIN users u ON u.id = tu.staff_id
                    WHERE tu.task_id = ?
                    ORDER BY tu.id ASC
                """, (tid,)).fetchall()
            return json_response(self, 200, {
                'task': dict(t),
                'assignments': [dict(a) for a in assignments],
                'updates': [dict(u) for u in updates]
            })

        if path == '/api/admin/punong-barangay':
            user = self.require_user(('admin',))
            if not user:
                return
            with db() as connection:
                pb_user = connection.execute("SELECT id, username, name, role, email, account_status, position, department, contact_number, last_login_at, created_at FROM users WHERE role = 'punong_barangay'").fetchone()
                official_row = connection.execute("SELECT * FROM barangay_officials WHERE lower(position) LIKE '%punong%' OR lower(position) LIKE '%captain%' LIMIT 1").fetchone()
                setting_pb = get_setting(connection, 'punong_barangay', '')
            return json_response(self, 200, {
                'user': dict(pb_user) if pb_user else None,
                'official': dict(official_row) if official_row else None,
                'settingPunongBarangay': setting_pb
            })

        if path == '/api/admin/task-oversight':
            user = self.require_user(('admin',))
            if not user:
                return
            with db() as connection:
                check_and_update_overdue_tasks(connection)
                total_tasks = connection.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()['n']
                by_status = {row['status']: row['n'] for row in connection.execute("SELECT status, COUNT(*) AS n FROM tasks GROUP BY status").fetchall()}
                staff_loads = connection.execute("""
                    SELECT u.id, u.name, u.position, u.account_status,
                           COUNT(ta.id) AS total_assigned,
                           SUM(CASE WHEN upper(ta.status) IN ('PENDING', 'ASSIGNED') THEN 1 ELSE 0 END) AS pending,
                           SUM(CASE WHEN upper(ta.status) IN ('ACKNOWLEDGED', 'IN_PROGRESS') THEN 1 ELSE 0 END) AS in_progress,
                           SUM(CASE WHEN upper(ta.status) IN ('COMPLETED', 'DONE') THEN 1 ELSE 0 END) AS completed,
                           SUM(CASE WHEN upper(ta.status) = 'OVERDUE' THEN 1 ELSE 0 END) AS overdue
                    FROM users u
                    LEFT JOIN task_assignments ta ON ta.staff_id = u.id
                    WHERE u.role = 'staff'
                    GROUP BY u.id
                """).fetchall()
                audit_rows = connection.execute("""
                    SELECT l.*, u.name AS actor_name
                    FROM activity_logs l
                    LEFT JOIN users u ON u.id = l.actor_user_id
                    WHERE l.event_type LIKE 'task:%' OR l.event_type LIKE 'message:%'
                    ORDER BY l.id DESC LIMIT 25
                """).fetchall()
            return json_response(self, 200, {
                'totalTasks': total_tasks,
                'byStatus': by_status,
                'staffWorkload': [dict(sl) for sl in staff_loads],
                'recentLogs': [dict(al) for al in audit_rows]
            })

        if path == '/api/notifications':
            with db() as connection:
                rows = connection.execute('SELECT id, title, body, notification_type, is_read, created_at FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 50', (user['id'],)).fetchall()
                unread = connection.execute('SELECT COUNT(*) AS n FROM notifications WHERE user_id = ? AND is_read = 0', (user['id'],)).fetchone()['n']
            return json_response(self, 200, {'items': [dict(row) for row in rows], 'unread': unread})
        if path == '/api/users':
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            with db() as connection:
                rows = connection.execute('SELECT id, username, name, role, email, account_status, google_verified, last_login_at, created_at FROM users ORDER BY id').fetchall()
            return json_response(self, 200, [{**dict(row), 'roleLabel': role_label(row['role'])} for row in rows])
        if path == '/api/staff':
            if user['role'] not in ('admin', 'staff'):
                return json_response(self, 403, {'error': 'Forbidden'})
            with db() as connection:
                rows = connection.execute("SELECT id, name, role FROM users WHERE role IN ('admin', 'staff') AND account_status IN ('active', 'verified') ORDER BY name").fetchall()
            return json_response(self, 200, [dict(row) for row in rows])
        if path == '/api/households':
            if user['role'] not in ('admin', 'staff'):
                return json_response(self, 403, {'error': 'Forbidden'})
            with db() as connection:
                rows = connection.execute('''SELECT household_no, COUNT(*) AS member_count,
                    GROUP_CONCAT(first_name || ' ' || last_name, ', ') AS members,
                    GROUP_CONCAT(DISTINCT address) AS addresses
                    FROM residents WHERE archived_at IS NULL GROUP BY household_no ORDER BY household_no''').fetchall()
            return json_response(self, 200, [dict(row) for row in rows])
        if path == '/api/certificates':
            if user['role'] not in ('admin', 'staff'):
                return json_response(self, 403, {'error': 'Forbidden'})
            with db() as connection:
                rows = connection.execute('SELECT * FROM certificate_issuances ORDER BY id DESC').fetchall()
            return json_response(self, 200, [row_payload(row) for row in rows])
        if path == '/api/programs':
            query_values = parse_qs(urlparse(self.path).query)
            scope = query_values.get('scope', [''])[0]
            sql = 'SELECT p.*, u.name AS created_by_name FROM programs p LEFT JOIN users u ON u.id = p.created_by_user_id'
            conditions, params = [], []
            if user['role'] == 'resident':
                conditions.append("p.status IN ('Scheduled', 'Ongoing')")
            elif scope != 'all':
                conditions.append("p.status != 'Archived'")
            if scope == 'upcoming':
                conditions.append("p.event_date >= ?")
                params.append(today())
            if conditions:
                sql += ' WHERE ' + ' AND '.join(conditions)
            sql += ' ORDER BY p.event_date DESC, p.id DESC'
            with db() as connection:
                rows = connection.execute(sql, params).fetchall()
            return json_response(self, 200, [row_payload(row) for row in rows])
        if path == '/api/lgu-settings':
            with db() as connection:
                admin_row = connection.execute("SELECT name FROM users WHERE role = 'admin' AND account_status IN ('active', 'verified') ORDER BY id LIMIT 1").fetchone()
                staff_row = connection.execute("SELECT name FROM users WHERE role = 'staff' AND account_status IN ('active', 'verified') ORDER BY id LIMIT 1").fetchone()
                values = {
                    'barangayName': get_setting(connection, 'barangay_name', ''),
                    'municipality': get_setting(connection, 'municipality', ''),
                    'punongBarangay': get_setting(connection, 'punong_barangay', '') or (admin_row['name'] if admin_row else ''),
                    'barangaySecretary': get_setting(connection, 'barangay_secretary', '') or (staff_row['name'] if staff_row else ''),
                }
            return json_response(self, 200, values)
        if path == '/api/integrity-check':
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            return json_response(self, 200, run_integrity_check())
        if path == '/api/registrations':
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            with db() as connection:
                rows = connection.execute("SELECT id, username, name, email, resident_record_id, account_status, created_at FROM users WHERE role = 'resident' ORDER BY id DESC").fetchall()
            return json_response(self, 200, [dict(row) for row in rows])
        if path == '/api/resident-ids':
            if user['role'] not in ('admin', 'staff'):
                return json_response(self, 403, {'error': 'Forbidden'})
            with db() as connection:
                rows = connection.execute('''SELECT r.id, r.resident_id, r.first_name, r.middle_name, r.last_name,
                    r.resident_status, r.created_at, i.status AS issuance_status, i.issuance_number,
                    i.issued_at, i.issued_by, i.remarks FROM residents r
                    LEFT JOIN resident_id_issuances i ON i.id = (SELECT MAX(id) FROM resident_id_issuances WHERE resident_record_id = r.id)
                    WHERE r.archived_at IS NULL ORDER BY r.id DESC''').fetchall()
            return json_response(self, 200, [dict(row) for row in rows])
        if path == '/api/classification-summary':
            if user['role'] not in ('admin', 'staff'):
                return json_response(self, 403, {'error': 'Forbidden'})
            query_values = parse_qs(urlparse(self.path).query)
            selected = query_values.get('classification', [''])[0]
            search = query_values.get('search', [''])[0].strip().lower()
            filters = ['archived_at IS NULL']
            params = []
            if selected and selected in VALID_CLASSIFICATIONS:
                filters.append('classification = ?')
                params.append(selected)
            if search:
                filters.append("lower(resident_id || ' ' || first_name || ' ' || coalesce(middle_name, '') || ' ' || last_name || ' ' || address) LIKE ?")
                params.append(f'%{search}%')
            where = ' AND '.join(filters)
            with db() as connection:
                counts = connection.execute(f'SELECT classification, COUNT(*) AS total FROM residents WHERE {where} GROUP BY classification ORDER BY classification', params).fetchall()
                residents = connection.execute(f'SELECT id, resident_id, first_name, middle_name, last_name, birth_date, gender, address, classification, resident_status FROM residents WHERE {where} ORDER BY last_name, first_name', params).fetchall()
            return json_response(self, 200, {'counts': [dict(row) for row in counts], 'residents': [dict(row) for row in residents], 'total': sum(row['total'] for row in counts)})
        if path == '/api/resident-id-settings':
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            with db() as connection:
                settings = {row['setting_key']: row['setting_value'] for row in connection.execute("SELECT setting_key, setting_value FROM system_settings WHERE setting_key IN ('resident_id_prefix', 'resident_id_sequence_length')")}
            return json_response(self, 200, {'prefix': settings.get('resident_id_prefix', 'BRGY'), 'sequenceLength': int(settings.get('resident_id_sequence_length', '6'))})
        collections = {
            '/api/residents': ('residents', ('admin', 'staff')),
            '/api/requests': ('requests', ('admin', 'staff', 'resident')),
            '/api/classifications': ('classifications', ('admin', 'staff')),
            '/api/tasks': ('tasks', ('admin', 'staff')),
            '/api/announcements': ('announcements', ('admin', 'staff', 'resident')),
            '/api/activity-logs': ('activity_logs', ('admin',)),
        }
        if path not in collections:
            return self.serve_static()
        table, roles = collections[path]
        if user['role'] not in roles:
            return json_response(self, 403, {'error': 'Forbidden'})
        params = []
        if table == 'residents':
            query = 'SELECT * FROM residents WHERE archived_at IS NULL ORDER BY id DESC'
        elif table == 'requests':
            query = '''SELECT r.*, u.name AS owner_name FROM requests r LEFT JOIN users u ON u.id = r.owner_user_id'''
            if user['role'] == 'resident':
                query += ' WHERE r.owner_user_id = ?'
                params.append(user['id'])
            query += ' ORDER BY r.id DESC'
        elif table == 'announcements':
            query = 'SELECT * FROM announcements WHERE archived_at IS NULL ORDER BY id DESC'
        elif table == 'activity_logs':
            query = '''SELECT l.*, u.name AS actor_name FROM activity_logs l LEFT JOIN users u ON u.id = l.actor_user_id ORDER BY l.id DESC LIMIT 500'''
        elif table == 'tasks':
            query = 'SELECT t.*, u.name AS assigned_to_name FROM tasks t LEFT JOIN users u ON u.id = t.assigned_to_user_id ORDER BY t.id DESC'
            if user['role'] == 'staff':
                query = 'SELECT t.*, u.name AS assigned_to_name FROM tasks t LEFT JOIN users u ON u.id = t.assigned_to_user_id WHERE t.assigned_to_user_id = ? ORDER BY t.id DESC'
                params.append(user['id'])
        else:
            query = f'SELECT * FROM {table} ORDER BY id DESC'
        with db() as connection:
            rows = connection.execute(query, params).fetchall()
        return json_response(self, 200, [row_payload(row) for row in rows])

    # ------------------------------------------------------------------
    # POST routes
    # ------------------------------------------------------------------
    def do_POST(self):
        path = urlparse(self.path).path
        if path == '/api/login' and not allow_rate(self, 'login', config.LOGIN_RATE_LIMIT, 300):
            return
        if path == '/api/register' and not allow_rate(self, 'register', config.REGISTER_RATE_LIMIT, 3600):
            return
        if path.startswith('/api/') and path not in ('/api/login', '/api/register') and not allow_rate(self, 'api-write', config.WRITE_RATE_LIMIT):
            return
        if path == '/api/admin/staff':
            user = self.require_user(('admin',))
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            first = str(payload.get('firstName', '')).strip()
            last = str(payload.get('lastName', '')).strip()
            username = str(payload.get('username', '')).strip().lower()
            password = str(payload.get('password', '')).strip()
            if not first:
                return json_response(self, 400, {'error': 'First name is required.'})
            if not last:
                return json_response(self, 400, {'error': 'Last name is required.'})
            if not username or not username.replace('.', '').replace('-', '').replace('_', '').isalnum():
                return json_response(self, 400, {'error': 'A valid username is required.'})
            if len(password) < 8:
                return json_response(self, 400, {'error': 'Password must contain at least 8 characters.'})
            status = str(payload.get('status', 'active')).strip().lower()
            if status not in ('active', 'disabled', 'archived'):
                return json_response(self, 400, {'error': 'Invalid staff account status.'})
            created_at = now()
            name = ' '.join(part for part in (first, str(payload.get('middleName', '')).strip(), last, str(payload.get('suffix', '')).strip()) if part)
            with db() as connection:
                if connection.execute('SELECT 1 FROM users WHERE lower(username) = ?', (username,)).fetchone():
                    return json_response(self, 409, {'error': 'That username is already in use.'})
                handler_id = payload.get('handlerUserId')
                if handler_id not in (None, '', 0, '0'):
                    handler = connection.execute("SELECT id FROM users WHERE id = ? AND role = 'admin' AND account_status IN ('active', 'verified')", (handler_id,)).fetchone()
                    if not handler:
                        return json_response(self, 400, {'error': 'Assigned account handler is not an active administrator.'})
                    handler_id = handler['id']
                else:
                    handler_id = user['id']
                cursor = connection.execute('''
                    INSERT INTO users (username, password_hash, name, role, email, account_status, position, department, contact_number, created_at, handler_user_id, handler_assigned_at, handler_assigned_by, archived_at, archived_by, archive_reason, updated_at, updated_by, created_by)
                    VALUES (?, ?, ?, 'staff', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (username, password_hash(password), name, str(payload.get('email', '')).strip(), status, str(payload.get('position', '')).strip(), str(payload.get('department', '')).strip(), str(payload.get('contactNumber', '')).strip(), created_at, handler_id, created_at, user['id'], created_at if status == 'archived' else None, user['id'] if status == 'archived' else None, str(payload.get('archiveReason', '')).strip() if status == 'archived' else None, created_at, user['id'], user['id']))
                staff_id = cursor.lastrowid
                connection.execute('INSERT INTO staff_assignment_history (staff_id, previous_handler_id, new_handler_id, changed_by, changed_at, reason) VALUES (?, NULL, ?, ?, ?, ?)', (staff_id, handler_id, user['id'], created_at, 'Initial account handler assignment'))
                log_activity(connection, 'staff:created', user['id'], {'staffId': staff_id, 'name': name, 'handlerUserId': handler_id})
                created = connection.execute('SELECT * FROM users WHERE id = ?', (staff_id,)).fetchone()
                result = staff_payload(connection, created)
            return json_response(self, 201, {'ok': True, 'staff': result})
        if path.startswith('/api/admin/staff/') and path.endswith('/assign-handler'):
            user = self.require_user(('admin',))
            if not user:
                return
            staff_id = path.split('/')[4]
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            handler_id = payload.get('handlerUserId')
            reason = str(payload.get('reason', '')).strip()
            with db() as connection:
                staff = connection.execute("SELECT * FROM users WHERE id = ? AND role = 'staff'", (staff_id,)).fetchone()
                handler = connection.execute("SELECT id, name FROM users WHERE id = ? AND role = 'admin' AND account_status IN ('active', 'verified')", (handler_id,)).fetchone()
                if not staff:
                    return json_response(self, 404, {'error': 'Staff user not found.'})
                if not handler:
                    return json_response(self, 400, {'error': 'Assigned account handler is not an active administrator.'})
                changed_at = now()
                connection.execute('UPDATE users SET handler_user_id = ?, handler_assigned_at = ?, handler_assigned_by = ?, updated_at = ?, updated_by = ? WHERE id = ?', (handler['id'], changed_at, user['id'], changed_at, user['id'], staff_id))
                connection.execute('INSERT INTO staff_assignment_history (staff_id, previous_handler_id, new_handler_id, changed_by, changed_at, reason) VALUES (?, ?, ?, ?, ?, ?)', (staff_id, staff['handler_user_id'], handler['id'], user['id'], changed_at, reason or 'Handler reassigned'))
                log_activity(connection, 'staff:handler-changed', user['id'], {'staffId': int(staff_id), 'previousHandlerId': staff['handler_user_id'], 'newHandlerId': handler['id'], 'reason': reason})
                updated = connection.execute('SELECT * FROM users WHERE id = ?', (staff_id,)).fetchone()
                result = staff_payload(connection, updated)
            return json_response(self, 200, {'ok': True, 'staff': result})
        if path.startswith('/api/admin/staff/') and path.endswith(('/activate', '/deactivate', '/restore')):
            user = self.require_user(('admin',))
            if not user:
                return
            staff_id = path.split('/')[4]
            action = path.rsplit('/', 1)[-1]
            new_status = 'active' if action in ('activate', 'restore') else 'disabled'
            with db() as connection:
                staff = connection.execute("SELECT * FROM users WHERE id = ? AND role = 'staff'", (staff_id,)).fetchone()
                if not staff:
                    return json_response(self, 404, {'error': 'Staff user not found.'})
                changed_at = now()
                connection.execute('UPDATE users SET account_status = ?, archived_at = NULL, archived_by = NULL, archive_reason = NULL, updated_at = ?, updated_by = ? WHERE id = ?', (new_status, changed_at, user['id'], staff_id))
                for token, session in list(TOKENS.items()):
                    if session.get('user_id') == int(staff_id) and new_status != 'active':
                        TOKENS.pop(token, None)
                log_activity(connection, f'staff:{action}d' if action == 'deactivate' else f'staff:{action}d', user['id'], {'staffId': int(staff_id)})
                updated = connection.execute('SELECT * FROM users WHERE id = ?', (staff_id,)).fetchone()
                result = staff_payload(connection, updated)
            return json_response(self, 200, {'ok': True, 'staff': result})
        if path == '/api/admin/officials':
            user = self.require_user(('admin',))
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            fn = str(payload.get('firstName', '')).strip()
            ln = str(payload.get('lastName', '')).strip()
            if (not fn or not ln) and payload.get('name'):
                parts = str(payload.get('name')).strip().split()
                if parts:
                    fn = parts[0]
                    ln = ' '.join(parts[1:]) if len(parts) > 1 else 'Official'
            pos = str(payload.get('position', '')).strip()
            if not fn or not pos:
                return json_response(self, 400, {'error': 'Name/First name and position are required.'})
            if not ln:
                ln = 'Official'
            mn = str(payload.get('middleName', '')).strip()
            sfx = str(payload.get('suffix', '')).strip()
            bio = str(payload.get('committee', '') or payload.get('bio', '')).strip()
            contact = str(payload.get('contactInfo', '') or payload.get('term_years', '')).strip()
            is_vis = 1 if payload.get('isVisible', payload.get('is_visible', True)) else 0
            order_val = int(payload.get('displayOrder', payload.get('display_order', 0)) or 0)
            
            profile_img = ''
            img_b64 = payload.get('imageBase64') or payload.get('photo') or payload.get('data')
            if img_b64:
                try:
                    with db() as conn_img:
                        _, pub_path = process_and_save_image(img_b64, 'officials', conn_img, apply_wm=True)
                    profile_img = pub_path
                except ValueError as ve:
                    return json_response(self, 400, {'error': str(ve)})
                except Exception as ex:
                    logging.error('Official photo save failed: %s', ex)
                    return json_response(self, 500, {'error': 'Failed to save official photo.'})

            created = now()
            with db() as connection:
                if order_val <= 0:
                    max_row = connection.execute('SELECT COALESCE(MAX(display_order), 0) AS m FROM barangay_officials').fetchone()
                    order_val = (max_row['m'] if max_row else 0) + 1
                cursor = connection.execute('''
                    INSERT INTO barangay_officials (first_name, middle_name, last_name, suffix, position, bio, contact_info, profile_image, display_order, is_visible, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                ''', (fn, mn, ln, sfx, pos, bio, contact, profile_img, order_val, is_vis, created, created))
                new_id = cursor.lastrowid
                log_activity(connection, 'admin:official-added', user['id'], {'officialId': new_id, 'name': f"{fn} {ln}", 'position': pos})
                row = connection.execute('SELECT * FROM barangay_officials WHERE id = ?', (new_id,)).fetchone()
            res_dict = dict(row)
            res_dict['name'] = f"{row['first_name']} {row['last_name']}".strip()
            res_dict['photo_path'] = row['profile_image']
            res_dict['public_path'] = row['profile_image']
            res_dict['status'] = (row['status'] or 'active').lower()
            return json_response(self, 201, {'ok': True, 'official': res_dict})

        if path.startswith('/api/admin/officials/') and path.endswith('/archive'):
            user = self.require_user(('admin',))
            if not user:
                return
            off_id = path.split('/')[4]
            with db() as connection:
                connection.execute("UPDATE barangay_officials SET status = 'ARCHIVED', is_visible = 0, archived_at = ?, updated_at = ? WHERE id = ?", (now(), now(), off_id))
                log_activity(connection, 'admin:official-archived', user['id'], {'officialId': off_id})
                row = connection.execute('SELECT * FROM barangay_officials WHERE id = ?', (off_id,)).fetchone()
            off_dict = dict(row) if row else {}
            if row:
                off_dict['name'] = f"{row['first_name']} {row['last_name']}".strip()
                off_dict['photo_path'] = row['profile_image']
                off_dict['public_path'] = row['profile_image']
                off_dict['status'] = (row['status'] or 'archived').lower()
            return json_response(self, 200, {'ok': True, 'message': 'Official archived successfully.', 'official': off_dict})

        if path.startswith('/api/admin/officials/') and path.endswith('/restore'):
            user = self.require_user(('admin',))
            if not user:
                return
            off_id = path.split('/')[4]
            with db() as connection:
                connection.execute("UPDATE barangay_officials SET status = 'ACTIVE', is_visible = 1, archived_at = NULL, updated_at = ? WHERE id = ?", (now(), off_id))
                log_activity(connection, 'admin:official-restored', user['id'], {'officialId': off_id})
                row = connection.execute('SELECT * FROM barangay_officials WHERE id = ?', (off_id,)).fetchone()
            off_dict = dict(row) if row else {}
            if row:
                off_dict['name'] = f"{row['first_name']} {row['last_name']}".strip()
                off_dict['photo_path'] = row['profile_image']
                off_dict['public_path'] = row['profile_image']
                off_dict['status'] = (row['status'] or 'active').lower()
            return json_response(self, 200, {'ok': True, 'message': 'Official restored successfully.', 'official': off_dict})

        if path == '/api/admin/gallery':
            user = self.require_user(('admin',))
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            caption = str(payload.get('caption', '')).strip()
            if not caption:
                return json_response(self, 400, {'error': 'Caption is required for gallery items.'})
            desc = str(payload.get('description', '')).strip()
            is_vis = 1 if payload.get('isVisible', True) else 0
            order_val = int(payload.get('displayOrder', 0) or 0)
            img_b64 = payload.get('imageBase64') or payload.get('photo') or payload.get('data')
            if not img_b64:
                return json_response(self, 400, {'error': 'Image data is required.'})
            try:
                with db() as conn_img:
                    orig_path, pub_path = process_and_save_image(img_b64, 'gallery', conn_img, apply_wm=True)
            except ValueError as ve:
                return json_response(self, 400, {'error': str(ve)})
            except Exception as ex:
                logging.error('Gallery image save failed: %s', ex)
                return json_response(self, 500, {'error': 'Failed to save gallery photo.'})

            created = now()
            with db() as connection:
                if order_val <= 0:
                    max_row = connection.execute('SELECT COALESCE(MAX(display_order), 0) AS m FROM gallery_items').fetchone()
                    order_val = (max_row['m'] if max_row else 0) + 1
                cursor = connection.execute('''
                    INSERT INTO gallery_items (image_path, thumbnail_path, caption, description, display_order, is_visible, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
                ''', (pub_path, orig_path, caption, desc, order_val, is_vis, created, created))
                new_id = cursor.lastrowid
                log_activity(connection, 'admin:gallery-item-added', user['id'], {'galleryId': new_id, 'caption': caption})
                row = connection.execute('SELECT * FROM gallery_items WHERE id = ?', (new_id,)).fetchone()
            res_dict = dict(row)
            res_dict['public_path'] = row['image_path']
            res_dict['thumbnail'] = row['thumbnail_path'] or row['image_path']
            res_dict['status'] = (row['status'] or 'active').lower()
            return json_response(self, 201, {'ok': True, 'item': res_dict})

        if path.startswith('/api/admin/gallery/') and path.endswith('/archive'):
            user = self.require_user(('admin',))
            if not user:
                return
            g_id = path.split('/')[4]
            with db() as connection:
                connection.execute("UPDATE gallery_items SET status = 'ARCHIVED', is_visible = 0, archived_at = ?, updated_at = ? WHERE id = ?", (now(), now(), g_id))
                log_activity(connection, 'admin:gallery-item-archived', user['id'], {'galleryId': g_id})
                row = connection.execute('SELECT * FROM gallery_items WHERE id = ?', (g_id,)).fetchone()
            g_dict = dict(row) if row else {}
            if row:
                g_dict['public_path'] = row['image_path']
                g_dict['thumbnail'] = row['thumbnail_path'] or row['image_path']
                g_dict['status'] = (row['status'] or 'archived').lower()
            return json_response(self, 200, {'ok': True, 'message': 'Gallery item archived.', 'item': g_dict})

        if path.startswith('/api/admin/gallery/') and path.endswith('/restore'):
            user = self.require_user(('admin',))
            if not user:
                return
            g_id = path.split('/')[4]
            with db() as connection:
                connection.execute("UPDATE gallery_items SET status = 'ACTIVE', is_visible = 1, archived_at = NULL, updated_at = ? WHERE id = ?", (now(), g_id))
                log_activity(connection, 'admin:gallery-item-restored', user['id'], {'galleryId': g_id})
                row = connection.execute('SELECT * FROM gallery_items WHERE id = ?', (g_id,)).fetchone()
            g_dict = dict(row) if row else {}
            if row:
                g_dict['public_path'] = row['image_path']
                g_dict['thumbnail'] = row['thumbnail_path'] or row['image_path']
                g_dict['status'] = (row['status'] or 'active').lower()
            return json_response(self, 200, {'ok': True, 'message': 'Gallery item restored.', 'item': g_dict})

        if path == '/api/resident/profile-photo':
            user = self.require_user(('resident',))
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            img_b64 = payload.get('photo') or payload.get('data') or payload.get('imageBase64')
            if not img_b64:
                return json_response(self, 400, {'error': 'No image data provided'})
            try:
                rel_path, _ = process_and_save_image(img_b64, 'residents', None, apply_wm=False)
            except ValueError as ve:
                return json_response(self, 400, {'error': str(ve)})
            except Exception as ex:
                logging.error('Resident photo upload failed: %s', ex)
                return json_response(self, 500, {'error': 'Failed to save profile photo'})
            with db() as connection:
                connection.execute('UPDATE users SET profile_image = ? WHERE id = ?', (rel_path, user['id']))
                if user.get('resident_record_id'):
                    connection.execute('UPDATE residents SET profile_image = ?, updated_at = ? WHERE id = ?', (rel_path, now(), user['resident_record_id']))
                log_activity(connection, 'resident:profile-photo-updated', user['id'], {'path': rel_path})
            return json_response(self, 200, {'ok': True, 'profile_image': rel_path, 'profileImage': rel_path, 'message': 'Profile picture updated successfully.'})

        if path.startswith('/api/admin/residents/') and path.endswith('/profile-photo'):
            user = self.require_user(('admin', 'staff'))
            if not user:
                return
            res_id = path.split('/')[4]
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            img_b64 = payload.get('photo') or payload.get('data') or payload.get('imageBase64')
            if not img_b64:
                return json_response(self, 400, {'error': 'No image data provided'})
            try:
                rel_path, _ = process_and_save_image(img_b64, 'residents', None, apply_wm=False)
            except ValueError as ve:
                return json_response(self, 400, {'error': str(ve)})
            except Exception as ex:
                logging.error('Admin resident photo upload failed: %s', ex)
                return json_response(self, 500, {'error': 'Failed to save profile photo'})
            with db() as connection:
                connection.execute('UPDATE residents SET profile_image = ?, updated_at = ? WHERE id = ?', (rel_path, now(), res_id))
                connection.execute('UPDATE users SET profile_image = ? WHERE resident_record_id = ?', (rel_path, res_id))
                log_activity(connection, 'admin:resident-photo-updated', user['id'], {'residentId': res_id, 'path': rel_path})
            return json_response(self, 200, {'ok': True, 'profile_image': rel_path, 'profileImage': rel_path, 'message': 'Resident profile picture updated successfully.'})

        if path == '/api/admin/hero-image':
            user = self.require_user(('admin',))
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})

            # Case 1: Reset to default hero image
            if payload.get('reset'):
                updated = now()
                with db() as connection:
                    set_setting(connection, 'hero_image_path', './assets/hero1.jpg')
                    set_setting(connection, 'hero_image_protected_path', '')
                    set_setting(connection, 'hero_image_focal_position', 'center')
                    set_setting(connection, 'hero_image_updated_at', updated)
                    log_activity(connection, 'admin:hero-image-reset', user['id'], {})
                return json_response(self, 200, {
                    'ok': True,
                    'message': 'Hero image reset to default.',
                    'heroImage': './assets/hero1.jpg',
                    'heroImageFocalPosition': 'center',
                    'heroImageUpdatedAt': updated
                })

            # Case 2: Update focal position only
            if 'focalPosition' in payload and not payload.get('data'):
                focal = str(payload.get('focalPosition', 'center')).strip().lower()
                if focal not in ('center', 'top', 'bottom'):
                    focal = 'center'
                updated = now()
                with db() as connection:
                    set_setting(connection, 'hero_image_focal_position', focal)
                    set_setting(connection, 'hero_image_updated_at', updated)
                    hero_image = get_setting(connection, 'hero_image_path', './assets/hero1.jpg')
                    log_activity(connection, 'admin:hero-focal-updated', user['id'], {'focalPosition': focal})
                return json_response(self, 200, {
                    'ok': True,
                    'message': 'Hero focal position updated.',
                    'heroImage': hero_image,
                    'heroImageFocalPosition': focal,
                    'heroImageUpdatedAt': updated
                })

            # Case 3: Image upload via Base64 data URL
            data_uri = str(payload.get('data', '')).strip()
            if not data_uri:
                return json_response(self, 400, {'error': 'No image data provided'})

            if ',' in data_uri:
                header, base64_data = data_uri.split(',', 1)
            else:
                header, base64_data = '', data_uri

            mime = ''
            if header.startswith('data:'):
                mime = header.split(';')[0].removeprefix('data:').lower()

            allowed_types = {
                'image/jpeg': '.jpg',
                'image/jpg': '.jpg',
                'image/png': '.png',
                'image/webp': '.webp'
            }

            ext = allowed_types.get(mime)
            if not ext:
                orig_filename = str(payload.get('filename', '')).lower()
                for e in ('.jpg', '.jpeg', '.png', '.webp'):
                    if orig_filename.endswith(e):
                        ext = '.jpg' if e == '.jpeg' else e
                        break

            import base64
            try:
                raw_bytes = base64.b64decode(base64_data)
            except Exception:
                return json_response(self, 400, {'error': 'Corrupt or invalid base64 image data.'})

            if len(raw_bytes) > 5 * 1024 * 1024:
                return json_response(self, 400, {'error': 'Image file exceeds maximum allowed size of 5MB.'})

            if len(raw_bytes) < 100:
                return json_response(self, 400, {'error': 'Image file is empty or too small.'})

            # Validate header magic bytes
            is_valid_image = False
            if raw_bytes.startswith(b'\xff\xd8\xff'):
                is_valid_image = True
                ext = '.jpg'
            elif raw_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
                is_valid_image = True
                ext = '.png'
            elif raw_bytes.startswith(b'RIFF') and b'WEBP' in raw_bytes[:16]:
                is_valid_image = True
                ext = '.webp'

            if not is_valid_image:
                return json_response(self, 400, {'error': 'Invalid image format. Must be a valid JPG, PNG, or WebP file.'})

            upload_dir = ROOT / 'uploads' / 'branding' / 'hero'
            upload_dir.mkdir(parents=True, exist_ok=True)

            timestamp_str = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
            token_hex = secrets.token_hex(4)
            filename = f'hero_{timestamp_str}_{token_hex}{ext}'
            file_path = upload_dir / filename
            file_path.write_bytes(raw_bytes)

            relative_path = f'./uploads/branding/hero/{filename}'
            focal = str(payload.get('focalPosition', 'center')).strip().lower()
            if focal not in ('center', 'top', 'bottom'):
                focal = 'center'
            updated = now()

            # ── Generate server-side watermarked version ───────────────────
            protected_relative_path = relative_path  # fallback: use original
            try:
                with db() as conn_wm:
                    ps = get_protection_settings(conn_wm)
                if ps.get('watermarkEnabled', True) and PILLOW_AVAILABLE:
                    wm_bytes = apply_watermark(
                        raw_bytes,
                        watermark_text=ps['watermarkText'],
                        opacity=ps['watermarkOpacity'],
                        position=ps['watermarkPosition'],
                    )
                    protected_dir = ROOT / 'uploads' / 'protected' / 'hero'
                    protected_dir.mkdir(parents=True, exist_ok=True)
                    protected_filename = f'hero_{timestamp_str}_{token_hex}_wm{ext}'
                    protected_path = protected_dir / protected_filename
                    protected_path.write_bytes(wm_bytes)
                    protected_relative_path = f'./uploads/protected/hero/{protected_filename}'
            except Exception as wm_err:
                logging.error('Watermark generation failed for hero upload: %s', wm_err)
                # Non-fatal: proceed with original as fallback

            with db() as connection:
                set_setting(connection, 'hero_image_path', relative_path)
                set_setting(connection, 'hero_image_protected_path', protected_relative_path)
                set_setting(connection, 'hero_image_focal_position', focal)
                set_setting(connection, 'hero_image_updated_at', updated)
                log_activity(connection, 'admin:hero-image-uploaded', user['id'], {
                    'filename': filename,
                    'size': len(raw_bytes),
                    'focalPosition': focal,
                    'watermarked': protected_relative_path != relative_path,
                })

            return json_response(self, 200, {
                'ok': True,
                'message': 'Hero image updated successfully.',
                'heroImage': protected_relative_path,  # public gets watermarked version
                'heroImageOriginal': relative_path,
                'heroImageFocalPosition': focal,
                'heroImageUpdatedAt': updated
            })
        if path == '/api/google/start':
            if not google_configured():
                return json_response(self, 503, {'error': 'Google verification is not configured on this server'})
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid registration details'})
            required = ('residentId', 'lastName', 'firstName', 'birthDate', 'classification', 'username', 'password', 'confirmPassword', 'email')
            if any(not str(payload.get(key, '')).strip() for key in required):
                return json_response(self, 400, {'error': 'Please complete all required registration fields'})
            if payload['password'] != payload['confirmPassword'] or len(payload['password']) < 8:
                return json_response(self, 400, {'error': 'Passwords must match and contain at least 8 characters'})
            username = str(payload['username']).strip().lower()
            if not username.replace('.', '').replace('-', '').replace('_', '').isalnum():
                return json_response(self, 400, {'error': 'Username contains invalid characters'})
            with db() as connection:
                if connection.execute('SELECT 1 FROM users WHERE lower(username) = ?', (username,)).fetchone():
                    return json_response(self, 409, {'error': 'That username is already registered'})
                classification_val = str(payload['classification']).strip().lower()
                resident = connection.execute(
                    '''SELECT * FROM residents 
                       WHERE resident_id = ? 
                         AND lower(trim(last_name)) = ? 
                         AND lower(trim(first_name)) = ? 
                         AND trim(birth_date) = ? 
                         AND lower(trim(classification)) = ? 
                         AND archived_at IS NULL''',
                    (str(payload['residentId']).strip(), str(payload['lastName']).strip().lower(), str(payload['firstName']).strip().lower(), str(payload['birthDate']).strip(), classification_val),
                ).fetchone()
                if not resident:
                    return json_response(self, 403, {'error': 'Resident verification failed: The provided details (including classification) do not match our official barangay resident records.'})
                if connection.execute('SELECT 1 FROM users WHERE resident_record_id = ?', (resident['id'],)).fetchone():
                    return json_response(self, 409, {'error': 'An online account already exists for this resident. Please sign in instead.'})
            state = secrets.token_urlsafe(32)
            GOOGLE_STATES[state] = {'details': payload, 'expires_at': time.time() + GOOGLE_STATE_TTL_SECONDS}
            params = {'client_id': os.environ['GOOGLE_CLIENT_ID'], 'redirect_uri': google_redirect_uri(self), 'response_type': 'code', 'scope': 'openid email profile', 'state': state, 'access_type': 'online', 'prompt': 'select_account'}
            return json_response(self, 200, {'authorization_url': 'https://accounts.google.com/o/oauth2/v2/auth?' + urlencode(params)})

        if path == '/api/register':
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid registration details'})

            required = ('residentId', 'lastName', 'firstName', 'birthDate', 'classification', 'username', 'password', 'confirmPassword')
            if any(not str(payload.get(key, '')).strip() for key in required):
                return json_response(self, 400, {'error': 'Please complete all required verification and account fields.'})

            password = str(payload.get('password', ''))
            confirm_password = str(payload.get('confirmPassword', ''))
            if password != confirm_password or len(password) < 8:
                return json_response(self, 400, {'error': 'Passwords must match and contain at least 8 characters.'})

            username = str(payload.get('username', '')).strip().lower()
            if not username.replace('.', '').replace('-', '').replace('_', '').isalnum():
                return json_response(self, 400, {'error': 'Username contains invalid characters.'})
            email_val = str(payload.get('email', '')).strip().lower()
            if email_val and not email_is_valid(email_val):
                return json_response(self, 400, {'error': 'Please provide a valid email address.'})

            classification_input = str(payload.get('classification', '')).strip()
            if classification_input not in VALID_CLASSIFICATIONS:
                return json_response(self, 400, {'error': 'Invalid classification selected for verification.'})

            with db() as connection:
                # 1. Unique username check
                if connection.execute('SELECT 1 FROM users WHERE lower(username) = ?', (username,)).fetchone():
                    return json_response(self, 409, {'error': 'That username is already registered. Please choose another username.'})

                # 2. Strict verification against SQLite official resident record
                resident = connection.execute('''
                    SELECT * FROM residents 
                    WHERE resident_id = ? 
                      AND lower(trim(last_name)) = ? 
                      AND lower(trim(first_name)) = ? 
                      AND trim(birth_date) = ? 
                      AND lower(trim(classification)) = ? 
                      AND archived_at IS NULL
                ''', (
                    str(payload['residentId']).strip(),
                    str(payload['lastName']).strip().lower(),
                    str(payload['firstName']).strip().lower(),
                    str(payload['birthDate']).strip(),
                    classification_input.lower()
                )).fetchone()

                if not resident:
                    return json_response(self, 403, {
                        'error': 'Verification failed: The provided Resident ID, Name, Birth Date, and Classification do not match our official barangay resident records.'
                    })

                # 3. Active status check
                if resident['resident_status'] == 'ARCHIVED' or resident['archived_at']:
                    return json_response(self, 403, {
                        'error': 'This resident record is archived or inactive. Please contact the Barangay Office.'
                    })

                # 4. Prevent duplicate accounts for the same resident
                if connection.execute('SELECT 1 FROM users WHERE resident_record_id = ?', (resident['id'],)).fetchone():
                    return json_response(self, 409, {
                        'error': 'An online account already exists for this resident. Please sign in instead.'
                    })
                if email_val and connection.execute('SELECT 1 FROM users WHERE lower(email) = ?', (email_val,)).fetchone():
                    return json_response(self, 409, {'error': 'That email address is already in use. Please use another email address.'})

                # 5. Create user record - FORCE role to 'resident' and link resident_record_id.
                # Account is immediately active; no email verification required.
                created_time = now()
                full_official_name = f"{resident['first_name']} {resident['last_name']}".strip()
                pw_hash = password_hash(password)

                cursor = connection.execute('''
                    INSERT INTO users (username, password_hash, name, role, email, email_verified,
                        email_verified_at, resident_record_id, account_status, google_verified, created_at)
                    VALUES (?, ?, ?, 'resident', ?, 1, ?, ?, 'active', 0, ?)
                ''', (username, pw_hash, full_official_name, email_val or None, created_time, resident['id'], created_time))
                new_user_id = cursor.lastrowid

                # Link owner_user_id on resident record (does NOT modify classification)
                connection.execute('UPDATE residents SET owner_user_id = ?, updated_at = ? WHERE id = ?', (new_user_id, created_time, resident['id']))

                # Audit log
                log_activity(connection, 'user:registered', new_user_id, {
                    'username': username,
                    'residentId': resident['resident_id'],
                    'classification': resident['classification']
                })

            return json_response(self, 201, {
                'ok': True,
                'resident_id': resident['resident_id'],
                'classification': resident['classification'],
                'message': 'Account created successfully. You can now sign in.'
            })


        # --- PUNONG BARANGAY & MESSAGING & TASK ROUTES (POST) ---
        if path in ('/api/messages', '/api/punong-barangay/messages'):
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            
            body = str(payload.get('body', '')).strip()
            if not body:
                return json_response(self, 400, {'error': 'Message body cannot be empty.'})
            
            msg_type = str(payload.get('messageType') or payload.get('message_type') or 'NORMAL').strip().upper()
            if msg_type not in ('NORMAL', 'IMPORTANT', 'URGENT', 'TASK', 'ANNOUNCEMENT', 'TASK_UPDATE', 'TASK_COMPLETED', 'CLARIFICATION'):
                msg_type = 'NORMAL'
            
            # Security: Only Punong Barangay or Admin can issue official TASK commands or ANNOUNCEMENT broadcasts
            if msg_type in ('TASK', 'ANNOUNCEMENT') and user['role'] not in ('punong_barangay', 'admin'):
                return json_response(self, 403, {'error': 'Forbidden: Only Punong Barangay or Admin can issue official tasks or announcements.'})
            
            subject = str(payload.get('subject') or payload.get('title') or '').strip()
            is_imp = 1 if (msg_type in ('IMPORTANT', 'URGENT') or payload.get('isImportant') or payload.get('is_important')) else 0
            task_id = payload.get('taskId') or payload.get('task_id')
            recipient_ids = payload.get('recipientIds') or payload.get('recipients') or payload.get('recipient_ids') or payload.get('recipient_id') or payload.get('recipientId')
            
            with db() as connection:
                if not recipient_ids or recipient_ids in ('pb', 'captain', 'punong_barangay') or payload.get('recipient_type') in ('PB', 'punong_barangay'):
                    pb_user = connection.execute("SELECT id FROM users WHERE role = 'punong_barangay' LIMIT 1").fetchone()
                    if pb_user:
                        target_ids = [pb_user['id']]
                    else:
                        admin_user = connection.execute("SELECT id FROM users WHERE role = 'admin' LIMIT 1").fetchone()
                        target_ids = [admin_user['id']] if admin_user else []
                elif recipient_ids == 'all' or recipient_ids == ['all'] or payload.get('recipient_type') == 'ALL':
                    if user['role'] not in ('punong_barangay', 'admin'):
                        return json_response(self, 403, {'error': 'Forbidden: Only Punong Barangay or Admin can broadcast to all staff.'})
                    staff_rows = connection.execute("SELECT id, name FROM users WHERE role = 'staff' AND account_status = 'active'").fetchall()
                    target_ids = [r['id'] for r in staff_rows]
                    conv_key = f"broadcast_{secrets.token_hex(6)}"
                    title = "All Staff Announcement" if msg_type == 'ANNOUNCEMENT' else "All Staff Broadcast"
                    cursor = connection.execute(
                        "INSERT INTO conversations (conversation_key, title, type, created_by, created_at, updated_at) VALUES (?, ?, 'group', ?, ?, ?)",
                        (conv_key, title, user['id'], now(), now())
                    )
                    conv_id = cursor.lastrowid
                else:
                    if not isinstance(recipient_ids, list):
                        target_ids = [int(recipient_ids)] if str(recipient_ids).isdigit() else []
                    else:
                        target_ids = [int(i) for i in recipient_ids if str(i).isdigit()]
                    
                    if not target_ids:
                        return json_response(self, 400, {'error': 'At least one recipient must be selected.'})
                    
                    if len(target_ids) == 1:
                        target_id = target_ids[0]
                        min_id, max_id = min(user['id'], target_id), max(user['id'], target_id)
                        conv_key = f"direct_{min_id}_{max_id}"
                        existing_conv = connection.execute("SELECT id FROM conversations WHERE conversation_key = ?", (conv_key,)).fetchone()
                        if existing_conv:
                            conv_id = existing_conv['id']
                            connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now(), conv_id))
                        else:
                            other_user = connection.execute("SELECT name FROM users WHERE id = ?", (target_id,)).fetchone()
                            conv_title = f"Direct: {other_user['name']}" if other_user else "Direct Message"
                            cursor = connection.execute(
                                "INSERT INTO conversations (conversation_key, title, type, created_by, created_at, updated_at) VALUES (?, ?, 'direct', ?, ?, ?)",
                                (conv_key, conv_title, user['id'], now(), now())
                            )
                            conv_id = cursor.lastrowid
                    else:
                        conv_key = f"group_{secrets.token_hex(6)}"
                        cursor = connection.execute(
                            "INSERT INTO conversations (conversation_key, title, type, created_by, created_at, updated_at) VALUES (?, ?, 'group', ?, ?, ?)",
                            (conv_key, f"Group Message ({len(target_ids)} staff)", user['id'], now(), now())
                        )
                        conv_id = cursor.lastrowid
                
                db_msg_type = 'NORMAL' if msg_type in ('CLARIFICATION', 'TASK_UPDATE', 'TASK_COMPLETED') else msg_type
                save_subject = subject
                if msg_type in ('CLARIFICATION', 'TASK_UPDATE', 'TASK_COMPLETED') and not (save_subject and save_subject.startswith(f"[{msg_type}]")):
                    save_subject = f"[{msg_type}] {save_subject}".strip()
                
                cur_msg = connection.execute(
                    "INSERT INTO messages (conversation_id, sender_id, message_type, subject, body, is_important, task_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (conv_id, user['id'], db_msg_type, save_subject, body, is_imp, task_id, now(), now())
                )
                msg_id = cur_msg.lastrowid
                
                for tid in target_ids:
                    connection.execute(
                        "INSERT INTO message_recipients (message_id, conversation_id, recipient_id, is_read) VALUES (?, ?, ?, 0)",
                        (msg_id, conv_id, tid)
                    )
                    connection.execute(
                        "INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id) VALUES (?, ?, ?, ?, 0, ?, ?, 'message', ?)",
                        (tid, f"[{msg_type}] Message from {user['name']}", body[:120], 'message', now(), str(conv_id), user['id'])
                    )
                
                log_activity(connection, 'message:sent', user['id'], {
                    'conversationId': conv_id,
                    'messageId': msg_id,
                    'messageType': msg_type,
                    'recipientsCount': len(target_ids),
                    'isImportant': bool(is_imp)
                })
            return json_response(self, 201, {'ok': True, 'conversationId': conv_id, 'messageId': msg_id, 'message': 'Message sent successfully.'})

        if path.startswith('/api/messages/') and path.endswith('/reply'):
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            conv_id_str = path.split('/')[3]
            if not conv_id_str.isdigit():
                return json_response(self, 400, {'error': 'Invalid conversation ID'})
            conv_id = int(conv_id_str)
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            
            body = str(payload.get('body', '')).strip()
            if not body:
                return json_response(self, 400, {'error': 'Reply body cannot be empty.'})
            
            msg_type = str(payload.get('messageType', 'NORMAL')).strip().upper()
            if msg_type not in ('NORMAL', 'IMPORTANT', 'URGENT', 'TASK', 'ANNOUNCEMENT', 'CLARIFICATION'):
                msg_type = 'NORMAL'
            if msg_type in ('TASK', 'ANNOUNCEMENT') and user['role'] not in ('punong_barangay', 'admin'):
                return json_response(self, 403, {'error': 'Forbidden: Only Punong Barangay or Admin can issue tasks or announcements.'})
            is_imp = 1 if (msg_type in ('IMPORTANT', 'URGENT') or payload.get('isImportant')) else 0
            
            with db() as connection:
                conv = connection.execute("SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
                if not conv:
                    return json_response(self, 404, {'error': 'Conversation not found'})
                
                # Check participation or admin oversight
                is_part = connection.execute("""
                    SELECT 1 FROM conversations c
                    LEFT JOIN messages m ON m.conversation_id = c.id
                    LEFT JOIN message_recipients mr ON mr.conversation_id = c.id
                    WHERE c.id = ? AND (c.created_by = ? OR m.sender_id = ? OR mr.recipient_id = ?)
                """, (conv_id, user['id'], user['id'], user['id'])).fetchone()
                if not is_part and user['role'] != 'admin':
                    return json_response(self, 403, {'error': 'Forbidden: You are not a participant in this conversation'})
                
                created_ts = now()
                db_msg_type = 'NORMAL' if msg_type in ('CLARIFICATION', 'TASK_UPDATE', 'TASK_COMPLETED') else msg_type
                reply_subj = f"[{msg_type}]" if msg_type in ('CLARIFICATION', 'TASK_UPDATE', 'TASK_COMPLETED') else ''
                cur_m = connection.execute(
                    "INSERT INTO messages (conversation_id, sender_id, message_type, subject, body, is_important, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (conv_id, user['id'], db_msg_type, reply_subj, body, is_imp, created_ts, created_ts)
                )
                msg_id = cur_m.lastrowid
                connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (created_ts, conv_id))
                
                # Find all other participants in conversation
                other_participants = connection.execute("""
                    SELECT DISTINCT u.id FROM users u
                    WHERE u.id != ? AND (
                        u.id = ?
                        OR u.id IN (SELECT sender_id FROM messages WHERE conversation_id = ?)
                        OR u.id IN (SELECT recipient_id FROM message_recipients WHERE conversation_id = ?)
                    )
                """, (user['id'], conv['created_by'], conv_id, conv_id)).fetchall()
                
                for op in other_participants:
                    connection.execute(
                        "INSERT INTO message_recipients (message_id, conversation_id, recipient_id, is_read) VALUES (?, ?, ?, 0)",
                        (msg_id, conv_id, op['id'])
                    )
                    connection.execute(
                        "INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id) VALUES (?, ?, ?, ?, 0, ?, ?, 'message', ?)",
                        (op['id'], f"Reply from {user['name']}", body[:120], 'message', created_ts, str(conv_id), user['id'])
                    )
                
                log_activity(connection, 'message:replied', user['id'], {'conversationId': conv_id, 'messageId': msg_id})
            return json_response(self, 201, {'ok': True, 'conversationId': conv_id, 'messageId': msg_id, 'message': 'Reply sent successfully.'})

        if path == '/api/messages/mark-all-read':
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            with db() as connection:
                connection.execute("UPDATE message_recipients SET is_read = 1, read_at = ? WHERE recipient_id = ? AND is_read = 0", (now(), user['id']))
                connection.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0 AND (notification_type IN ('message', 'task') OR related_type IN ('message', 'task'))", (user['id'],))
            return json_response(self, 200, {'ok': True, 'message': 'All messages marked as read.'})

        if path == '/api/notifications/mark-all-read' or path == '/api/notifications/read':
            user = self.require_user()
            if not user:
                return
            with db() as connection:
                connection.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0", (user['id'],))
            return json_response(self, 200, {'ok': True, 'message': 'All notifications marked as read.'})

        if path.startswith('/api/messages/') and path.endswith('/read'):
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            conv_id_str = path.split('/')[3]
            if conv_id_str.isdigit():
                conv_id = int(conv_id_str)
                with db() as connection:
                    connection.execute("UPDATE message_recipients SET is_read = 1, read_at = ? WHERE conversation_id = ? AND recipient_id = ? AND is_read = 0", (now(), conv_id, user['id']))
            return json_response(self, 200, {'ok': True})

        if path.startswith('/api/messages/') and path.endswith('/important'):
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            msg_id_str = path.split('/')[3]
            if not msg_id_str.isdigit():
                return json_response(self, 400, {'error': 'Invalid message ID'})
            with db() as connection:
                row = connection.execute("SELECT is_important FROM messages WHERE id = ?", (int(msg_id_str),)).fetchone()
                if row:
                    new_val = 0 if row['is_important'] else 1
                    connection.execute("UPDATE messages SET is_important = ?, updated_at = ? WHERE id = ?", (new_val, now(), int(msg_id_str)))
                    return json_response(self, 200, {'ok': True, 'isImportant': bool(new_val)})
            return json_response(self, 404, {'error': 'Message not found'})

        if path.startswith('/api/messages/') and path.endswith('/archive'):
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            conv_id_str = path.split('/')[3]
            if not conv_id_str.isdigit():
                return json_response(self, 400, {'error': 'Invalid conversation ID'})
            with db() as connection:
                connection.execute("UPDATE message_recipients SET is_archived = 1, archived_at = ? WHERE conversation_id = ? AND recipient_id = ?", (now(), int(conv_id_str), user['id']))
            return json_response(self, 200, {'ok': True, 'message': 'Conversation archived.'})

        if path in ('/api/tasks', '/api/punong-barangay/tasks'):
            user = self.require_user(('punong_barangay', 'admin'))
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            
            title = str(payload.get('title', '')).strip()
            desc = str(payload.get('description', '')).strip()
            if not title:
                return json_response(self, 400, {'error': 'Task title is required.'})
            if not desc:
                return json_response(self, 400, {'error': 'Task description is required.'})
            
            staff_ids = payload.get('assignedStaffIds') or payload.get('assignedTo') or payload.get('staffIds') or payload.get('assigned_to') or payload.get('assigned_staff_ids')
            if not isinstance(staff_ids, list):
                target_staff = [int(staff_ids)] if str(staff_ids).isdigit() else []
            else:
                target_staff = [int(i) for i in staff_ids if str(i).isdigit()]
            
            if not target_staff:
                return json_response(self, 400, {'error': 'At least one staff member must be assigned.'})
            
            priority = str(payload.get('priority', 'NORMAL')).strip().upper()
            if priority not in ('LOW', 'NORMAL', 'HIGH', 'URGENT'):
                priority = 'NORMAL'
            
            due_date = str(payload.get('dueDate') or payload.get('due_date') or '').strip()
            due_time = str(payload.get('dueTime') or payload.get('due_time') or '').strip()
            task_type = str(payload.get('taskType') or payload.get('task_type') or 'General Assignment').strip()
            related_record = str(payload.get('relatedRecord') or payload.get('related_record') or '').strip()
            
            created_ts = now()
            year = datetime.now(timezone.utc).year
            
            with db() as connection:
                # Generate unique Task ID
                cursor_seq = connection.execute("SELECT COUNT(*) AS n FROM tasks WHERE substr(created_at, 1, 4) = ?", (str(year),))
                seq_no = (cursor_seq.fetchone()['n'] or 0) + 1
                task_id = f"TASK-{year}-{seq_no:04d}-{secrets.token_hex(2).upper()}"
                
                # Primary assignee for legacy compatibility
                primary_assignee = target_staff[0]
                
                cursor = connection.execute("""
                    INSERT INTO tasks (task_id, title, description, task_type, priority, due_date, due_time,
                                       status, created_by_user_id, assigned_to_user_id, related_record,
                                       payload_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?, ?, ?, ?, ?)
                """, (task_id, title, desc, task_type, priority, due_date, due_time,
                      user['id'], primary_assignee, related_record,
                      json.dumps({'title': title, 'description': desc, 'priority': priority, 'assignedTo': target_staff}),
                      created_ts, created_ts))
                new_task_id = cursor.lastrowid
                
                for sid in target_staff:
                    connection.execute("""
                        INSERT OR REPLACE INTO task_assignments (task_id, staff_id, status, assigned_at)
                        VALUES (?, ?, 'PENDING', ?)
                    """, (new_task_id, sid, created_ts))
                    
                    connection.execute("""
                        INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id)
                        VALUES (?, ?, ?, 'task', 0, ?, ?, 'task', ?)
                    """, (sid, f"New Task: {title}", f"Priority: {priority} · Due: {due_date or 'No deadline'}", created_ts, str(new_task_id), user['id']))

                    # Also insert into conversation between PB and assigned staff
                    min_id, max_id = min(user['id'], sid), max(user['id'], sid)
                    conv_key = f"direct_{min_id}_{max_id}"
                    existing_conv = connection.execute("SELECT id FROM conversations WHERE conversation_key = ?", (conv_key,)).fetchone()
                    if existing_conv:
                        t_conv_id = existing_conv['id']
                        connection.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (created_ts, t_conv_id))
                    else:
                        other_u = connection.execute("SELECT name FROM users WHERE id = ?", (sid,)).fetchone()
                        t_title = f"Direct: {other_u['name']}" if other_u else "Direct Message"
                        c_cur = connection.execute("INSERT INTO conversations (conversation_key, title, type, created_by, created_at, updated_at) VALUES (?, ?, 'direct', ?, ?, ?)", (conv_key, t_title, user['id'], created_ts, created_ts))
                        t_conv_id = c_cur.lastrowid
                    
                    m_cur = connection.execute("""
                        INSERT INTO messages (conversation_id, sender_id, message_type, subject, body, is_important, task_id, created_at, updated_at)
                        VALUES (?, ?, 'TASK', ?, ?, ?, ?, ?, ?)
                    """, (t_conv_id, user['id'], f"OFFICIAL COMMAND: {title}", desc, 1 if priority in ('HIGH', 'URGENT') else 0, new_task_id, created_ts, created_ts))
                    new_m_id = m_cur.lastrowid
                    connection.execute("INSERT INTO message_recipients (message_id, conversation_id, recipient_id, is_read) VALUES (?, ?, ?, 0)", (new_m_id, t_conv_id, sid))

                log_activity(connection, 'task:created', user['id'], {
                    'taskId': task_id,
                    'recordId': new_task_id,
                    'title': title,
                    'priority': priority,
                    'assignees': target_staff
                })
                log_activity(connection, 'task:assigned', user['id'], {
                    'taskId': task_id,
                    'assigneesCount': len(target_staff)
                })
                
                task_row = connection.execute("SELECT * FROM tasks WHERE id = ?", (new_task_id,)).fetchone()
            return json_response(self, 201, {'ok': True, 'id': new_task_id, 'task_id': new_task_id, 'taskId': task_id, 'task': dict(task_row), 'message': 'Task created and assigned successfully.'})

        if path.startswith('/api/tasks/') and path.endswith('/acknowledge'):
            user = self.require_user(('staff', 'punong_barangay', 'admin'))
            if not user:
                return
            task_ref = path.split('/')[3]
            with db() as connection:
                t = connection.execute("SELECT * FROM tasks WHERE id = ? OR task_id = ?", (task_ref, task_ref)).fetchone()
                if not t:
                    return json_response(self, 404, {'error': 'Task not found'})
                tid = t['id']
                # Check assignment
                ta = connection.execute("SELECT * FROM task_assignments WHERE task_id = ? AND staff_id = ?", (tid, user['id'])).fetchone()
                if not ta and user['role'] == 'staff':
                    return json_response(self, 403, {'error': 'Forbidden: You are not assigned to this task'})
                
                ts = now()
                connection.execute("UPDATE task_assignments SET status = 'ACKNOWLEDGED', acknowledged_at = ? WHERE task_id = ? AND staff_id = ?", (ts, tid, user['id']))
                if t['status'] in ('PENDING', 'assigned', 'PENDING'):
                    connection.execute("UPDATE tasks SET status = 'ACKNOWLEDGED', acknowledged_at = ?, updated_at = ? WHERE id = ?", (ts, ts, tid))
                
                connection.execute("""
                    INSERT INTO task_updates (task_id, staff_id, update_type, update_text, progress_percent, created_at)
                    VALUES (?, ?, 'acknowledge', 'Task acknowledged and queued.', 0, ?)
                """, (tid, user['id'], ts))
                
                if t['created_by_user_id']:
                    connection.execute("""
                        INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id)
                        VALUES (?, ?, ?, 'task', 0, ?, ?, 'task', ?)
                    """, (t['created_by_user_id'], f"Task Acknowledged: {t['title']}", f"{user['name']} acknowledged assigned task.", ts, str(tid), user['id']))
                
                log_activity(connection, 'task:acknowledged', user['id'], {'taskId': t['task_id'], 'recordId': tid})
            return json_response(self, 200, {'ok': True, 'message': 'Task acknowledged.'})

        if path.startswith('/api/tasks/') and path.endswith('/updates'):
            user = self.require_user(('staff', 'punong_barangay', 'admin'))
            if not user:
                return
            task_ref = path.split('/')[3]
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            
            update_text = str(payload.get('updateText') or payload.get('text') or payload.get('notes') or '').strip()
            if not update_text:
                return json_response(self, 400, {'error': 'Update text is required.'})
            
            update_type = str(payload.get('updateType') or payload.get('update_type') or 'progress').strip()
            if update_type not in ('progress', 'clarification_request', 'clarification_response', 'note', 'hold'):
                update_type = 'progress'
            
            progress_pct = int(payload.get('progressPercent') or payload.get('progress_percent') or 0)
            progress_pct = max(0, min(100, progress_pct))
            
            with db() as connection:
                t = connection.execute("SELECT * FROM tasks WHERE id = ? OR task_id = ?", (task_ref, task_ref)).fetchone()
                if not t:
                    return json_response(self, 404, {'error': 'Task not found'})
                tid = t['id']
                
                if user['role'] == 'staff':
                    ta = connection.execute("SELECT * FROM task_assignments WHERE task_id = ? AND staff_id = ?", (tid, user['id'])).fetchone()
                    if not ta:
                        return json_response(self, 403, {'error': 'Forbidden: You are not assigned to this task'})
                
                ts = now()
                cursor = connection.execute("""
                    INSERT INTO task_updates (task_id, staff_id, update_type, update_text, progress_percent, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (tid, user['id'], update_type, update_text, progress_pct, ts))
                up_id = cursor.lastrowid
                
                if update_type == 'progress':
                    # Update assignment to IN_PROGRESS if was PENDING or ACKNOWLEDGED
                    connection.execute("UPDATE task_assignments SET status = 'IN_PROGRESS' WHERE task_id = ? AND staff_id = ? AND status IN ('PENDING', 'ACKNOWLEDGED')", (tid, user['id']))
                    connection.execute("UPDATE tasks SET status = 'IN_PROGRESS', progress_percent = ?, updated_at = ? WHERE id = ?", (progress_pct, ts, tid))
                    if t['created_by_user_id']:
                        connection.execute("""
                            INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id)
                            VALUES (?, ?, ?, 'task', 0, ?, ?, 'task', ?)
                        """, (t['created_by_user_id'], f"Task Progress ({progress_pct}%): {t['title']}", f"{user['name']}: {update_text[:100]}", ts, str(tid), user['id']))
                    log_activity(connection, 'task:progress-updated', user['id'], {'taskId': t['task_id'], 'progress': progress_pct, 'updateId': up_id})
                
                elif update_type == 'clarification_request':
                    if t['created_by_user_id']:
                        connection.execute("""
                            INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id)
                            VALUES (?, ?, ?, 'task', 0, ?, ?, 'task', ?)
                        """, (t['created_by_user_id'], f"Clarification Needed: {t['title']}", f"{user['name']}: {update_text[:100]}", ts, str(tid), user['id']))
                    log_activity(connection, 'task:clarification-requested', user['id'], {'taskId': t['task_id'], 'updateId': up_id})
                
                elif update_type == 'clarification_response':
                    # Notify assigned staff
                    assignees = connection.execute("SELECT staff_id FROM task_assignments WHERE task_id = ?", (tid,)).fetchall()
                    for a in assignees:
                        connection.execute("""
                            INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id)
                            VALUES (?, ?, ?, 'task', 0, ?, ?, 'task', ?)
                        """, (a['staff_id'], f"Clarification Response: {t['title']}", f"{user['name']}: {update_text[:100]}", ts, str(tid), user['id']))
                    log_activity(connection, 'task:clarification-responded', user['id'], {'taskId': t['task_id'], 'updateId': up_id})
            return json_response(self, 201, {'ok': True, 'updateId': up_id, 'message': 'Task update recorded successfully.'})

        if path.startswith('/api/tasks/') and path.endswith('/hold'):
            user = self.require_user(('staff', 'punong_barangay', 'admin'))
            if not user:
                return
            task_ref = path.split('/')[3]
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                payload = {}
            reason = str(payload.get('reason', 'Marked on hold by staff')).strip()
            with db() as connection:
                t = connection.execute("SELECT * FROM tasks WHERE id = ? OR task_id = ?", (task_ref, task_ref)).fetchone()
                if not t:
                    return json_response(self, 404, {'error': 'Task not found'})
                tid = t['id']
                if user['role'] == 'staff':
                    ta = connection.execute("SELECT * FROM task_assignments WHERE task_id = ? AND staff_id = ?", (tid, user['id'])).fetchone()
                    if not ta:
                        return json_response(self, 403, {'error': 'Forbidden: You are not assigned to this task'})
                
                ts = now()
                connection.execute("UPDATE task_assignments SET status = 'ON_HOLD' WHERE task_id = ? AND staff_id = ?", (tid, user['id']))
                connection.execute("UPDATE tasks SET status = 'ON_HOLD', updated_at = ? WHERE id = ?", (ts, tid))
                connection.execute("""
                    INSERT INTO task_updates (task_id, staff_id, update_type, update_text, progress_percent, created_at)
                    VALUES (?, ?, 'hold', ?, 0, ?)
                """, (tid, user['id'], reason, ts))
                
                if t['created_by_user_id']:
                    connection.execute("""
                        INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id)
                        VALUES (?, ?, ?, 'task', 0, ?, ?, 'task', ?)
                    """, (t['created_by_user_id'], f"Task On Hold: {t['title']}", f"{user['name']}: {reason[:100]}", ts, str(tid), user['id']))
                log_activity(connection, 'task:marked-on-hold', user['id'], {'taskId': t['task_id'], 'reason': reason})
            return json_response(self, 200, {'ok': True, 'message': 'Task marked on hold.'})

        if path.startswith('/api/tasks/') and path.endswith('/complete'):
            user = self.require_user(('staff', 'punong_barangay', 'admin'))
            if not user:
                return
            task_ref = path.split('/')[3]
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                payload = {}
            notes = str(payload.get('completionNotes', '') or payload.get('notes', 'Completed')).strip()
            with db() as connection:
                t = connection.execute("SELECT * FROM tasks WHERE id = ? OR task_id = ?", (task_ref, task_ref)).fetchone()
                if not t:
                    return json_response(self, 404, {'error': 'Task not found'})
                tid = t['id']
                if user['role'] == 'staff':
                    ta = connection.execute("SELECT * FROM task_assignments WHERE task_id = ? AND staff_id = ?", (tid, user['id'])).fetchone()
                    if not ta:
                        return json_response(self, 403, {'error': 'Forbidden: You are not assigned to this task'})
                
                ts = now()
                connection.execute("""
                    UPDATE task_assignments
                    SET status = 'COMPLETED', completed_at = ?, completion_notes = ?
                    WHERE task_id = ? AND staff_id = ?
                """, (ts, notes, tid, user['id']))
                
                # Check if all assignees completed
                incomplete = connection.execute("SELECT COUNT(*) AS n FROM task_assignments WHERE task_id = ? AND status != 'COMPLETED'", (tid,)).fetchone()['n']
                if incomplete == 0 or user['role'] in ('punong_barangay', 'admin'):
                    connection.execute("""
                        UPDATE tasks
                        SET status = 'COMPLETED', progress_percent = 100, completion_date = ?, completion_notes = ?, updated_at = ?
                        WHERE id = ?
                    """, (ts, notes, ts, tid))
                
                connection.execute("""
                    INSERT INTO task_updates (task_id, staff_id, update_type, update_text, progress_percent, created_at)
                    VALUES (?, ?, 'complete', ?, 100, ?)
                """, (tid, user['id'], f"Completed: {notes}", ts))
                
                if t['created_by_user_id']:
                    connection.execute("""
                        INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id)
                        VALUES (?, ?, ?, 'task', 0, ?, ?, 'task', ?)
                    """, (t['created_by_user_id'], f"Task Completed: {t['title']}", f"{user['name']} completed the task. Notes: {notes[:100]}", ts, str(tid), user['id']))
                log_activity(connection, 'task:completed', user['id'], {'taskId': t['task_id'], 'recordId': tid, 'notes': notes})
            return json_response(self, 200, {'ok': True, 'message': 'Task marked completed.'})

        if path.startswith('/api/tasks/') and path.endswith('/assign'):
            user = self.require_user(('punong_barangay', 'admin'))
            if not user:
                return
            task_ref = path.split('/')[3]
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            with db() as connection:
                t = connection.execute("SELECT * FROM tasks WHERE id = ? OR task_id = ?", (task_ref, task_ref)).fetchone()
                if not t:
                    return json_response(self, 404, {'error': 'Task not found'})
                tid = t['id']
                staff_ids = payload.get('assignedStaffIds') or payload.get('staffIds') or payload.get('assignedTo')
                if not isinstance(staff_ids, list):
                    target_staff = [int(staff_ids)] if str(staff_ids).isdigit() else []
                else:
                    target_staff = [int(i) for i in staff_ids if str(i).isdigit()]
                if not target_staff:
                    return json_response(self, 400, {'error': 'At least one staff member must be specified.'})
                
                ts = now()
                if payload.get('replace', True):
                    connection.execute("DELETE FROM task_assignments WHERE task_id = ?", (tid,))
                for sid in target_staff:
                    connection.execute("INSERT OR REPLACE INTO task_assignments (task_id, staff_id, status, assigned_at) VALUES (?, ?, 'PENDING', ?)", (tid, sid, ts))
                    connection.execute("""
                        INSERT INTO notifications (user_id, title, body, notification_type, is_read, created_at, related_id, related_type, sender_id)
                        VALUES (?, ?, ?, 'task', 0, ?, ?, 'task', ?)
                    """, (sid, f"Task Assigned: {t['title']}", f"You have been assigned to task {t['task_id']}", ts, str(tid), user['id']))
                log_activity(connection, 'task:assigned', user['id'], {'taskId': t['task_id'], 'assignees': target_staff})
            return json_response(self, 200, {'ok': True, 'message': 'Staff assigned successfully.'})

        if path.startswith('/api/tasks/') and path.endswith('/status'):
            user = self.require_user(('punong_barangay', 'staff', 'admin'))
            if not user:
                return
            task_ref = path.split('/')[3]
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            new_status = str(payload.get('status', '')).strip().upper()
            if new_status not in ('PENDING', 'ACKNOWLEDGED', 'IN_PROGRESS', 'ON_HOLD', 'COMPLETED', 'CANCELLED', 'OVERDUE'):
                return json_response(self, 400, {'error': 'Invalid status'})
            with db() as connection:
                t = connection.execute("SELECT * FROM tasks WHERE id = ? OR task_id = ?", (task_ref, task_ref)).fetchone()
                if not t:
                    return json_response(self, 404, {'error': 'Task not found'})
                tid = t['id']
                ts = now()
                if user['role'] == 'staff':
                    ta = connection.execute("SELECT * FROM task_assignments WHERE task_id = ? AND staff_id = ?", (tid, user['id'])).fetchone()
                    if not ta:
                        return json_response(self, 403, {'error': 'Forbidden: You are not assigned to this task'})
                    connection.execute("UPDATE task_assignments SET status = ? WHERE task_id = ? AND staff_id = ?", (new_status, tid, user['id']))
                    if new_status == 'COMPLETED':
                        connection.execute("UPDATE task_assignments SET completed_at = ? WHERE task_id = ? AND staff_id = ?", (ts, tid, user['id']))
                    incomplete = connection.execute("SELECT COUNT(*) AS n FROM task_assignments WHERE task_id = ? AND status != 'COMPLETED'", (tid,)).fetchone()['n']
                    if incomplete == 0:
                        connection.execute("UPDATE tasks SET status = 'COMPLETED', progress_percent = 100, completion_date = ?, updated_at = ? WHERE id = ?", (ts, ts, tid))
                else:
                    connection.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE id = ?", (new_status, ts, tid))
                    connection.execute("UPDATE task_assignments SET status = ? WHERE task_id = ?", (new_status, tid))
                log_activity(connection, 'task:status-changed', user['id'], {'taskId': t['task_id'], 'status': new_status})
            return json_response(self, 200, {'ok': True, 'status': new_status, 'message': 'Task status updated.'})

        if path.startswith('/api/notifications/') and path.endswith('/read') and path != '/api/notifications/read':
            user = self.require_user()
            if not user:
                return
            notif_id_str = path.split('/')[3]
            if notif_id_str.isdigit():
                with db() as connection:
                    connection.execute("UPDATE notifications SET is_read = 1 WHERE user_id = ? AND id = ?", (user['id'], int(notif_id_str)))
            return json_response(self, 200, {'ok': True})

        if path == '/api/resend-verification':
            # Email verification is no longer required; endpoint kept for API compatibility.
            return json_response(self, 200, {'ok': True, 'message': 'Email verification is not required. You may sign in directly.'})

        if path == '/api/login':
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})
            identifier = str(payload.get('identifier') or payload.get('username') or payload.get('email') or '').strip().lower()
            if not identifier:
                return json_response(self, 400, {'error': 'Username or email and password are required.'})
            with db() as connection:
                user = connection.execute('SELECT * FROM users WHERE lower(username) = ? OR lower(email) = ? LIMIT 1', (identifier, identifier)).fetchone()
                if not user and identifier in ('captain', 'pb', 'punongbarangay'):
                    user = connection.execute("SELECT * FROM users WHERE role = 'punong_barangay' LIMIT 1").fetchone()
            if not user or user['account_status'] not in ('active', 'verified') or not password_matches(str(payload.get('password', '')), user['password_hash']):
                if user and user['account_status'] in ('pending', 'rejected', 'disabled', 'suspended', 'archived'):
                    return json_response(self, 403, {'error': f"This account is {user['account_status']}. Please contact the barangay administrator."})
                return json_response(self, 401, {'error': 'Invalid credentials'})
            if user['password_hash'].startswith('sha256') or len(user['password_hash']) == 64:
                with db() as connection:
                    connection.execute('UPDATE users SET password_hash = ? WHERE id = ?', (password_hash(str(payload.get('password', ''))), user['id']))
            token = secrets.token_urlsafe(32)
            TOKENS[token] = {'user_id': user['id'], 'expires_at': time.time() + TOKEN_TTL_SECONDS}
            resident_info = None
            user_dict = dict(user)
            if user_dict.get('resident_record_id'):
                with db() as connection:
                    res_row = connection.execute('SELECT resident_id, classification FROM residents WHERE id = ? AND archived_at IS NULL', (user_dict['resident_record_id'],)).fetchone()
                    if res_row:
                        resident_info = {'residentId': res_row['resident_id'], 'classification': res_row['classification']}
            with db() as connection:
                connection.execute('UPDATE users SET last_login_at = ? WHERE id = ?', (now(), user['id']))
                log_activity(connection, 'user:login', user['id'], {'username': user['username']})
            return json_response(self, 200, {
                'token': token,
                'user': {
                    'id': user['id'],
                    'name': user['name'],
                    'role': user['role'],
                    'roleLabel': role_label(user['role']),
                    'initials': ''.join(part[0] for part in user['name'].split()[:2]).upper(),
                    'username': user['username'],
                    'position': user['position'] if 'position' in user.keys() else None,
                    'department': user['department'] if 'department' in user.keys() else None,
                    'accountStatus': user['account_status'],
                    'residentId': resident_info['residentId'] if resident_info else None,
                    'classification': resident_info['classification'] if resident_info else None
                }
            })
        if path == '/api/logout':
            token = self.headers.get('Authorization', '').removeprefix('Bearer ').strip()
            session = TOKENS.pop(token, None)
            if session:
                with db() as connection:
                    row = connection.execute('SELECT id, username FROM users WHERE id = ?', (session['user_id'],)).fetchone()
                    if row:
                        log_activity(connection, 'user:logout', row['id'], {'username': row['username']})
            return json_response(self, 200, {'ok': True, 'message': 'Signed out successfully'})
        if path == '/api/notifications/read':
            user = self.require_user()
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                payload = {}
            with db() as connection:
                if payload.get('all'):
                    connection.execute('UPDATE notifications SET is_read = 1 WHERE user_id = ?', (user['id'],))
                else:
                    ids = [int(i) for i in payload.get('ids', []) if str(i).isdigit()]
                    if ids:
                        marks = ','.join('?' * len(ids))
                        connection.execute(f'UPDATE notifications SET is_read = 1 WHERE user_id = ? AND id IN ({marks})', [user['id'], *ids])
            return json_response(self, 200, {'ok': True})
        if path == '/api/programs':
            user = self.require_user(('admin', 'staff'))
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})
            title = str(payload.get('title', '')).strip()
            event_date = str(payload.get('eventDate', '')).strip()
            if not title or not event_date:
                return json_response(self, 400, {'error': 'Program title and date are required'})
            try:
                datetime.fromisoformat(event_date)
            except ValueError:
                return json_response(self, 400, {'error': 'Program date must be a valid date (YYYY-MM-DD)'})
            created = now()
            with db() as connection:
                seq = connection.execute("SELECT COALESCE(MAX(id), 0) + 1 AS n FROM programs").fetchone()['n']
                event_id = f"PRG-{datetime.now(timezone.utc).year}-{seq:05d}"
                connection.execute('''INSERT INTO programs (event_id, title, description, event_date, start_time, end_time, location, organizer, status, created_by_user_id, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?, ?)''',
                    (event_id, title, str(payload.get('description', '')).strip(), event_date, str(payload.get('startTime', '')).strip(), str(payload.get('endTime', '')).strip(),
                     str(payload.get('location', '')).strip(), str(payload.get('organizer', '')).strip() or user['name'], user['id'], created, created))
                log_activity(connection, 'program:created', user['id'], {'eventId': event_id, 'title': title, 'eventDate': event_date})
                notify_users(connection, resident_user_ids(connection), 'New barangay program posted', f'{title} on {event_date}', 'program')
            return json_response(self, 201, {'ok': True, 'eventId': event_id})
        if path == '/api/announcements':
            user = self.require_user(('admin', 'staff'))
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})
            title = str(payload.get('title', '')).strip()
            body = str(payload.get('body', '')).strip()
            if not title or not body:
                return json_response(self, 400, {'error': 'Announcement title and body are required'})
            created = now()
            with db() as connection:
                connection.execute('INSERT INTO announcements (title, body, category, priority, created_by_user_id, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (title, body, str(payload.get('category', '')).strip(), str(payload.get('priority', '')).strip(), user['id'], json.dumps({'category': payload.get('category', ''), 'priority': payload.get('priority', '')}), created, created))
                log_activity(connection, 'announcement:posted', user['id'], {'title': title})
                notify_users(connection, resident_user_ids(connection), 'New barangay announcement', title, 'announcement')
            return json_response(self, 201, {'ok': True})
        user = self.require_user(('admin', 'staff', 'resident'))
        if not user:
            return
        try:
            payload = self.read_json()
        except (ValueError, json.JSONDecodeError):
            return json_response(self, 400, {'error': 'Invalid request'})
        if path.startswith('/api/resident-ids/') and path.endswith('/replace'):
            if user['role'] not in ('admin', 'staff'):
                return json_response(self, 403, {'error': 'Forbidden'})
            resident_record_id = path.removeprefix('/api/resident-ids/').removesuffix('/replace')
            if not resident_record_id.isdigit():
                return json_response(self, 404, {'error': 'Resident not found'})
            changed_at = now()
            with db() as connection:
                resident = connection.execute('SELECT resident_id FROM residents WHERE id = ? AND archived_at IS NULL', (int(resident_record_id),)).fetchone()
                current = connection.execute('SELECT id FROM resident_id_issuances WHERE resident_record_id = ? ORDER BY id DESC LIMIT 1', (int(resident_record_id),)).fetchone()
                if not resident:
                    return json_response(self, 404, {'error': 'Resident not found'})
                if current:
                    connection.execute("UPDATE resident_id_issuances SET status = 'Replaced', updated_at = ?, remarks = ? WHERE id = ?", (changed_at, str(payload.get('remarks', 'Replacement requested')).strip(), current['id']))
                connection.execute('INSERT INTO resident_id_issuances (resident_record_id, resident_id, issuance_number, status, remarks, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)', (int(resident_record_id), resident['resident_id'], f'CARD-{datetime.now(timezone.utc).year}-{int(resident_record_id):06d}-{secrets.token_hex(3).upper()}', 'Assigned', str(payload.get('remarks', 'Replacement ID')).strip(), changed_at, changed_at))
                log_activity(connection, 'resident-id:replaced', user['id'], {'residentId': resident['resident_id']})
            return json_response(self, 201, {'ok': True, 'residentId': resident['resident_id'], 'status': 'Assigned'})
        if path == '/api/events':
            return self.save_event(user, payload)
        return json_response(self, 404, {'error': 'Not found'})

    # ------------------------------------------------------------------
    # PUT routes
    # ------------------------------------------------------------------
    def do_PUT(self):
        if not allow_rate(self, 'api-write', config.WRITE_RATE_LIMIT):
            return
        user = self.require_user(('admin', 'staff', 'punong_barangay'))
        if not user:
            return
        request_path = urlparse(self.path).path

        # --- TASK & PB ADMIN (PUT) ---
        if request_path.startswith('/api/tasks/'):
            user = self.require_user(('punong_barangay', 'admin'))
            if not user:
                return
            task_ref = request_path.removeprefix('/api/tasks/')
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            with db() as connection:
                t = connection.execute("SELECT * FROM tasks WHERE id = ? OR task_id = ?", (task_ref, task_ref)).fetchone()
                if not t:
                    return json_response(self, 404, {'error': 'Task not found'})
                tid = t['id']
                updates = []
                params = []
                if 'title' in payload and str(payload['title']).strip():
                    updates.append("title = ?"); params.append(str(payload['title']).strip())
                if 'description' in payload and str(payload['description']).strip():
                    updates.append("description = ?"); params.append(str(payload['description']).strip())
                if 'priority' in payload:
                    pri = str(payload['priority']).strip().upper()
                    if pri in ('LOW', 'NORMAL', 'HIGH', 'URGENT'):
                        updates.append("priority = ?"); params.append(pri)
                        log_activity(connection, 'task:priority-changed', user['id'], {'taskId': t['task_id'], 'priority': pri})
                if 'dueDate' in payload:
                    updates.append("due_date = ?"); params.append(str(payload['dueDate']).strip())
                    log_activity(connection, 'task:deadline-changed', user['id'], {'taskId': t['task_id'], 'dueDate': str(payload['dueDate']).strip()})
                if 'dueTime' in payload:
                    updates.append("due_time = ?"); params.append(str(payload['dueTime']).strip())
                if 'status' in payload:
                    st = str(payload['status']).strip().upper()
                    if st in ('PENDING', 'ACKNOWLEDGED', 'IN_PROGRESS', 'ON_HOLD', 'COMPLETED', 'CANCELLED', 'OVERDUE'):
                        updates.append("status = ?"); params.append(st)
                        log_activity(connection, 'task:status-changed', user['id'], {'taskId': t['task_id'], 'status': st})
                
                if updates:
                    updates.append("updated_at = ?"); params.append(now())
                    connection.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", [*params, tid])
                
                # Check reassignment if staffIds provided
                new_staff = payload.get('assignedStaffIds') or payload.get('assignedTo')
                if new_staff:
                    if not isinstance(new_staff, list):
                        target_staff = [int(new_staff)] if str(new_staff).isdigit() else []
                    else:
                        target_staff = [int(i) for i in new_staff if str(i).isdigit()]
                    if target_staff:
                        connection.execute("DELETE FROM task_assignments WHERE task_id = ?", (tid,))
                        for sid in target_staff:
                            connection.execute("INSERT INTO task_assignments (task_id, staff_id, status, assigned_at) VALUES (?, ?, 'PENDING', ?)", (tid, sid, now()))
                        log_activity(connection, 'task:reassigned', user['id'], {'taskId': t['task_id'], 'staffIds': target_staff})
                
                updated_t = connection.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
            return json_response(self, 200, {'ok': True, 'task': dict(updated_t), 'message': 'Task updated successfully.'})

        if request_path == '/api/admin/punong-barangay':
            user = self.require_user(('admin',))
            if not user:
                return
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            with db() as connection:
                pb = connection.execute("SELECT * FROM users WHERE role = 'punong_barangay'").fetchone()
                if not pb:
                    return json_response(self, 404, {'error': 'Punong Barangay account not found'})
                
                updates, params = [], []
                if 'name' in payload and str(payload['name']).strip():
                    new_name = str(payload['name']).strip()
                    updates.append("name = ?"); params.append(new_name)
                    set_setting(connection, 'punong_barangay', new_name)
                if 'status' in payload or 'accountStatus' in payload:
                    new_st = str(payload.get('status') or payload.get('accountStatus')).strip().lower()
                    if new_st in ('active', 'suspended', 'archived'):
                        updates.append("account_status = ?"); params.append(new_st)
                if 'password' in payload and str(payload['password']).strip():
                    updates.append("password_hash = ?"); params.append(password_hash(str(payload['password']).strip()))
                
                if updates:
                    connection.execute(f"UPDATE users SET {', '.join(updates)} WHERE id = ?", [*params, pb['id']])
                    log_activity(connection, 'admin:pb-configured', user['id'], {'punongBarangayId': pb['id'], 'changes': list(payload.keys())})
                
                updated_pb = connection.execute("SELECT id, username, name, role, email, account_status, position, department, contact_number, created_at FROM users WHERE id = ?", (pb['id'],)).fetchone()
            return json_response(self, 200, {'ok': True, 'user': dict(updated_pb), 'message': 'Punong Barangay settings updated.'})

        if request_path.startswith('/api/admin/staff/') and request_path.endswith('/archive'):
            user = self.require_user(('admin',))
            if not user:
                return
            staff_id = request_path.split('/')[4]
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                payload = {}
            with db() as connection:
                st_user = connection.execute("SELECT * FROM users WHERE id = ? AND role = 'staff'", (staff_id,)).fetchone()
                if not st_user:
                    return json_response(self, 404, {'error': 'Staff user not found'})
                changed_at = now()
                connection.execute("UPDATE users SET account_status = 'archived', archived_at = ?, archived_by = ?, archive_reason = ?, updated_at = ?, updated_by = ? WHERE id = ?", (changed_at, user['id'], str(payload.get('reason', '')).strip(), changed_at, user['id'], staff_id))
                # Invalidate tokens for this user
                to_del = [t for t, s in TOKENS.items() if s.get('user_id') == int(staff_id)]
                for t in to_del:
                    TOKENS.pop(t, None)
                log_activity(connection, 'staff:archived', user['id'], {'staffId': staff_id, 'name': st_user['name'], 'reason': str(payload.get('reason', '')).strip()})
                updated = connection.execute('SELECT * FROM users WHERE id = ?', (staff_id,)).fetchone()
                result = staff_payload(connection, updated)
            return json_response(self, 200, {'ok': True, 'message': f"Staff member '{st_user['name']}' has been archived.", 'staff': result})

        if request_path.startswith('/api/admin/staff/') and request_path.endswith('/restore'):
            user = self.require_user(('admin',))
            if not user:
                return
            staff_id = request_path.split('/')[4]
            with db() as connection:
                st_user = connection.execute("SELECT * FROM users WHERE id = ? AND role = 'staff'", (staff_id,)).fetchone()
                if not st_user:
                    return json_response(self, 404, {'error': 'Staff user not found'})
                changed_at = now()
                connection.execute("UPDATE users SET account_status = 'active', archived_at = NULL, archived_by = NULL, archive_reason = NULL, updated_at = ?, updated_by = ? WHERE id = ?", (changed_at, user['id'], staff_id))
                log_activity(connection, 'staff:restored', user['id'], {'staffId': staff_id, 'name': st_user['name']})
                updated = connection.execute('SELECT * FROM users WHERE id = ?', (staff_id,)).fetchone()
                result = staff_payload(connection, updated)
            return json_response(self, 200, {'ok': True, 'message': f"Staff member '{st_user['name']}' has been restored to active.", 'staff': result})

        if request_path.startswith('/api/admin/staff/'):
            user = self.require_user(('admin',))
            if not user:
                return
            staff_id = request_path.removeprefix('/api/admin/staff/')
            if not staff_id.isdigit():
                return json_response(self, 400, {'error': 'Invalid staff ID.'})
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request payload'})
            with db() as connection:
                existing = connection.execute("SELECT * FROM users WHERE id = ? AND role = 'staff'", (int(staff_id),)).fetchone()
                if not existing:
                    return json_response(self, 404, {'error': 'Staff user not found.'})
                first = str(payload.get('firstName', '')).strip()
                middle = str(payload.get('middleName', '')).strip()
                last = str(payload.get('lastName', '')).strip()
                suffix = str(payload.get('suffix', '')).strip()
                name = ' '.join(part for part in (first, middle, last, suffix) if part) or existing['name']
                username = str(payload.get('username', existing['username'])).strip().lower()
                if connection.execute('SELECT 1 FROM users WHERE lower(username) = ? AND id != ?', (username, int(staff_id))).fetchone():
                    return json_response(self, 409, {'error': 'That username is already in use.'})
                status = str(payload.get('status', existing['account_status'])).strip().lower()
                if status == 'inactive': status = 'disabled'
                if status not in ('active', 'disabled', 'archived'):
                    return json_response(self, 400, {'error': 'Invalid staff account status.'})
                handler_id = payload.get('handlerUserId', existing['handler_user_id'])
                if handler_id in ('', None):
                    handler_id = None
                if handler_id is not None:
                    handler = connection.execute("SELECT id FROM users WHERE id = ? AND role = 'admin' AND account_status IN ('active', 'verified')", (handler_id,)).fetchone()
                    if not handler:
                        return json_response(self, 400, {'error': 'Assigned account handler is not an active administrator.'})
                profile_image = existing['profile_image'] or ''
                if payload.get('removePhoto'):
                    profile_image = ''
                elif payload.get('imageBase64'):
                    try:
                        _, profile_image = process_and_save_image(payload['imageBase64'], 'staff', connection, apply_wm=True)
                    except ValueError as error:
                        return json_response(self, 400, {'error': str(error)})
                    except Exception:
                        logging.exception('Failed to update staff profile photo')
                        return json_response(self, 500, {'error': 'Failed to save staff profile photo.'})
                changed_at = now()
                fields = [name, username, str(payload.get('email', existing['email'] or '')).strip(), status, str(payload.get('position', existing['position'] or '')).strip(), str(payload.get('department', existing['department'] or '')).strip(), changed_at, user['id']]
                connection.execute('UPDATE users SET name = ?, username = ?, email = ?, account_status = ?, position = ?, department = ?, profile_image = ?, handler_user_id = ?, handler_assigned_at = CASE WHEN ? IS NOT handler_user_id THEN ? ELSE handler_assigned_at END, handler_assigned_by = CASE WHEN ? IS NOT handler_user_id THEN ? ELSE handler_assigned_by END, updated_at = ?, updated_by = ? WHERE id = ?', (*fields[:6], profile_image, handler_id, handler_id, changed_at, handler_id, user['id'], fields[6], fields[7], int(staff_id)))
                if handler_id != existing['handler_user_id']:
                    connection.execute('INSERT INTO staff_assignment_history (staff_id, previous_handler_id, new_handler_id, changed_by, changed_at, reason) VALUES (?, ?, ?, ?, ?, ?)', (int(staff_id), existing['handler_user_id'], handler_id, user['id'], changed_at, 'Handler changed during staff edit'))
                    log_activity(connection, 'staff:handler-changed', user['id'], {'staffId': int(staff_id), 'previousHandlerId': existing['handler_user_id'], 'newHandlerId': handler_id, 'reason': 'Handler changed during staff edit'})
                if status != 'active':
                    for token, session in list(TOKENS.items()):
                        if session.get('user_id') == int(staff_id): TOKENS.pop(token, None)
                log_activity(connection, 'staff:updated', user['id'], {'staffId': int(staff_id), 'changes': list(payload.keys())})
                updated = connection.execute('SELECT * FROM users WHERE id = ?', (int(staff_id),)).fetchone()
                result = staff_payload(connection, updated)
            return json_response(self, 200, {'ok': True, 'staff': result})

        if request_path == '/api/admin/officials/reorder':
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})
            order_ids = payload.get('order', [])
            items = payload.get('items', [])
            with db() as connection:
                if items:
                    for itm in items:
                        connection.execute('UPDATE barangay_officials SET display_order = ?, updated_at = ? WHERE id = ?', (itm['display_order'], now(), itm['id']))
                elif order_ids:
                    for idx, off_id in enumerate(order_ids):
                        connection.execute('UPDATE barangay_officials SET display_order = ?, updated_at = ? WHERE id = ?', (idx + 1, now(), off_id))
                log_activity(connection, 'admin:officials-reordered', user['id'], {'count': len(items) or len(order_ids)})
            return json_response(self, 200, {'ok': True, 'message': 'Officials reordered successfully.'})

        if request_path.startswith('/api/admin/officials/'):
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            off_id = request_path.removeprefix('/api/admin/officials/')
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})

            with db() as connection:
                existing = connection.execute('SELECT * FROM barangay_officials WHERE id = ?', (off_id,)).fetchone()
                if not existing:
                    return json_response(self, 404, {'error': 'Official not found.'})

                fn = str(payload.get('firstName', '')).strip()
                ln = str(payload.get('lastName', '')).strip()
                if (not fn or not ln) and payload.get('name'):
                    parts = str(payload.get('name')).strip().split()
                    if parts:
                        fn = parts[0]
                        ln = ' '.join(parts[1:]) if len(parts) > 1 else 'Official'
                fn = fn or existing['first_name']
                ln = ln or existing['last_name']
                pos = str(payload.get('position', '')).strip() or existing['position']
                mn = str(payload.get('middleName', existing['middle_name'] or '')).strip()
                sfx = str(payload.get('suffix', existing['suffix'] or '')).strip()
                bio = str(payload.get('committee', payload.get('bio', existing['bio'] or ''))).strip()
                contact = str(payload.get('contactInfo', payload.get('term_years', existing['contact_info'] or ''))).strip()
                is_vis = 1 if payload.get('isVisible', payload.get('is_visible', bool(existing['is_visible']))) else 0
                status_raw = payload.get('status', existing['status'])
                status = 'ARCHIVED' if str(status_raw).upper() == 'ARCHIVED' else 'ACTIVE'
                order_val = int(payload.get('displayOrder', payload.get('display_order', existing['display_order'])) or 0)

                profile_img = existing['profile_image'] or ''
                if payload.get('removePhoto') or payload.get('remove_photo'):
                    profile_img = ''
                img_b64 = payload.get('imageBase64') or payload.get('photo') or payload.get('data')
                if img_b64:
                    try:
                        _, pub_path = process_and_save_image(img_b64, 'officials', connection, apply_wm=True)
                        profile_img = pub_path
                    except ValueError as ve:
                        return json_response(self, 400, {'error': str(ve)})
                    except Exception as ex:
                        logging.error('Failed to update official photo: %s', ex)
                        return json_response(self, 500, {'error': 'Failed to update photo.'})

                connection.execute('''
                    UPDATE barangay_officials
                    SET first_name = ?, middle_name = ?, last_name = ?, suffix = ?, position = ?,
                        bio = ?, contact_info = ?, profile_image = ?, display_order = ?, is_visible = ?,
                        status = ?, updated_at = ?
                    WHERE id = ?
                ''', (fn, mn, ln, sfx, pos, bio, contact, profile_img, order_val, is_vis, status, now(), off_id))
                log_activity(connection, 'admin:official-updated', user['id'], {'officialId': off_id, 'name': f"{fn} {ln}"})
                updated = connection.execute('SELECT * FROM barangay_officials WHERE id = ?', (off_id,)).fetchone()
            res_dict = dict(updated)
            res_dict['name'] = f"{updated['first_name']} {updated['last_name']}".strip()
            res_dict['photo_path'] = updated['profile_image']
            res_dict['public_path'] = updated['profile_image']
            res_dict['status'] = (updated['status'] or 'active').lower()
            return json_response(self, 200, {'ok': True, 'official': res_dict})

        if request_path == '/api/admin/gallery/reorder':
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})
            order_ids = payload.get('order', [])
            items = payload.get('items', [])
            with db() as connection:
                if items:
                    for itm in items:
                        connection.execute('UPDATE gallery_items SET display_order = ?, updated_at = ? WHERE id = ?', (itm['display_order'], now(), itm['id']))
                elif order_ids:
                    for idx, g_id in enumerate(order_ids):
                        connection.execute('UPDATE gallery_items SET display_order = ?, updated_at = ? WHERE id = ?', (idx + 1, now(), g_id))
                log_activity(connection, 'admin:gallery-reordered', user['id'], {'count': len(items) or len(order_ids)})
            return json_response(self, 200, {'ok': True, 'message': 'Gallery reordered successfully.'})

        if request_path.startswith('/api/admin/gallery/'):
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            g_id = request_path.removeprefix('/api/admin/gallery/')
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})

            with db() as connection:
                existing = connection.execute('SELECT * FROM gallery_items WHERE id = ?', (g_id,)).fetchone()
                if not existing:
                    return json_response(self, 404, {'error': 'Gallery item not found.'})

                caption = str(payload.get('caption', existing['caption'])).strip() or existing['caption']
                desc = str(payload.get('description', existing['description'] or '')).strip()
                is_vis = 1 if payload.get('isVisible', payload.get('is_visible', bool(existing['is_visible']))) else 0
                order_val = int(payload.get('displayOrder', payload.get('display_order', existing['display_order'])) or 0)
                status_raw = payload.get('status', existing['status'])
                status = 'ARCHIVED' if str(status_raw).upper() == 'ARCHIVED' else 'ACTIVE'

                img_path = existing['image_path']
                thumb_path = existing['thumbnail_path']
                img_b64 = payload.get('imageBase64') or payload.get('photo') or payload.get('data')
                if img_b64:
                    try:
                        orig_p, pub_p = process_and_save_image(img_b64, 'gallery', connection, apply_wm=True)
                        img_path, thumb_path = pub_p, orig_p
                    except ValueError as ve:
                        return json_response(self, 400, {'error': str(ve)})
                    except Exception as ex:
                        logging.error('Failed to update gallery image: %s', ex)
                        return json_response(self, 500, {'error': 'Failed to save image.'})

                connection.execute('''
                    UPDATE gallery_items
                    SET caption = ?, description = ?, image_path = ?, thumbnail_path = ?, display_order = ?, is_visible = ?, status = ?, updated_at = ?
                    WHERE id = ?
                ''', (caption or existing['caption'], desc, img_path, thumb_path, order_val, is_vis, status, now(), g_id))
                log_activity(connection, 'admin:gallery-item-updated', user['id'], {'galleryId': g_id})
                updated = connection.execute('SELECT * FROM gallery_items WHERE id = ?', (g_id,)).fetchone()
            res_dict = dict(updated)
            res_dict['public_path'] = updated['image_path']
            res_dict['thumbnail'] = updated['thumbnail_path'] or updated['image_path']
            res_dict['status'] = (updated['status'] or 'active').lower()
            return json_response(self, 200, {'ok': True, 'item': res_dict})

        if request_path == '/api/admin/website-settings':
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})
            allowed_keys = [
                'barangay_name', 'municipality', 'punong_barangay', 'barangay_secretary',
                'welcome_badge', 'welcome_title', 'welcome_subtitle', 'hero_overlay_opacity', 'hero_visible',
                'contact_address', 'contact_phone', 'contact_email', 'contact_office_hours',
                'contact_emergency', 'contact_website', 'social_facebook', 'social_twitter', 'social_youtube',
                'btn_portal_label', 'btn_services_label', 'btn_announcements_label', 'btn_programs_label'
            ]
            alias_map = {
                'emergency_hotline': 'contact_emergency',
                'contact_hours': 'contact_office_hours',
            }
            with db() as connection:
                for k, v in list(payload.items()):
                    target_k = alias_map.get(k, k)
                    if target_k in allowed_keys:
                        val = str(v).strip()
                        set_setting(connection, target_k, val)
                log_activity(connection, 'admin:website-settings-updated', user['id'], {k: payload[k] for k in payload if k in allowed_keys or k in alias_map})
            return json_response(self, 200, {'ok': True, 'message': 'Website settings updated successfully.'})
        if request_path == '/api/lgu-settings':
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})
            mapping = {'barangayName': 'barangay_name', 'municipality': 'municipality', 'punongBarangay': 'punong_barangay', 'barangaySecretary': 'barangay_secretary'}
            changed_at = now()
            with db() as connection:
                for key, setting in mapping.items():
                    if key in payload:
                        connection.execute('UPDATE system_settings SET setting_value = ?, updated_at = ? WHERE setting_key = ?', (str(payload[key]).strip(), changed_at, setting))
            return json_response(self, 200, {'ok': True})
        if request_path == '/api/admin/protection-settings':
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})
            changed_at = now()
            with db() as connection:
                if 'enabled' in payload:
                    set_setting(connection, 'cp_enabled', 'true' if payload['enabled'] else 'false')
                if 'watermarkEnabled' in payload:
                    set_setting(connection, 'cp_watermark_enabled', 'true' if payload['watermarkEnabled'] else 'false')
                if 'watermarkText' in payload:
                    wm_text = str(payload['watermarkText']).strip()[:120] or 'OFFICIAL BARANGAY WEBSITE'
                    set_setting(connection, 'cp_watermark_text', wm_text)
                if 'watermarkOpacity' in payload:
                    try:
                        op = max(0.05, min(0.70, float(payload['watermarkOpacity'])))
                    except (TypeError, ValueError):
                        op = 0.30
                    set_setting(connection, 'cp_watermark_opacity', f'{op:.2f}')
                if 'watermarkPosition' in payload:
                    pos = str(payload['watermarkPosition']).strip()
                    if pos not in ('bottom-right', 'bottom-left', 'bottom-center', 'center'):
                        pos = 'bottom-right'
                    set_setting(connection, 'cp_watermark_position', pos)
                if 'rightClickProtection' in payload:
                    set_setting(connection, 'cp_right_click_protection', 'true' if payload['rightClickProtection'] else 'false')
                if 'dragPrevention' in payload:
                    set_setting(connection, 'cp_drag_prevention', 'true' if payload['dragPrevention'] else 'false')
                log_activity(connection, 'admin:protection-settings-updated', user['id'], {k: v for k, v in payload.items()})
                ps = get_protection_settings(connection)
            return json_response(self, 200, {'ok': True, 'settings': ps})
        if request_path == '/api/resident-id-settings':
            if user['role'] != 'admin':
                return json_response(self, 403, {'error': 'Forbidden'})
            try:
                payload = self.read_json()
            except (ValueError, json.JSONDecodeError):
                return json_response(self, 400, {'error': 'Invalid request'})
            prefix = str(payload.get('prefix', '')).strip().upper()
            try:
                sequence_length = int(payload.get('sequenceLength', 6))
            except (TypeError, ValueError):
                return json_response(self, 400, {'error': 'Sequence length must be a number'})
            if not prefix.replace('-', '').isalnum() or not 2 <= len(prefix) <= 20 or not 4 <= sequence_length <= 12:
                return json_response(self, 400, {'error': 'Use a 2-20 character prefix and a sequence length from 4 to 12'})
            with db() as connection:
                changed_at = now()
                connection.execute('UPDATE system_settings SET setting_value = ?, updated_at = ? WHERE setting_key = ?', (prefix, changed_at, 'resident_id_prefix'))
                connection.execute('UPDATE system_settings SET setting_value = ?, updated_at = ? WHERE setting_key = ?', (str(sequence_length), changed_at, 'resident_id_sequence_length'))
            return json_response(self, 200, {'ok': True, 'prefix': prefix, 'sequenceLength': sequence_length})
        if request_path.startswith('/api/programs/'):
            program_ref = request_path.removeprefix('/api/programs/')
            with db() as connection:
                program = connection.execute('SELECT * FROM programs WHERE event_id = ? OR id = ?', (program_ref, int(program_ref) if program_ref.isdigit() else -1)).fetchone()
                if not program:
                    return json_response(self, 404, {'error': 'Program not found'})
                try:
                    payload = self.read_json()
                except (ValueError, json.JSONDecodeError):
                    return json_response(self, 400, {'error': 'Invalid request'})
                updates, params = [], []
                field_map = {'title': 'title', 'description': 'description', 'eventDate': 'event_date', 'startTime': 'start_time', 'endTime': 'end_time', 'location': 'location', 'organizer': 'organizer'}
                for key, column in field_map.items():
                    if key in payload:
                        value = str(payload[key]).strip()
                        if key == 'title' and not value:
                            return json_response(self, 400, {'error': 'Program title cannot be empty'})
                        if key == 'eventDate':
                            try:
                                datetime.fromisoformat(value)
                            except ValueError:
                                return json_response(self, 400, {'error': 'Program date must be a valid date (YYYY-MM-DD)'})
                        updates.append(f'{column} = ?'); params.append(value)
                if 'status' in payload:
                    status = payload['status']
                    if status not in PROGRAM_STATUSES:
                        return json_response(self, 400, {'error': 'Invalid program status'})
                    updates.append('status = ?'); params.append(status)
                if not updates:
                    return json_response(self, 400, {'error': 'No changes provided'})
                updates.append('updated_at = ?'); params.append(now())
                params.append(program['id'])
                connection.execute(f"UPDATE programs SET {', '.join(updates)} WHERE id = ?", params)
                log_activity(connection, 'program:updated', user['id'], {'eventId': program['event_id'], 'changes': list(payload.keys())})
                return json_response(self, 200, {'ok': True})
        if request_path.startswith('/api/requests/'):
            request_ref = request_path.removeprefix('/api/requests/')
            with db() as connection:
                request_row = connection.execute('SELECT * FROM requests WHERE request_id = ? OR id = ?', (request_ref, int(request_ref) if request_ref.isdigit() else -1)).fetchone()
                if not request_row:
                    return json_response(self, 404, {'error': 'Request not found'})
                try:
                    payload = self.read_json()
                except (ValueError, json.JSONDecodeError):
                    return json_response(self, 400, {'error': 'Invalid request'})
                status = str(payload.get('status', '')).strip().lower()
                if status not in REQUEST_STATUSES:
                    return json_response(self, 400, {'error': 'Invalid request status'})
                changed_at = now()
                updates = ['status = ?', 'updated_at = ?']
                params = [status, changed_at]
                if 'remarks' in payload:
                    updates.append('remarks = ?'); params.append(str(payload['remarks']).strip())
                if status not in PENDING_STATUSES and not request_row['processed_at']:
                    updates.append('processed_at = ?'); params.append(changed_at)
                if status in ('approved', 'ready for release', 'completed'):
                    updates.append('approved_at = ?'); params.append(changed_at)
                    updates.append('approved_by_user_id = ?'); params.append(user['id'])
                params.append(request_row['id'])
                connection.execute(f"UPDATE requests SET {', '.join(updates)} WHERE id = ?", params)
                log_activity(connection, 'request:status-updated', user['id'], {'requestId': request_row['request_id'], 'previous': request_row['status'], 'new': status})
                if request_row['owner_user_id']:
                    label = request_row['type'] or 'Your request'
                    notify_users(connection, [request_row['owner_user_id']], f'Request {status.replace("-", " ").title()}', f'Your {label} ({request_row["request_id"]}) is now {status.replace("-", " ")}.', 'request')
                return json_response(self, 200, {'ok': True, 'status': status})
        if request_path.startswith('/api/resident-ids/'):
            resident_record_id = request_path.removeprefix('/api/resident-ids/')
            if resident_record_id.isdigit() and user['role'] in ('admin', 'staff'):
                try:
                    payload = self.read_json()
                except (ValueError, json.JSONDecodeError):
                    return json_response(self, 400, {'error': 'Invalid request'})
                status = payload.get('status')
                allowed_statuses = ('Assigned', 'Ready for Issuance', 'Issued', 'Released', 'Lost', 'Cancelled')
                if status not in allowed_statuses:
                    return json_response(self, 400, {'error': 'Invalid issuance status'})
                changed_at = now()
                with db() as connection:
                    issuance = connection.execute('SELECT * FROM resident_id_issuances WHERE resident_record_id = ? ORDER BY id DESC LIMIT 1', (int(resident_record_id),)).fetchone()
                    resident = connection.execute('SELECT resident_id FROM residents WHERE id = ? AND archived_at IS NULL', (int(resident_record_id),)).fetchone()
                    if not resident:
                        return json_response(self, 404, {'error': 'Resident not found'})
                    if not issuance:
                        issuance_number = f'CARD-{datetime.now(timezone.utc).year}-{int(resident_record_id):06d}'
                        connection.execute('INSERT INTO resident_id_issuances (resident_record_id, resident_id, issuance_number, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)', (int(resident_record_id), resident['resident_id'], issuance_number, status, changed_at, changed_at))
                    else:
                        issued_at = changed_at if status in ('Issued', 'Released') else issuance['issued_at']
                        connection.execute('UPDATE resident_id_issuances SET status = ?, issued_at = ?, issued_by = ?, remarks = ?, updated_at = ? WHERE id = ?', (status, issued_at, user['name'] if status in ('Issued', 'Released') else issuance['issued_by'], str(payload.get('remarks', '')).strip() or issuance['remarks'], changed_at, issuance['id']))
                    log_activity(connection, 'resident-id:status-updated', user['id'], {'residentId': resident['resident_id'], 'status': status})
                return json_response(self, 200, {'ok': True, 'status': status})
            return json_response(self, 404, {'error': 'Not found'})
        if request_path.startswith('/api/registrations/'):
            registration_id = request_path.removeprefix('/api/registrations/')
            if registration_id.isdigit() and user['role'] == 'admin':
                try:
                    payload = self.read_json()
                except (ValueError, json.JSONDecodeError):
                    return json_response(self, 400, {'error': 'Invalid request'})
                status = payload.get('status')
                if status not in ('pending', 'verified', 'active', 'disabled', 'rejected'):
                    return json_response(self, 400, {'error': 'Invalid account status'})
                with db() as connection:
                    target = connection.execute("SELECT id, username FROM users WHERE id = ? AND role = 'resident'", (int(registration_id),)).fetchone()
                    if not target:
                        return json_response(self, 404, {'error': 'Registration not found'})
                    connection.execute('UPDATE users SET account_status = ? WHERE id = ?', (status, int(registration_id)))
                    log_activity(connection, 'registration:status-updated', user['id'], {'username': target['username'], 'new': status})
                    message = {'active': 'Your resident account has been approved.', 'verified': 'Your resident account has been verified.', 'rejected': 'Your resident account application was rejected.', 'disabled': 'Your resident account has been disabled.', 'pending': 'Your resident account is pending review.'}[status]
                    notify_users(connection, [target['id']], 'Account status updated', message, 'account')
                return json_response(self, 200, {'ok': True, 'status': status})
            return json_response(self, 404, {'error': 'Not found'})
        path, _, record_id = request_path.partition('/api/residents/')
        if path != '' or not record_id.isdigit():
            return json_response(self, 404, {'error': 'Not found'})
        try:
            payload = self.read_json()
        except (ValueError, json.JSONDecodeError):
            return json_response(self, 400, {'error': 'Invalid request'})
        allowed = ('householdNo', 'lastName', 'firstName', 'middleName', 'birthDate', 'gender', 'civilStatus', 'address', 'contact', 'voter', 'classification')
        fields = {key: payload[key] for key in allowed if key in payload}
        if not fields:
            return json_response(self, 400, {'error': 'No changes provided'})
        if 'classification' in fields and fields['classification'] not in VALID_CLASSIFICATIONS:
            return json_response(self, 400, {'error': 'Invalid resident classification'})
        mapping = {'householdNo': 'household_no', 'lastName': 'last_name', 'firstName': 'first_name', 'middleName': 'middle_name', 'birthDate': 'birth_date', 'gender': 'gender', 'civilStatus': 'civil_status', 'address': 'address', 'contact': 'contact', 'voter': 'voter', 'classification': 'classification'}
        sql = ', '.join(f'{mapping[key]} = ?' for key in fields)
        with db() as connection:
            previous = connection.execute('SELECT resident_id, classification FROM residents WHERE id = ? AND archived_at IS NULL', (int(record_id),)).fetchone()
            if not previous:
                return json_response(self, 404, {'error': 'Resident not found'})
            cursor = connection.execute(f'UPDATE residents SET {sql}, updated_at = ? WHERE id = ? AND archived_at IS NULL', [*fields.values(), now(), int(record_id)])
            if cursor.rowcount == 0:
                return json_response(self, 404, {'error': 'Resident not found'})
            if 'classification' in fields and fields['classification'] != previous['classification']:
                log_activity(connection, 'resident:classification-updated', user['id'], {'residentId': previous['resident_id'], 'previous': previous['classification'], 'new': fields['classification']})
        return json_response(self, 200, {'ok': True})

    # ------------------------------------------------------------------
    # DELETE routes (soft archive)
    # ------------------------------------------------------------------
    def do_DELETE(self):
        if not allow_rate(self, 'api-write', config.WRITE_RATE_LIMIT):
            return
        user = self.require_user(('admin', 'staff', 'resident'))
        if not user:
            return
        request_path = urlparse(self.path).path
        if request_path.startswith('/api/announcements/'):
            if user['role'] not in ('admin', 'staff'):
                return json_response(self, 403, {'error': 'Forbidden'})
            announcement_ref = request_path.removeprefix('/api/announcements/')
            with db() as connection:
                row = connection.execute('SELECT id, title FROM announcements WHERE id = ? AND archived_at IS NULL', (int(announcement_ref) if announcement_ref.isdigit() else -1,)).fetchone()
                if not row:
                    return json_response(self, 404, {'error': 'Announcement not found'})
                connection.execute('UPDATE announcements SET archived_at = ?, updated_at = ? WHERE id = ?', (now(), now(), row['id']))
                log_activity(connection, 'announcement:archived', user['id'], {'title': row['title']})
            return json_response(self, 200, {'ok': True})
        if request_path.startswith('/api/programs/'):
            if user['role'] not in ('admin', 'staff'):
                return json_response(self, 403, {'error': 'Forbidden'})
            program_ref = request_path.removeprefix('/api/programs/')
            with db() as connection:
                row = connection.execute('SELECT id, event_id, title FROM programs WHERE (event_id = ? OR id = ?) AND status != \'Archived\'', (program_ref, int(program_ref) if program_ref.isdigit() else -1)).fetchone()
                if not row:
                    return json_response(self, 404, {'error': 'Program not found'})
                connection.execute("UPDATE programs SET status = 'Archived', updated_at = ? WHERE id = ?", (now(), row['id']))
                log_activity(connection, 'program:archived', user['id'], {'eventId': row['event_id'], 'title': row['title']})
            return json_response(self, 200, {'ok': True})
        if request_path == '/api/resident/profile-photo':
            user = self.require_user(('resident',))
            if not user:
                return
            with db() as connection:
                connection.execute("UPDATE users SET profile_image = '' WHERE id = ?", (user['id'],))
                if user.get('resident_record_id'):
                    connection.execute("UPDATE residents SET profile_image = '', updated_at = ? WHERE id = ?", (now(), user['resident_record_id']))
                log_activity(connection, 'resident:profile-photo-removed', user['id'], {})
            return json_response(self, 200, {'ok': True, 'message': 'Profile picture removed.'})

        if request_path.startswith('/api/admin/residents/') and request_path.endswith('/profile-photo'):
            user = self.require_user(('admin', 'staff'))
            if not user:
                return
            res_id = request_path.split('/')[4]
            with db() as connection:
                connection.execute("UPDATE residents SET profile_image = '', updated_at = ? WHERE id = ?", (now(), res_id))
                connection.execute("UPDATE users SET profile_image = '' WHERE resident_record_id = ?", (res_id,))
                log_activity(connection, 'admin:resident-photo-removed', user['id'], {'residentId': res_id})
            return json_response(self, 200, {'ok': True, 'message': 'Resident profile photo removed.'})

        if request_path.startswith('/api/admin/officials/'):
            user = self.require_user(('admin',))
            if not user:
                return
            off_id = request_path.removeprefix('/api/admin/officials/')
            with db() as connection:
                connection.execute('DELETE FROM barangay_officials WHERE id = ?', (off_id,))
                log_activity(connection, 'admin:official-deleted', user['id'], {'officialId': off_id})
            return json_response(self, 200, {'ok': True, 'message': 'Official deleted.'})

        if request_path.startswith('/api/admin/gallery/'):
            user = self.require_user(('admin',))
            if not user:
                return
            g_id = request_path.removeprefix('/api/admin/gallery/')
            with db() as connection:
                connection.execute('DELETE FROM gallery_items WHERE id = ?', (g_id,))
                log_activity(connection, 'admin:gallery-item-deleted', user['id'], {'galleryId': g_id})
            return json_response(self, 200, {'ok': True, 'message': 'Gallery item deleted.'})

        user = self.require_user(('admin',))
        if not user:
            return
        prefix, _, record_id = request_path.partition('/api/residents/')
        if prefix != '' or not record_id.isdigit():
            return json_response(self, 404, {'error': 'Not found'})
        with db() as connection:
            cursor = connection.execute("UPDATE residents SET archived_at = ?, resident_status = 'ARCHIVED', updated_at = ? WHERE id = ? AND archived_at IS NULL", (now(), now(), int(record_id)))
            if cursor.rowcount == 0:
                return json_response(self, 404, {'error': 'Resident not found'})
            log_activity(connection, 'resident:archived', user['id'], {'recordId': int(record_id)})
        return json_response(self, 200, {'ok': True})

    # ------------------------------------------------------------------
    # Domain events (typed writes bound to the authenticated actor)
    # ------------------------------------------------------------------
    def save_event(self, user, event):
        event_type = event.get('event', 'unknown')
        allowed_roles = {
            'resident:added': ('admin', 'staff'),
            'resident:classified': ('admin', 'staff'),
            'cert:requested': ('admin', 'staff', 'resident'),
            'service:requested': ('admin', 'staff', 'resident'),
            'blotter:filed': ('admin', 'staff', 'resident'),
            'task:assigned': ('admin', 'staff'),
            'announcement:posted': ('admin', 'staff'),
            'cert:issued': ('admin', 'staff'),
        }
        if event_type not in allowed_roles or user['role'] not in allowed_roles[event_type]:
            return json_response(self, 403, {'error': 'You do not have permission to perform this action'})
        created = now()
        generated_resident_id = None
        generated_request_id = None
        with db() as connection:
            if event_type == 'resident:added':
                required = ('householdNo', 'lastName', 'firstName', 'birthDate', 'gender', 'civilStatus', 'address', 'contact')
                if any(not str(event.get(key, '')).strip() for key in required):
                    return json_response(self, 400, {'error': 'Required resident information is missing'})
                classification = str(event.get('classification', 'Unclassified')).strip()
                if classification not in VALID_CLASSIFICATIONS:
                    return json_response(self, 400, {'error': 'Invalid resident classification'})
                duplicate = connection.execute('''SELECT resident_id FROM residents WHERE lower(last_name) = ? AND lower(first_name) = ?
                    AND birth_date = ? AND lower(address) = ? AND contact = ? AND archived_at IS NULL''',
                    (str(event['lastName']).strip().lower(), str(event['firstName']).strip().lower(), str(event['birthDate']).strip(), str(event['address']).strip().lower(), str(event['contact']).strip())).fetchone()
                if duplicate:
                    return json_response(self, 409, {'error': 'A possible existing resident record was found. Please review it before creating a new Resident ID.', 'residentId': duplicate['resident_id']})
                generated_resident_id = next_resident_id(connection)
                connection.execute('INSERT INTO residents (resident_id, household_no, last_name, first_name, middle_name, birth_date, gender, civil_status, address, contact, voter, classification, owner_user_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)', (generated_resident_id, event.get('householdNo'), event.get('lastName'), event.get('firstName'), event.get('middleName'), event.get('birthDate'), event.get('gender'), event.get('civilStatus'), event.get('address'), event.get('contact'), event.get('voter', 'No'), classification, None, created, created))
                record_id = connection.execute('SELECT id FROM residents WHERE resident_id = ?', (generated_resident_id,)).fetchone()['id']
                connection.execute('INSERT INTO resident_id_issuances (resident_record_id, resident_id, issuance_number, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)', (record_id, generated_resident_id, f'CARD-{datetime.now(timezone.utc).year}-{record_id:06d}', 'Assigned', created, created))
            elif event_type == 'resident:classified':
                resident_ref = str(event.get('residentId', '')).strip()
                classification = str(event.get('classification', '')).strip()
                if classification not in VALID_CLASSIFICATIONS:
                    return json_response(self, 400, {'error': 'Invalid resident classification'})
                resident = connection.execute('SELECT id, resident_id, first_name, middle_name, last_name, classification FROM residents WHERE resident_id = ? AND archived_at IS NULL', (resident_ref,)).fetchone()
                if not resident:
                    return json_response(self, 404, {'error': 'Resident not found for classification'})
                full_name = ' '.join(part for part in (resident['first_name'], resident['middle_name'], resident['last_name']) if part)
                connection.execute('INSERT INTO classifications (resident_record_id, resident_id, full_name, classification, class_code, class_status, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (resident['id'], resident['resident_id'], full_name, classification, event.get('classCode'), event.get('classStatus', 'Active'), json.dumps(event), created, created))
                if classification != resident['classification']:
                    connection.execute('UPDATE residents SET classification = ?, updated_at = ? WHERE id = ?', (classification, created, resident['id']))
            elif event_type in ('cert:requested', 'blotter:filed', 'service:requested'):
                request_id = f'REQ-{datetime.now().year}-{secrets.token_hex(4).upper()}'
                generated_request_id = request_id
                owner_id = user['id']
                default_type = {'cert:requested': event.get('type', 'Certificate'), 'blotter:filed': 'Blotter Case', 'service:requested': 'Service Request'}[event_type]
                category = event.get('category', event.get('caseType'))
                resident_link = None
                if user['role'] == 'resident' and user.get('resident_record_id'):
                    resident_link = user['resident_record_id']
                    linked_res = connection.execute('SELECT * FROM residents WHERE id = ? AND archived_at IS NULL', (resident_link,)).fetchone()
                    if linked_res:
                        event['resident'] = f"{linked_res['first_name']} {linked_res['last_name']}".strip()
                        event['residentId'] = linked_res['resident_id']
                        event['classification'] = linked_res['classification']
                        event['address'] = linked_res['address']
                elif event.get('residentId'):
                    resident_ref = str(event.get('residentId', '')).strip()
                    linked = connection.execute('SELECT * FROM residents WHERE resident_id = ? AND archived_at IS NULL', (resident_ref,)).fetchone()
                    if linked:
                        resident_link = linked['id']
                        event['resident'] = f"{linked['first_name']} {linked['last_name']}".strip()
                        event['classification'] = linked['classification']
                        event['address'] = linked['address']
                connection.execute('INSERT INTO requests (request_id, owner_user_id, resident_record_id, type, purpose, category, status, date_needed, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (request_id, owner_id, resident_link, default_type, event.get('purpose', event.get('details')), category, 'pending', event.get('dateNeeded'), json.dumps({**event, 'requestId': request_id}), created, created))
                labels = {'cert:requested': 'New document request submitted', 'blotter:filed': 'New blotter case filed', 'service:requested': 'New service request submitted'}
                notify_users(connection, staff_user_ids(connection), labels[event_type], f'{default_type}: {event.get("purpose", event.get("details", ""))}'.strip(), 'request', exclude_user_id=user['id'])
            elif event_type == 'task:assigned':
                title = str(event.get('title', '')).strip()
                if not title:
                    return json_response(self, 400, {'error': 'Task title is required'})
                task_id = f'T-{datetime.now().year}-{secrets.token_hex(3).upper()}'
                assignee_id = None
                assignee_ref = event.get('assignedToUserId')
                if assignee_ref and str(assignee_ref).isdigit():
                    row = connection.execute("SELECT id FROM users WHERE id = ? AND role IN ('admin', 'staff')", (int(assignee_ref),)).fetchone()
                    assignee_id = row['id'] if row else None
                connection.execute('INSERT INTO tasks (task_id, title, task_type, priority, due_date, status, assigned_to_user_id, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                    (task_id, title, event.get('taskType'), event.get('priority'), event.get('dueDate'), 'assigned', assignee_id, json.dumps(event), created, created))
                if assignee_id:
                    notify_users(connection, [assignee_id], 'New task assigned', title, 'task', exclude_user_id=user['id'])
            elif event_type == 'announcement:posted':
                title = str(event.get('title', '')).strip()
                body = str(event.get('body', '')).strip()
                if not title or not body:
                    return json_response(self, 400, {'error': 'Announcement title and body are required'})
                connection.execute('INSERT INTO announcements (title, body, category, priority, created_by_user_id, payload_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    (title, body, event.get('category'), event.get('priority'), user['id'], json.dumps(event), created, created))
                notify_users(connection, resident_user_ids(connection), 'New barangay announcement', title, 'announcement', exclude_user_id=user['id'])
            elif event_type == 'cert:issued':
                cert_no = str(event.get('certNo', '')).strip()
                if not cert_no:
                    return json_response(self, 400, {'error': 'Certificate number is required'})
                if connection.execute('SELECT 1 FROM certificate_issuances WHERE cert_no = ?', (cert_no,)).fetchone():
                    return json_response(self, 409, {'error': 'That certificate number already exists'})
                request_link = None
                request_ref = str(event.get('requestId', '')).strip()
                request_row = None
                if request_ref:
                    request_row = connection.execute('SELECT * FROM requests WHERE request_id = ? OR id = ?', (request_ref, int(request_ref) if request_ref.isdigit() else -1)).fetchone()
                    if not request_row:
                        return json_response(self, 404, {'error': 'Linked request not found'})
                    request_link = request_row['id']
                    if request_row['resident_record_id']:
                        official_res = connection.execute('SELECT * FROM residents WHERE id = ?', (request_row['resident_record_id'],)).fetchone()
                        if official_res:
                            event['resident'] = f"{official_res['first_name']} {official_res['last_name']}".strip()
                            event['classification'] = official_res['classification']
                            event['address'] = official_res['address']
                connection.execute('INSERT INTO certificate_issuances (cert_no, request_record_id, issued_by_user_id, payload_json, created_at) VALUES (?, ?, ?, ?, ?)',
                    (cert_no, request_link, user['id'], json.dumps(event), created))
                if request_row:
                    connection.execute("UPDATE requests SET status = 'completed', approved_at = ?, approved_by_user_id = ?, processed_at = COALESCE(processed_at, ?), updated_at = ? WHERE id = ?",
                        (created, user['id'], created, created, request_row['id']))
                    log_activity(connection, 'request:status-updated', user['id'], {'requestId': request_row['request_id'], 'previous': request_row['status'], 'new': 'completed'})
                    if request_row['owner_user_id']:
                        notify_users(connection, [request_row['owner_user_id']], 'Certificate ready', f'Your {request_row["type"] or "certificate"} ({request_row["request_id"]}) has been issued. Reference: {cert_no}.', 'certificate')
            log_activity(connection, event_type, user['id'], event)
        return json_response(self, 200, {'ok': True, **({'residentId': generated_resident_id} if generated_resident_id else {}), **({'requestId': generated_request_id} if generated_request_id else {})})

    def serve_static(self):
        relative = urlparse(self.path).path.lstrip('/') or 'index.html'
        target = (ROOT / relative).resolve()
        if ROOT not in target.parents and target != ROOT:
            return json_response(self, 404, {'error': 'Not found'})
            
        # Security Boundary: Never serve database files, backend code, credentials, or logs to the browser
        blocked_exts = ('.sqlite3', '.db', '.sqlite', '.sqlite3-journal', '.backup', '.py', '.bat', '.vbs', '.sh', '.log', '.env')
        if any(target.name.lower().endswith(ext) for ext in blocked_exts) or target.name.startswith('.'):
            return json_response(self, 404, {'error': 'Not found'})

        if not target.is_file():
            # If requesting a specific static asset that doesn't exist, return 404 (don't send index.html)
            static_exts = ('.js', '.css', '.png', '.jpg', '.jpeg', '.svg', '.ico', '.webp', '.woff', '.woff2', '.ttf', '.json', '.map')
            if any(target.name.lower().endswith(ext) for ext in static_exts):
                return json_response(self, 404, {'error': 'File not found'})
            # SPA fallback: serve index.html for navigation routes
            target = ROOT / 'index.html'
            if not target.is_file():
                return json_response(self, 404, {'error': 'Not found'})

        allowed_exts = ('.html', '.js', '.css', '.json', '.svg', '.png', '.jpg', '.jpeg', '.webp', '.ico', '.woff2', '.woff', '.ttf', '.map')
        if target.suffix.lower() not in allowed_exts:
            return json_response(self, 404, {'error': 'Not found'})

        ext = target.suffix.lower()
        content_types = {
            '.html': 'text/html; charset=utf-8',
            '.js': 'application/javascript; charset=utf-8',
            '.css': 'text/css; charset=utf-8',
            '.json': 'application/json; charset=utf-8',
            '.svg': 'image/svg+xml',
            '.png': 'image/png',
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.webp': 'image/webp',
            '.ico': 'image/x-icon',
            '.woff2': 'font/woff2',
            '.woff': 'font/woff',
            '.ttf': 'font/ttf',
        }
        content_type = content_types.get(ext) or mimetypes.guess_type(str(target))[0] or 'application/octet-stream'
        data = target.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-cache, must-revalidate')
        add_security_headers(self)
        add_cors_headers(self)
        self.end_headers()
        self.wfile.write(data)


def activity_summary(event_type, payload):
    """Human-readable summary built strictly from the stored payload."""
    if event_type == 'user:login':
        return f"{payload.get('username', 'A user')} signed in."
    if event_type == 'user:logout':
        return f"{payload.get('username', 'A user')} signed out."
    if event_type == 'user:registered':
        return f"Account '{payload.get('username', '')}' registered and awaits approval."
    if event_type == 'resident:added':
        return f"Resident {payload.get('firstName', '')} {payload.get('lastName', '')} registered ({payload.get('residentId', 'ID pending')})."
    if event_type == 'resident:classified':
        return f"{payload.get('fullName', payload.get('residentId', 'Resident'))} classified as {payload.get('classification', '')}."
    if event_type == 'resident:archived':
        return f"Resident record #{payload.get('recordId', '')} archived."
    if event_type == 'resident:classification-updated':
        return f"Resident {payload.get('residentId', '')} classification changed from {payload.get('previous', '')} to {payload.get('new', '')}."
    if event_type == 'cert:requested':
        return f"{payload.get('type', 'Certificate')} requested for {payload.get('resident', payload.get('purpose', ''))}."
    if event_type == 'service:requested':
        return f"Service request ({payload.get('category', 'General')}): {str(payload.get('details', payload.get('purpose', '')))[:120]}"
    if event_type == 'blotter:filed':
        return f"Blotter filed: {payload.get('caseType', '')} — {payload.get('complainant', '')} vs {payload.get('respondent', '')}."
    if event_type == 'cert:issued':
        return f"Certificate {payload.get('certNo', '')} ({payload.get('type', '')}) issued to {payload.get('resident', '')}."
    if event_type == 'task:assigned':
        return f"Task assigned: {payload.get('title', payload.get('taskId', ''))}."
    if event_type == 'announcement:posted':
        return f"Announcement published: {payload.get('title', '')}."
    if event_type == 'announcement:archived':
        return f"Announcement archived: {payload.get('title', '')}."
    if event_type == 'program:created':
        return f"Program posted: {payload.get('title', '')} on {payload.get('eventDate', '')}."
    if event_type == 'program:updated':
        return f"Program {payload.get('eventId', '')} updated ({', '.join(payload.get('changes', []))})."
    if event_type == 'program:archived':
        return f"Program archived: {payload.get('title', payload.get('eventId', ''))}."
    if event_type == 'request:status-updated':
        return f"Request {payload.get('requestId', '')} moved from {payload.get('previous', '')} to {payload.get('new', '')}."
    if event_type == 'registration:status-updated':
        return f"Account '{payload.get('username', '')}' set to {payload.get('new', '')}."
    if event_type == 'resident-id:status-updated':
        return f"Resident ID card for {payload.get('residentId', '')} marked {payload.get('status', '')}."
    if event_type == 'resident-id:replaced':
        return f"Replacement card issuance created for {payload.get('residentId', '')}."
    return event_type


def run_integrity_check():
    findings = []

    def finding(check, severity, detail):
        findings.append({'check': check, 'severity': severity, 'detail': detail})

    with db() as connection:
        duplicates = connection.execute('SELECT resident_id, COUNT(*) AS n FROM residents GROUP BY resident_id HAVING n > 1').fetchall()
        for row in duplicates:
            finding('duplicate_resident_id', 'critical', f"Resident ID {row['resident_id']} appears {row['n']} times.")
        duplicates = connection.execute('SELECT lower(username) AS uname, COUNT(*) AS n FROM users GROUP BY lower(username) HAVING n > 1').fetchall()
        for row in duplicates:
            finding('duplicate_username', 'critical', f"Username '{row['uname']}' belongs to {row['n']} accounts.")
        orphans = connection.execute('SELECT r.request_id FROM requests r LEFT JOIN users u ON u.id = r.owner_user_id WHERE r.owner_user_id IS NOT NULL AND u.id IS NULL').fetchall()
        for row in orphans:
            finding('orphan_request_owner', 'critical', f"Request {row['request_id']} references a missing user account.")
        orphans = connection.execute('SELECT n.id FROM notifications n LEFT JOIN users u ON u.id = n.user_id WHERE u.id IS NULL').fetchall()
        for row in orphans:
            finding('orphan_notification', 'warning', f"Notification #{row['id']} references a missing user account.")
        orphans = connection.execute('SELECT l.id FROM activity_logs l LEFT JOIN users u ON u.id = l.actor_user_id WHERE l.actor_user_id IS NOT NULL AND u.id IS NULL').fetchall()
        for row in orphans:
            finding('orphan_activity_actor', 'warning', f"Activity log #{row['id']} references a missing user account.")
        orphans = connection.execute('SELECT i.id FROM resident_id_issuances i LEFT JOIN residents r ON r.id = i.resident_record_id WHERE r.id IS NULL').fetchall()
        for row in orphans:
            finding('orphan_id_issuance', 'warning', f"ID issuance #{row['id']} references a missing resident record.")
        orphans = connection.execute('SELECT u.id, u.username FROM users u LEFT JOIN residents r ON r.id = u.resident_record_id WHERE u.resident_record_id IS NOT NULL AND r.id IS NULL').fetchall()
        for row in orphans:
            finding('orphan_user_resident_link', 'warning', f"User '{row['username']}' links to a missing resident record.")
        invalid = connection.execute('SELECT resident_id, classification FROM residents').fetchall()
        for row in invalid:
            if row['classification'] not in VALID_CLASSIFICATIONS:
                finding('invalid_classification', 'warning', f"Resident {row['resident_id']} has unrecognized classification '{row['classification']}'.")
        invalid = connection.execute('SELECT request_id, status FROM requests').fetchall()
        for row in invalid:
            if row['status'].lower() not in REQUEST_STATUSES:
                finding('invalid_request_status', 'warning', f"Request {row['request_id']} has unrecognized status '{row['status']}'.")
        invalid = connection.execute("SELECT event_id, status FROM programs").fetchall()
        for row in invalid:
            if row['status'] not in PROGRAM_STATUSES:
                finding('invalid_program_status', 'warning', f"Program {row['event_id']} has unrecognized status '{row['status']}'.")
        missing = connection.execute("SELECT COUNT(*) AS n FROM residents WHERE created_at IS NULL OR updated_at IS NULL OR created_at = '' OR updated_at = ''").fetchone()['n']
        if missing:
            finding('missing_timestamps', 'warning', f"{missing} resident record(s) are missing created/updated timestamps.")
        missing = connection.execute("SELECT COUNT(*) AS n FROM requests WHERE created_at IS NULL OR updated_at IS NULL OR created_at = '' OR updated_at = ''").fetchone()['n']
        if missing:
            finding('missing_timestamps', 'warning', f"{missing} request record(s) are missing created/updated timestamps.")
        dupes = connection.execute('''SELECT lower(last_name) AS ln, lower(first_name) AS fn, birth_date, lower(address) AS addr, COUNT(*) AS n
            FROM residents WHERE archived_at IS NULL GROUP BY ln, fn, birth_date, addr HAVING n > 1''').fetchall()
        for row in dupes:
            finding('possible_duplicate_resident', 'info', f"{row['n']} active residents share name '{row['fn']} {row['ln']}', birth date {row['birth_date']}, and address.")
    return {'checkedAt': now(), 'findings': findings}


if __name__ == '__main__':
    logging.basicConfig(
        level=getattr(logging, config.LOG_LEVEL, logging.INFO),
        format='[%(asctime)s] [%(levelname)s] %(message)s',
        handlers=[
            logging.StreamHandler(sys.stdout),
            logging.FileHandler(config.LOG_FILE, encoding='utf-8')
        ]
    )
    init_db()

    server_address = (config.HOST, config.PORT)
    try:
        server = ThreadingHTTPServer(server_address, Handler)
    except OSError as err:
        if getattr(err, 'errno', None) in (10048, 98, 48) or '10048' in str(err):
            print(f"\n[ERROR] Port {config.PORT} is already in use by another application or server instance.")
            print(f"[GUIDE] Please close the conflicting application, or change BMS_PORT in config.py / .env.\n")
            logging.error("Port %s is already in use: %s", config.PORT, err)
            sys.exit(1)
        else:
            logging.error("Failed to start HTTP server: %s", err)
            raise

    def shutdown_signal_handler(signum, frame):
        logging.info("Shutting down BMS server gracefully...")
        import threading
        threading.Thread(target=server.shutdown).start()

    signal.signal(signal.SIGINT, shutdown_signal_handler)
    try:
        signal.signal(signal.SIGTERM, shutdown_signal_handler)
    except (AttributeError, ValueError):
        pass

    app_url = f"http://{config.HOST}:{config.PORT}/"
    health_url = f"http://{config.HOST}:{config.PORT}/api/health"
    print("========================================")
    print("BMS SERVER STARTED")
    print("========================================")
    print(f"Application:   {app_url}")
    print(f"Health Check:  {health_url}")
    print(f"Database:      {DB_PATH}")
    logging.info("BMS Server running at %s", app_url)

    if config.AUTO_OPEN_BROWSER:
        import threading, webbrowser
        def _auto_open():
            time.sleep(0.5)
            try:
                webbrowser.open(app_url)
            except Exception as e:
                logging.warning("Auto browser open failed: %s", e)
        threading.Thread(target=_auto_open, daemon=True).start()

    try:
        server.serve_forever()
    except Exception:
        pass
    finally:
        server.server_close()
        logging.info("BMS Server stopped cleanly.")