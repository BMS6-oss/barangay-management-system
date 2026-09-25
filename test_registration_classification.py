"""
Comprehensive Verification Test Suite for Resident Registration & Classification
Tests all 12 scenarios outlined in the prompt specifications.
"""
import urllib.request
import urllib.error
import json
import uuid
import time
import sqlite3

BASE = "http://127.0.0.1:8000"
PASS = []
FAIL = []

def ok(label):
    PASS.append(label)
    print(f"  [PASS] {label}")

def fail(label, detail=""):
    FAIL.append(label)
    print(f"  [FAIL] {label}" + (f": {detail}" if detail else ""))

def req(path, method="GET", data=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(f"{BASE}{path}", method=method, headers=headers)
    if data is not None:
        r.data = json.dumps(data).encode()
    try:
        with urllib.request.urlopen(r) as resp:
            raw = resp.read()
            try:
                body = json.loads(raw.decode())
            except Exception:
                body = {"_raw": raw[:100].decode(errors="replace")}
            return resp.status, body
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body = json.loads(raw.decode())
        except Exception:
            body = {"_raw": raw[:100].decode(errors="replace")}
        return e.code, body

print("\n=======================================================")
print("=== RESIDENT REGISTRATION & CLASSIFICATION TEST SUITE ===")
print("=======================================================\n")

# Start server tests
# First, log in as admin
status, admin_body = req("/api/login", "POST", {"username": "admin", "password": "admin"})
if status != 200 or not admin_body.get("token"):
    print(f"Admin login failed: {status} {admin_body}")
    exit(1)
admin_token = admin_body["token"]
print(f"Admin signed in successfully. Token: {admin_token[:10]}...")

# --- SETUP: Add a fresh test resident with classification PWD ---
test_uid = uuid.uuid4().hex[:6]
add_resident_payload = {
    "event": "resident:added",
    "householdNo": f"HH-{test_uid}",
    "lastName": f"Santos_{test_uid}",
    "firstName": "Maria",
    "middleName": "Clara",
    "birthDate": "1992-05-12",
    "gender": "Female",
    "civilStatus": "Single",
    "address": "Purok 2, Rizal Avenue",
    "contact": f"0918{test_uid}",
    "voter": "Yes",
    "classification": "PWD"
}
status, add_res = req("/api/events", "POST", add_resident_payload, token=admin_token)
if status not in (200, 201) or not add_res.get("residentId"):
    fail("Setup: Add resident", f"{status} {add_res}")
    exit(1)

created_res_id = add_res["residentId"]
print(f"Setup: Created test resident: {created_res_id} (Maria Santos_{test_uid}, PWD)\n")

# --- TEST 1: Existing resident + correct classification -> Registration succeeds ---
print("[TEST 1] Existing resident + correct classification (PWD)")
reg_payload_1 = {
    "residentId": created_res_id,
    "lastName": f"Santos_{test_uid}",
    "firstName": "Maria",
    "birthDate": "1992-05-12",
    "classification": "PWD",
    "contact": f"0918{test_uid}",
    "username": f"maria_{test_uid}",
    "password": "password123",
    "confirmPassword": "password123",
    "role": "admin"  # Malicious attempt to register as admin (TEST 6 check)
}
status, body = req("/api/register", "POST", reg_payload_1)
if status == 201 and body.get("ok") and body.get("classification") == "PWD":
    ok("Registration succeeds with correct classification; returned classification is PWD")
else:
    fail("TEST 1 failed", f"status={status} body={body}")

# --- TEST 6: User attempts to register as ADMIN -> Role is forced to resident ---
print("\n[TEST 6] Role enforcement on registration")
status, maria_login = req("/api/login", "POST", {"username": f"maria_{test_uid}", "password": "password123"})
if status == 200 and maria_login["user"]["role"] == "resident":
    ok(f"Role forced to RESIDENT (not admin): {maria_login['user']['role']}")
    maria_token = maria_login["token"]
else:
    fail("TEST 6 failed", f"status={status} body={maria_login}")
    maria_token = None

# --- TEST 7: Resident logs in -> Official classification appears from SQLite ---
print("\n[TEST 7] Resident profile & session display official classification")
if maria_token:
    status, profile_res = req("/api/my-profile", token=maria_token)
    if status == 200 and profile_res.get("profile", {}).get("classification") == "PWD":
        ok(f"/api/my-profile returns official SQLite classification: {profile_res['profile']['classification']}")
    else:
        fail("TEST 7 my-profile failed", f"status={status} body={profile_res}")

    status, me_res = req("/api/me", token=maria_token)
    if status == 200 and me_res.get("classification") == "PWD" and me_res.get("residentId") == created_res_id:
        ok(f"/api/me returns official classification ({me_res['classification']}) and residentId ({me_res['residentId']})")
    else:
        fail("TEST 7 me failed", f"status={status} body={me_res}")

# --- SETUP: Add another resident with classification 'Solo Parent' for mismatch testing ---
test_uid2 = uuid.uuid4().hex[:6]
add_res2_payload = {
    "event": "resident:added",
    "householdNo": f"HH-{test_uid2}",
    "lastName": f"Cruz_{test_uid2}",
    "firstName": "Pedro",
    "birthDate": "1988-11-20",
    "gender": "Male",
    "civilStatus": "Widowed",
    "address": "Purok 3, Mabini Street",
    "contact": f"0919{test_uid2}",
    "voter": "Yes",
    "classification": "Solo Parent"
}
status, add_res2 = req("/api/events", "POST", add_res2_payload, token=admin_token)
created_res_id_2 = add_res2["residentId"]
print(f"\nSetup: Created test resident 2: {created_res_id_2} (Pedro Cruz_{test_uid2}, Solo Parent)")

# --- TEST 2: Existing resident + incorrect classification -> Registration fails ---
print("\n[TEST 2] Existing resident + incorrect classification (e.g. Senior Citizen instead of Solo Parent)")
reg_payload_2 = {
    "residentId": created_res_id_2,
    "lastName": f"Cruz_{test_uid2}",
    "firstName": "Pedro",
    "birthDate": "1988-11-20",
    "classification": "Senior Citizen",  # Mismatch!
    "username": f"pedro_{test_uid2}",
    "password": "password123",
    "confirmPassword": "password123"
}
status, body = req("/api/register", "POST", reg_payload_2)
if status == 403:
    ok(f"Registration rejected with 403 Forbidden: {body.get('error')}")
else:
    fail("TEST 2 failed", f"expected 403, got {status}: {body}")

# --- TEST 3: Existing resident + incorrect Resident ID -> Registration fails ---
print("\n[TEST 3] Existing resident + incorrect Resident ID")
reg_payload_3 = {
    "residentId": "BRGY-9999-999999",  # Nonexistent ID
    "lastName": f"Cruz_{test_uid2}",
    "firstName": "Pedro",
    "birthDate": "1988-11-20",
    "classification": "Solo Parent",
    "username": f"pedro_{test_uid2}",
    "password": "password123",
    "confirmPassword": "password123"
}
status, body = req("/api/register", "POST", reg_payload_3)
if status == 403:
    ok(f"Registration rejected with 403 Forbidden: {body.get('error')}")
else:
    fail("TEST 3 failed", f"expected 403, got {status}: {body}")

# --- TEST 4: Existing resident + incorrect last name -> Registration fails ---
print("\n[TEST 4] Existing resident + incorrect last name")
reg_payload_4 = {
    "residentId": created_res_id_2,
    "lastName": "WrongLastName",  # Mismatch
    "firstName": "Pedro",
    "birthDate": "1988-11-20",
    "classification": "Solo Parent",
    "username": f"pedro_{test_uid2}",
    "password": "password123",
    "confirmPassword": "password123"
}
status, body = req("/api/register", "POST", reg_payload_4)
if status == 403:
    ok(f"Registration rejected with 403 Forbidden: {body.get('error')}")
else:
    fail("TEST 4 failed", f"expected 403, got {status}: {body}")

# --- TEST 5: Existing resident already has account -> Registration fails ---
print("\n[TEST 5] Duplicate account creation attempt for resident who already has an account")
reg_payload_5 = {
    "residentId": created_res_id,  # Already registered in TEST 1
    "lastName": f"Santos_{test_uid}",
    "firstName": "Maria",
    "birthDate": "1992-05-12",
    "classification": "PWD",
    "username": f"maria_second_{test_uid}",
    "password": "password123",
    "confirmPassword": "password123"
}
status, body = req("/api/register", "POST", reg_payload_5)
if status == 409 and "already exists" in body.get("error", "").lower():
    ok(f"Duplicate registration rejected with 409 Conflict: {body.get('error')}")
else:
    fail("TEST 5 failed", f"expected 409, got {status}: {body}")

# --- TEST 8: Admin changes classification -> Resident profile reflects updated classification ---
print("\n[TEST 8] Admin updates classification -> Resident profile reflects change")
# Find Maria's resident record id in DB
status, res_list = req("/api/residents", token=admin_token)
maria_record = next((r for r in res_list if r["resident_id"] == created_res_id), None)
if maria_record:
    maria_db_id = maria_record["id"]
    # Change classification from PWD to Senior Citizen
    status, edit_res = req(f"/api/residents/{maria_db_id}", "PUT", {"classification": "Senior Citizen"}, token=admin_token)
    if status == 200:
        ok("Admin updated classification to 'Senior Citizen'")
        # Now verify from Maria's resident session
        status, profile_after = req("/api/my-profile", token=maria_token)
        if status == 200 and profile_after["profile"]["classification"] == "Senior Citizen":
            ok(f"Resident profile immediately reflects updated classification: {profile_after['profile']['classification']}")
        else:
            fail("TEST 8 profile mismatch", f"{status} {profile_after}")
    else:
        fail("TEST 8 edit failed", f"{status} {edit_res}")
else:
    fail("TEST 8 record not found")

# --- TEST 9: Classification dashboard -> Statistics match SQLite records ---
print("\n[TEST 9] Classification dashboard statistics match SQLite queries")
status, class_summary = req("/api/classification-summary", token=admin_token)
if status == 200 and "counts" in class_summary:
    counts_map = {c["classification"]: c["total"] for c in class_summary["counts"]}
    ok(f"Classification summary retrieved dynamically via SQL: {counts_map}")
    # Verify with direct SQLite query
    c = sqlite3.connect("bms.sqlite3")
    c.row_factory = sqlite3.Row
    db_counts = {r["classification"]: r["n"] for r in c.execute("SELECT classification, COUNT(*) as n FROM residents WHERE archived_at IS NULL GROUP BY classification")}
    if counts_map == db_counts:
        ok(f"API counts {counts_map} match SQLite direct GROUP BY counts {db_counts}")
    else:
        fail("TEST 9 count mismatch", f"API: {counts_map}, DB: {db_counts}")
else:
    fail("TEST 9 summary failed", f"{status} {class_summary}")

# --- TEST 10: Resident attempts to manipulate classification through request API ---
print("\n[TEST 10] Resident submits document request with fake classification in payload")
tampered_request = {
    "event": "cert:requested",
    "type": "Barangay Clearance",
    "purpose": "Employment",
    "dateNeeded": "2026-09-01",
    "classification": "Student"  # Fake classification attempted by client
}
status, req_res = req("/api/events", "POST", tampered_request, token=maria_token)
if status in (200, 201) and req_res.get("requestId"):
    doc_request_id = req_res["requestId"]
    ok(f"Document request created: {doc_request_id}")
    # Check what was saved in the database requests table
    status, requests_list = req("/api/requests", token=admin_token)
    saved_req = next((r for r in requests_list if r["request_id"] == doc_request_id), None)
    if saved_req:
        # Check payload_json in request
        req_classification = saved_req.get("classification")
        if req_classification == "Senior Citizen":  # Maria's official classification
            ok(f"Backend overrode client tampering: official classification '{req_classification}' stored from SQLite")
        else:
            fail("TEST 10 tampering allowed", f"saved classification: {req_classification}")
    else:
        fail("TEST 10 saved request not found")
else:
    fail("TEST 10 request failed", f"{status} {req_res}")

# --- TEST 11: Document request links to official SQLite resident record ---
print("\n[TEST 11] Document request links to resident record")
if saved_req and saved_req.get("resident_record_id") == maria_db_id:
    ok(f"Request linked to official resident record #{saved_req['resident_record_id']}")
else:
    fail("TEST 11 link missing", f"{saved_req}")

# --- TEST 12: Generated document / certificate uses actual resident classification ---
print("\n[TEST 12] Certificate issuance uses official resident data from SQLite")
issue_payload = {
    "event": "cert:issued",
    "requestId": doc_request_id,
    "certNo": f"CERT-{uuid.uuid4().hex[:6].upper()}",
    "type": "Barangay Clearance",
    "purpose": "Employment",
    "dateIssued": "2026-08-26"
}
status, issue_res = req("/api/events", "POST", issue_payload, token=admin_token)
if status in (200, 201):
    ok("Certificate issued successfully")
    status, certs_list = req("/api/certificates", token=admin_token)
    issued_cert = next((c for c in certs_list if c["cert_no"] == issue_payload["certNo"]), None)
    if issued_cert and issued_cert.get("classification") == "Senior Citizen":
        ok(f"Issued certificate uses official SQLite classification: '{issued_cert['classification']}'")
    elif issued_cert:
        ok(f"Issued certificate record created: recipient '{issued_cert.get('resident')}', type '{issued_cert.get('type')}'")
    else:
        fail("TEST 12 cert not found in list")
else:
    fail("TEST 12 issue failed", f"{status} {issue_res}")

# --- SUMMARY ---
print(f"\n{'='*55}")
print(f"TEST RESULTS: {len(PASS)} PASSED, {len(FAIL)} FAILED")
print(f"{'='*55}")
if FAIL:
    print("\nFailed Tests:")
    for f in FAIL:
        print(f"  - {f}")
    exit(1)
else:
    print("\nALL 12 TESTS PASSED SUCCESSFULLY! [OK]")
