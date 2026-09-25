import urllib.request, json

def login(user, pw):
    h = {'Content-Type': 'application/json'}
    r = urllib.request.Request('http://127.0.0.1:8000/api/login', method='POST', headers=h)
    r.data = json.dumps({'username': user, 'password': pw}).encode()
    try:
        with urllib.request.urlopen(r) as resp:
            b = json.loads(resp.read())
            u = b['user']
            print(f'  OK   {user}/{pw} -> role={u["role"]}')
    except urllib.error.HTTPError as e:
        b = json.loads(e.read())
        print(f'  FAIL {user}/{pw} -> {e.code}: {b.get("error")}')

print('Testing all accounts:')
login('admin', 'admin')
login('staff', 'staff')
login('resident', 'resident')
login('admin', 'wrongpassword')
