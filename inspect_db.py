import sqlite3
c = sqlite3.connect('bms.sqlite3')
c.row_factory = sqlite3.Row

print('=== TABLES ===')
for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
    print(' ', row[0])

print()
print('=== residents columns ===')
for row in c.execute('PRAGMA table_info(residents)'):
    print(f'  {row["cid"]} {row["name"]} {row["type"]} nullable={row["notnull"]==0}')

print()
print('=== users columns ===')
for row in c.execute('PRAGMA table_info(users)'):
    print(f'  {row["cid"]} {row["name"]} {row["type"]} nullable={row["notnull"]==0}')

print()
print('=== SAMPLE residents (up to 10) ===')
for row in c.execute('SELECT resident_id, first_name, last_name, classification, resident_status, archived_at FROM residents LIMIT 10'):
    print(dict(row))

print()
print('=== SAMPLE users ===')
for row in c.execute('SELECT id, username, role, account_status, resident_record_id FROM users'):
    print(dict(row))

print()
print('=== classifications table exists? ===')
exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='classifications'").fetchone()
print('yes' if exists else 'no')

print()
print('=== classification distribution in residents ===')
for row in c.execute("SELECT classification, COUNT(*) AS n FROM residents WHERE archived_at IS NULL GROUP BY classification ORDER BY n DESC"):
    print(f'  {row["classification"]}: {row["n"]}')