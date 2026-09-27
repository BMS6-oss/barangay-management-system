"""Secure Administrator Password Reset Utility for Barangay Management System (BMS).

Works transparently across both database backends:
- Production: PostgreSQL via DATABASE_URL
- Local Development: SQLite via config.DATABASE_PATH

Uses the project's standard secure hashing:
PBKDF2 HMAC-SHA256 with 240,000 rounds and a 16-byte cryptographically secure salt.

Usage:
    python scripts/reset_admin_password.py [--username admin] [--password NEW_PASSWORD]
    (If --password is omitted, prompts securely without terminal echo.)
"""
import argparse
import getpass
import hashlib
import os
import secrets
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import db_adapter


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt.encode('ascii'), 240_000)
    return f'pbkdf2_sha256$240000${salt}${digest.hex()}'


def reset_password(username: str, new_password: str) -> bool:
    engine = db_adapter.get_database_engine()
    print(f"Connecting to database backend: {engine}")

    with db_adapter.db() as conn:
        row = conn.execute("SELECT id, username, role, account_status FROM users WHERE lower(username) = ? LIMIT 1", (username.lower(),)).fetchone()
        if not row:
            print(f"[ERROR] User '{username}' was not found in the {engine} database.")
            return False

        new_hash = hash_password(new_password)
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, row['id']))
        if hasattr(conn, 'commit'):
            conn.commit()

        print(f"[OK] Password successfully reset for user '{username}' (role: {row['role']}) on {engine}.")
        return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Securely reset user password in BMS database.")
    parser.add_argument('--username', default='admin', help="Username to reset (defaults to 'admin')")
    parser.add_argument('--password', default=None, help="New password (optional; prompted securely if omitted)")
    args = parser.parse_args()

    target_pass = args.password
    if not target_pass:
        target_pass = getpass.getpass(f"Enter new password for '{args.username}': ")
        confirm_pass = getpass.getpass(f"Confirm new password for '{args.username}': ")
        if target_pass != confirm_pass:
            print("[ERROR] Passwords do not match.")
            sys.exit(1)

    if len(target_pass) < 4:
        print("[ERROR] Password must be at least 4 characters long.")
        sys.exit(1)

    success = reset_password(args.username, target_pass)
    sys.exit(0 if success else 1)
