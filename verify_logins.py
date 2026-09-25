import urllib.request
import json

def test_login(u, p):
    h = {'Content-Type': 'application/json'}
    r = urllib.request.Request('http://127.0.0.1:8000/api/login', method='POST', headers=h)
    r.data = json.dumps({'username': u, 'password': p}).encode()
    try:
        with urllib.request.urlopen(r) as resp:
            body = json.loads(resp.read().decode())
            uinfo = body['user']
            print(f"  [OK] {u}/{p} -> role: {uinfo['role']}, name: {uinfo['name']}, classification: {uinfo.get('classification')}")
    except urllib.error.HTTPError as e:
        print(f"  [FAIL] {u}/{p} -> HTTP {e.code}: {e.read().decode()}")

print("Testing all test accounts:")
test_login('admin', 'admin')
test_login('admin', 'admin123')
test_login('staff', 'staff')
test_login('staff', 'staff123')
test_login('resident', 'resident')
test_login('resident', 'resident123')
