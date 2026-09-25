import sqlite3
import server

c = sqlite3.connect('bms.sqlite3')
c.row_factory = sqlite3.Row
users = c.execute('SELECT id, username, password_hash, account_status, role FROM users').fetchall()
print('=== USERS IN DB ===')
for u in users:
    print(f"User ID: {u['id']}, Username: {u['username']}, Role: {u['role']}, Status: {u['account_status']}")
    print(f"  Hash: {u['password_hash'][:40]}...")
    matches = []
    for test_pwd in [u['username'], u['username'] + '123', 'admin', 'admin123', 'staff', 'staff123', 'resident', 'resident123', 'password', 'password123']:
        if server.password_matches(test_pwd, u['password_hash']):
            matches.append(test_pwd)
    print(f"  Matching passwords: {matches}")
