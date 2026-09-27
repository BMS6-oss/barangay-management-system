"""Database Migration Tool: SQLite to PostgreSQL for Barangay Management System (BMS).

Safely migrates all existing records from local SQLite (bms.sqlite3) to
the production PostgreSQL database (DATABASE_URL) while preserving:
- All user accounts, roles, and password hashes
- All residents, households, and classifications
- All requests, certificates, and ID issuances
- All official CMS entries, gallery items, and public programs
- All notifications, activity logs, and conversations
- Sequence/serial continuity for all primary keys

Usage:
    python scripts/migrate_to_postgres.py [--sqlite PATH] [--dry-run]
"""
import argparse
import logging
import os
import sqlite3
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import config
import db_adapter

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

TABLE_ORDER = [
    'users',
    'system_settings',
    'resident_id_sequences',
    'residents',
    'staff_assignment_history',
    'resident_id_issuances',
    'requests',
    'classifications',
    'tasks',
    'announcements',
    'certificate_issuances',
    'programs',
    'notifications',
    'activity_logs',
    'barangay_officials',
    'gallery_items',
    'conversations',
    'messages',
    'message_recipients',
    'task_assignments',
    'task_updates'
]


def migrate(sqlite_path: str, dry_run: bool = False):
    if not os.path.exists(sqlite_path):
        logger.error(f"Source SQLite database not found: {sqlite_path}")
        return False

    url = config.DATABASE_URL or os.getenv('DATABASE_URL', '')
    pg_conn = None
    if not dry_run:
        if not url or not (url.startswith('postgres://') or url.startswith('postgresql://')):
            logger.error("No valid PostgreSQL DATABASE_URL found in environment or config.")
            return False
        logger.info("Connecting to target PostgreSQL database...")
        pg_conn = db_adapter.get_postgres_connection()
        logger.info("Verifying PostgreSQL schema...")
        db_adapter.init_db(pg_conn)
    else:
        logger.info("DRY-RUN MODE: Scanning source SQLite database tables and records without writing to PostgreSQL.")

    logger.info(f"Connecting to source SQLite database: {sqlite_path}")
    src_conn = sqlite3.connect(sqlite_path)
    src_conn.row_factory = sqlite3.Row

    total_migrated = 0

    for table in TABLE_ORDER:
        # Check if table exists in SQLite
        table_exists = src_conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name = ?", (table,)
        ).fetchone()

        if not table_exists:
            logger.info(f"Table '{table}' does not exist in source database; skipping.")
            continue

        rows = src_conn.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            logger.info(f"Table '{table}': 0 rows to migrate.")
            continue

        cols = [k for k in rows[0].keys()]
        col_list_str = ", ".join(cols)
        placeholders_str = ", ".join(["%s"] * len(cols))

        logger.info(f"Table '{table}': Found {len(rows)} record(s) to migrate.")

        if dry_run:
            continue

        cur = pg_conn.raw_conn.cursor()
        inserted = 0
        for r in rows:
            vals = tuple(r[c] for c in cols)
            try:
                # Use ON CONFLICT DO NOTHING to avoid duplicate key errors on re-run
                cur.execute(
                    f"INSERT INTO {table} ({col_list_str}) VALUES ({placeholders_str}) ON CONFLICT DO NOTHING",
                    vals
                )
                inserted += 1
            except Exception as e:
                logger.warning(f"  Failed to insert into {table} (skipping row): {e}")

        pg_conn.commit()
        cur.close()
        logger.info(f"  Successfully migrated {inserted} row(s) into '{table}'.")
        total_migrated += inserted

        # Update PostgreSQL sequence for tables with integer id
        if 'id' in cols:
            try:
                cur = pg_conn.raw_conn.cursor()
                cur.execute(f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT max(id) FROM {table}), 1)
                    )
                """)
                pg_conn.commit()
                cur.close()
            except Exception:
                pass

    src_conn.close()
    if pg_conn:
        pg_conn.close()

    logger.info(f"Migration completed! Total records processed: {total_migrated}")
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="Migrate BMS SQLite database to PostgreSQL.")
    parser.add_argument('--sqlite', default=str(config.DATABASE_PATH), help="Path to source SQLite file")
    parser.add_argument('--dry-run', action='store_true', help="Inspect source data without writing to PostgreSQL")
    args = parser.parse_args()

    success = migrate(args.sqlite, args.dry_run)
    sys.exit(0 if success else 1)
