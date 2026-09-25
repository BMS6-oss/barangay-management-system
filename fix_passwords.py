import sqlite3
import server

c = sqlite3.connect('bms.sqlite3')

# Reset test users
for username, password in [
    ('admin', 'admin'),
    ('staff', 'staff'),
    ('staff2', 'staff'),
    ('staff3', 'staff'),
    ('resident', 'resident'),
    ('barangayadmin', 'admin123'),
    ('punong_barangay', 'captain123'),
]:
    h = server.password_hash(password)
    c.execute('UPDATE users SET password_hash = ?, account_status = "active" WHERE username = ?', (h, username))
    print(f"Updated {username} password to '{password}'")

c.commit()
print("All test account passwords successfully updated in SQLite!")
