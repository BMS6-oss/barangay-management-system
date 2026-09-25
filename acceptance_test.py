"""
Full acceptance test for the BMS authentication and CRUD flow.
Run AFTER starting server.py:  python server.py
Then: python acceptance_test.py
"""
import urllib.request
import urllib.error
import json
import time
import uuid

BASE = "http://127.0.0.1:8000"
PASS = []
FAIL = []


def ok(label):
    PASS.append(label)
    print(f"  [PASS] {label}")


def fail(label, detail=""):
    FAIL.append(label)
    print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))


def req(path, method="GET", data=None, token=None, expect_status=200):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"{BASE}{path}", method=method, headers=headers)
    if data is not None:
        r.data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read()
            cors = resp.headers.get("Access-Control-Allow-Origin", "")
            try:
                body = json.loads(raw.decode())
            except Exception:
                body = {"_raw": raw[:80].decode(errors="replace")}
            return resp.status, body, cors
    except urllib.error.HTTPError as e:
        body = {}
        try:
            body = json.loads(e.read().decode())
        except Exception:
            pass
        cors = e.headers.get("Access-Control-Allow-Origin", "")
        return e.code, body, cors


print("\n=== BMS ACCEPTANCE TEST ===\n")

# 1. Health endpoint
print("[1] Health check")
status, body, cors = req("/api/health")
if status == 200 and body.get("ok"):
    ok("GET /api/health returns 200 + ok:true")
else:
    fail("GET /api/health", f"status={status} body={body}")

if cors:
    ok("CORS header present on /api/health")
else:
    fail("CORS header missing on /api/health")

# 2. Frontend served over HTTP
print("\n[2] Frontend served via HTTP")
status, body, _ = req("/")
if status == 200:
    ok("GET / returns 200 (index.html served)")
else:
    fail("GET /", f"status={status}")

# 3. sqlite-api.js served
status, body, _ = req("/sqlite-api.js")
if status == 200:
    ok("GET /sqlite-api.js returns 200")
else:
    fail("GET /sqlite-api.js", f"status={status}")

# 4. OPTIONS preflight
print("\n[3] CORS preflight (OPTIONS)")
status, _, cors = req("/api/login", method="OPTIONS")
if status in (200, 204):
    ok(f"OPTIONS /api/login returns {status}")
else:
    fail("OPTIONS /api/login", f"status={status}")
if cors:
    ok("CORS header present on OPTIONS response")
else:
    fail("CORS header missing on OPTIONS response")

# 5. Login with VALID credentials
print("\n[4] Login with valid credentials (admin/admin)")
status, body, cors = req("/api/login", method="POST", data={"username": "admin", "password": "admin"})
if status == 200 and body.get("token"):
    ok("POST /api/login with admin/admin returns token")
    token = body["token"]
else:
    fail("POST /api/login admin/admin", f"status={status} body={body}")
    token = None

if cors:
    ok("CORS header present on /api/login response")
else:
    fail("CORS header missing on /api/login response")

# 6. Login with INVALID credentials
print("\n[5] Login with invalid credentials")
status, body, _ = req("/api/login", method="POST", data={"username": "admin", "password": "wrongpassword"})
if status == 401:
    ok("POST /api/login with wrong password returns 401")
else:
    fail("POST /api/login wrong password", f"expected 401, got {status}")

# 7. Session validation
print("\n[6] Session & /api/me")
if token:
    status, body, _ = req("/api/me", token=token)
    if status == 200 and body.get("role") == "admin":
        ok("/api/me returns correct role for admin")
    else:
        fail("/api/me", f"status={status} body={body}")

    # Unauthenticated /api/me should fail
    status, body, _ = req("/api/me")
    if status == 401:
        ok("/api/me without token returns 401")
    else:
        fail("/api/me without token", f"expected 401, got {status}")

# 8. SQLite-backed CRUD — add a resident
print("\n[7] Resident CRUD via SQLite")
if token:
    uid = uuid.uuid4().hex[:6]
    ev = {
        "event": "resident:added",
        "lastName": "AcceptanceTest",
        "firstName": f"User{uid}",
        "birthDate": "1990-06-15",
        "gender": "Male",
        "civilStatus": "Single",
        "address": "123 Test Street",
        "contact": "09001234567",
        "voter": "Yes",
        "householdNo": "TEST-01",
    }
    status, body, _ = req("/api/events", method="POST", data=ev, token=token)
    if status == 200 and body.get("residentId"):
        ok(f"Add resident returns assigned ID: {body['residentId']}")
        resident_id = body["residentId"]
    else:
        fail("Add resident", f"status={status} body={body}")
        resident_id = None

    # Verify resident appears in list
    status, residents, _ = req("/api/residents", token=token)
    if status == 200 and isinstance(residents, list):
        found = next((r for r in residents if r.get("resident_id") == resident_id), None)
        if found:
            ok("New resident appears in /api/residents list")
            db_id = found["id"]
        else:
            fail("New resident not in /api/residents list")
            db_id = None
    else:
        fail("/api/residents list", f"status={status}")
        db_id = None

    # Edit resident
    if db_id:
        status, body, _ = req(f"/api/residents/{db_id}", method="PUT",
                               data={"firstName": f"Edited{uid}"}, token=token)
        if status == 200 and body.get("ok"):
            ok("PUT /api/residents/{id} updates resident")
        else:
            fail("PUT /api/residents/{id}", f"status={status} body={body}")

        # Archive resident
        status, body, _ = req(f"/api/residents/{db_id}", method="DELETE", token=token)
        if status == 200 and body.get("ok"):
            ok("DELETE /api/residents/{id} archives resident")
        else:
            fail("DELETE /api/residents/{id}", f"status={status} body={body}")

        # Confirm no longer in list
        _, residents2, _ = req("/api/residents", token=token)
        if isinstance(residents2, list) and not any(r.get("id") == db_id for r in residents2):
            ok("Archived resident no longer in active list")
        else:
            fail("Archived resident still visible in list")

# 9. Logout
print("\n[8] Logout")
if token:
    status, body, _ = req("/api/logout", method="POST", token=token)
    if status == 200:
        ok("POST /api/logout returns 200")
    else:
        fail("POST /api/logout", f"status={status} body={body}")

    # Token should now be invalid
    time.sleep(0.1)
    status, _, _ = req("/api/me", token=token)
    if status in (401, 403):
        ok("After logout, /api/me returns 401/403 (session revoked)")
    else:
        fail("After logout, /api/me should reject old token", f"got {status}")

# Summary
print(f"\n{'='*40}")
print(f"PASSED: {len(PASS)}  |  FAILED: {len(FAIL)}")
if FAIL:
    print("\nFailed checks:")
    for f in FAIL:
        print(f"  - {f}")
    raise SystemExit(1)
else:
    print("\nAll acceptance checks passed!")
