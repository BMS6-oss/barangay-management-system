import sqlite3, hashlib, secrets

def reset_pw(username, password):
    salt = secrets.token_hex(16)
    p = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode('ascii'), 240000).hex()
    h = 'pbkdf2_sha256$240000$' + salt + '$' + p
    c = sqlite3.connect('bms.sqlite3')
    c.execute('UPDATE users SET password_hash=? WHERE username=?', (h, username))
    c.commit()
    print(f'Reset {username} -> {password}')

reset_pw('staff', 'staff')
reset_pw('resident', 'resident')
