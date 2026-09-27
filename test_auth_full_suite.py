"""Comprehensive End-to-End Authentication and 405 Fix Test Suite.

Verifies:
1. Valid administrator login (200 OK, session token, admin role)
2. Invalid password (401 Unauthorized, safe error)
3. Invalid username (401 Unauthorized, safe error)
4. Disabled account status (403 Forbidden, account disabled notice)
5. Session token verification (/api/me)
6. Logout (/api/logout, token revoked)
7. Protected API access restriction (unauthorized rejected)
8. HTTP method handling on /api/login (POST works, OPTIONS CORS works)
9. CORS support for GitHub Pages origin (https://bms6-oss.github.io)
"""
import json
import sqlite3
import time
import urllib.error
import urllib.request

BASE_URL = "http://127.0.0.1:8000"

def post_json(path, data, headers=None):
    url = f"{BASE_URL}{path}"
    h = {'Content-Type': 'application/json'}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=json.dumps(data).encode('utf-8'), headers=h, method='POST')
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8')), resp.headers
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode('utf-8'))
        except Exception:
            pass
        return e.code, body, e.headers

def get_json(path, headers=None):
    url = f"{BASE_URL}{path}"
    h = {}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method='GET')
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read().decode('utf-8')), resp.headers
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode('utf-8'))
        except Exception:
            pass
        return e.code, body, e.headers

def options_request(path, headers=None):
    url = f"{BASE_URL}{path}"
    h = {}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method='OPTIONS')
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers

def run_suite():
    print("=" * 65)
    print("  BMS AUTHENTICATION & LOGIN 405 FIX VERIFICATION SUITE")
    print("=" * 65)

    passed = 0
    failed = 0

    def assert_test(cond, name, details=""):
        nonlocal passed, failed
        if cond:
            print(f"  [PASS] {name}")
            passed += 1
        else:
            print(f"  [FAIL] {name} - {details}")
            failed += 1

    # --- Test 1: Valid Administrator Credentials ---
    print("\n[Test 1] Valid Administrator Credentials")
    status, body, _ = post_json('/api/login', {'identifier': 'admin', 'password': 'admin'})
    token = body.get('token')
    user = body.get('user', {})
    assert_test(status == 200, "Login returns HTTP 200 OK", f"Status: {status}")
    assert_test(bool(token and len(token) > 20), "Session token generated and returned")
    assert_test(user.get('role') == 'admin', "User role is confirmed as 'admin'")
    assert_test(user.get('name') == 'Punong Barangay', "User display name matches administrator")

    # --- Test 2: Invalid Password ---
    print("\n[Test 2] Invalid Password")
    status, body, _ = post_json('/api/login', {'identifier': 'admin', 'password': 'wrongpassword999'})
    assert_test(status == 401, "Login with invalid password returns HTTP 401", f"Status: {status}")
    assert_test(body.get('error') == 'Invalid credentials', "Returns safe, non-revealing error message")

    # --- Test 3: Invalid User ---
    print("\n[Test 3] Invalid User")
    status, body, _ = post_json('/api/login', {'identifier': 'nonexistent_user_xyz', 'password': 'somepassword'})
    assert_test(status == 401, "Login with invalid user returns HTTP 401", f"Status: {status}")
    assert_test(body.get('error') == 'Invalid credentials', "Returns safe generic error message")

    # --- Test 4: Disabled Account ---
    print("\n[Test 4] Disabled Account")
    # Temporarily set up a disabled test user in SQLite and clean up immediately
    conn = sqlite3.connect('bms.sqlite3')
    conn.execute("DELETE FROM users WHERE username = 'test_disabled_tmp'")
    conn.execute("""
        INSERT INTO users (username, password_hash, name, role, account_status, created_at)
        VALUES ('test_disabled_tmp', 'sha256$e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855', 'Disabled User', 'resident', 'disabled', datetime('now'))
    """)
    conn.commit()
    conn.close()

    try:
        status, body, _ = post_json('/api/login', {'identifier': 'test_disabled_tmp', 'password': ''})
        assert_test(status == 403, "Login with disabled account returns HTTP 403", f"Status: {status}")
        assert_test("disabled" in body.get('error', '').lower(), "Returns account status notification without leaking internals")
    finally:
        conn = sqlite3.connect('bms.sqlite3')
        conn.execute("DELETE FROM users WHERE username = 'test_disabled_tmp'")
        conn.commit()
        conn.close()

    # --- Test 5: Session Token Verification (Page Refresh simulation) ---
    print("\n[Test 5] Page Refresh (Session Persistence via /api/me)")
    status, body, _ = get_json('/api/me', headers={'Authorization': f'Bearer {token}'})
    assert_test(status == 200, "/api/me returns HTTP 200 for active session", f"Status: {status}")
    user_obj = body.get('user') if isinstance(body.get('user'), dict) else body
    assert_test(user_obj.get('username') == 'admin', "Current user identity persists", f"Username: {user_obj.get('username')}")

    # --- Test 6: Logout ---
    print("\n[Test 6] Logout and Session Invalidation")
    status, body, _ = post_json('/api/logout', {}, headers={'Authorization': f'Bearer {token}'})
    assert_test(status == 200, "Logout returns HTTP 200 OK", f"Status: {status}")
    # After logout, the token must be revoked
    status, body, _ = get_json('/api/me', headers={'Authorization': f'Bearer {token}'})
    assert_test(status == 401, "Revoked token is rejected with HTTP 401", f"Status: {status}")

    # --- Test 7: Protected Administrator API Access ---
    print("\n[Test 7] Direct Protected API Access (Unauthorized)")
    status, body, _ = post_json('/api/admin/staff', {'firstName': 'Test', 'lastName': 'Staff'})
    assert_test(status == 401, "Protected API rejects unauthenticated caller with HTTP 401", f"Status: {status}")

    # --- Test 8: HTTP Method & 405 Absence on Login Endpoint ---
    print("\n[Test 8] HTTP Method Verification on /api/login")
    status, _, _ = post_json('/api/login', {'identifier': 'admin', 'password': 'admin'})
    assert_test(status == 200, "POST /api/login returns HTTP 200 (NOT 405 Method Not Allowed)", f"Status: {status}")

    # --- Test 9: CORS Handling for GitHub Pages Origin ---
    print("\n[Test 9] CORS Preflight for GitHub Pages Origin")
    status, headers = options_request('/api/login', headers={
        'Origin': 'https://bms6-oss.github.io',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'Content-Type, Authorization'
    })
    allow_origin = headers.get('Access-Control-Allow-Origin', '')
    assert_test(status == 204, "OPTIONS /api/login returns HTTP 204 No Content", f"Status: {status}")
    assert_test(allow_origin == 'https://bms6-oss.github.io', "CORS header permits https://bms6-oss.github.io origin", f"Allow-Origin: {allow_origin}")

    print("\n" + "=" * 65)
    print(f"  SUMMARY: {passed} passed, {failed} failed")
    print("=" * 65)

    if failed > 0:
        exit(1)

if __name__ == '__main__':
    run_suite()
