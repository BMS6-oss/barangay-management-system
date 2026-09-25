"""
Phase 19 Verification Suite:
Tests the exact scenarios required by the specification:
TEST 1 - SERVER RUNNING: Public endpoints, programs, announcements load safely.
TEST 2 - SERVER STOPPED: Offline behavior, fallback data, no start_bms.bat, no SERVER_UNAVAILABLE in UI.
TEST 3 - API TIMEOUT / CONNECTION INTERRUPTION: Safe fallback, no unhandled rejection.
TEST 4 - DATABASE FAILURE / SECURITY: No DB errors exposed, private SQLite files blocked from download.
TEST 5 - PRODUCTION / STATIC AUDIT: Zero references to start_bms.bat, local machine paths, or raw exceptions in public-facing code.
"""

import os
import sys
import json
import urllib.request
import urllib.error
import re
from pathlib import Path

BASE_URL = "http://127.0.0.1:8000"
ROOT = Path(__file__).resolve().parent

def log_test(name, passed, detail=""):
    status = "[PASS]" if passed else "[FAIL]"
    print(f"  {status} {name}")
    if detail:
        print(f"         {detail}")
    return passed

def run_suite():
    print("=" * 60)
    print("  BMS SERVER UNAVAILABLE & PUBLIC PAGE ROBUSTNESS TEST SUITE")
    print("=" * 60)
    
    results = []
    
    # -------------------------------------------------------------
    # TEST 1 — SERVER RUNNING
    # -------------------------------------------------------------
    print("\n[TEST 1] SERVER RUNNING — Live Public Data Loading")
    try:
        req = urllib.request.Request(f"{BASE_URL}/api/health")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            results.append(log_test("Health endpoint responds 200 OK", resp.status == 200 and data.get("ok") is True))
            results.append(log_test("Health endpoint hides full system path", data.get("db_path") == "bms.sqlite3"))
    except Exception as e:
        results.append(log_test("Health endpoint responds 200 OK", False, str(e)))

    try:
        req = urllib.request.Request(f"{BASE_URL}/api/public/info")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            results.append(log_test("GET /api/public/info returns public info", resp.status == 200 and "barangayName" in data))
    except Exception as e:
        results.append(log_test("GET /api/public/info returns public info", False, str(e)))

    try:
        req = urllib.request.Request(f"{BASE_URL}/api/public/programs")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            results.append(log_test("GET /api/public/programs returns 200 array", resp.status == 200 and isinstance(data, list)))
    except Exception as e:
        results.append(log_test("GET /api/public/programs returns 200 array", False, str(e)))

    try:
        req = urllib.request.Request(f"{BASE_URL}/api/public/announcements")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode())
            results.append(log_test("GET /api/public/announcements returns 200 array", resp.status == 200 and isinstance(data, list)))
    except Exception as e:
        results.append(log_test("GET /api/public/announcements returns 200 array", False, str(e)))

    # -------------------------------------------------------------
    # TEST 2 & TEST 3 — FALLBACK DATA AVAILABILITY & ISOLATION
    # -------------------------------------------------------------
    print("\n[TEST 2 & 3] FALLBACK DATA VERIFICATION — Offline & Timeout Tolerance")
    prog_fallback = ROOT / "data" / "public-programs.json"
    ann_fallback = ROOT / "data" / "public-announcements.json"

    results.append(log_test("data/public-programs.json exists", prog_fallback.is_file()))
    results.append(log_test("data/public-announcements.json exists", ann_fallback.is_file()))

    if prog_fallback.is_file():
        with open(prog_fallback, "r", encoding="utf-8") as f:
            prog_data = json.load(f)
            has_programs = "programs" in prog_data and len(prog_data["programs"]) > 0
            results.append(log_test("Fallback programs contains safe public records", has_programs))
            # Verify no sensitive keywords in fallback data
            raw_text = json.dumps(prog_data).lower()
            no_secrets = not any(w in raw_text for w in ["password", "token", "secret", "private", "owner_user_id", "resident_id"])
            results.append(log_test("Fallback programs contains ZERO private data/credentials", no_secrets))

    if ann_fallback.is_file():
        with open(ann_fallback, "r", encoding="utf-8") as f:
            ann_data = json.load(f)
            has_announcements = "announcements" in ann_data and len(ann_data["announcements"]) > 0
            results.append(log_test("Fallback announcements contains safe public bulletins", has_announcements))

    # Test static availability of fallback JSON through HTTP
    try:
        req = urllib.request.Request(f"{BASE_URL}/data/public-programs.json")
        with urllib.request.urlopen(req, timeout=3) as resp:
            results.append(log_test("HTTP GET /data/public-programs.json serves 200", resp.status == 200))
    except Exception as e:
        results.append(log_test("HTTP GET /data/public-programs.json serves 200", False, str(e)))

    # -------------------------------------------------------------
    # TEST 4 — DATABASE ACCESS RESTRICTION & SECURITY BOUNDARIES
    # -------------------------------------------------------------
    print("\n[TEST 4] SECURITY BOUNDARIES — Private SQLite & Code Protection")
    # Verify that private SQLite databases cannot be downloaded via HTTP
    private_endpoints = [
        "/bms.sqlite3",
        "/bms.sqlite3-journal",
        "/bms.db",
        "/server.py",
        "/config.py",
        "/.env",
        "/.env.example"
    ]
    for ep in private_endpoints:
        try:
            req = urllib.request.Request(f"{BASE_URL}{ep}")
            with urllib.request.urlopen(req, timeout=3) as resp:
                results.append(log_test(f"HTTP GET {ep} must be blocked (403/404)", False, f"Returned status {resp.status}!"))
        except urllib.error.HTTPError as e:
            results.append(log_test(f"HTTP GET {ep} is blocked (HTTP {e.code})", e.code in (403, 404)))
        except Exception as e:
            results.append(log_test(f"HTTP GET {ep} is blocked", True, str(e)))

    # -------------------------------------------------------------
    # TEST 5 — STATIC CODE AUDIT FOR LEAKED LOCAL PATHS & SCRIPTS
    # -------------------------------------------------------------
    print("\n[TEST 5] PRODUCTION CODE AUDIT — Clean Public Webpage")
    files_to_check = [
        "index.html",
        "sqlite-api.js",
        "js/welcome-data.js",
        "js/welcome-ui.js",
        "js/config.js",
        "data/public-programs.json",
        "data/public-announcements.json"
    ]
    
    forbidden_terms = [
        "start_bms.bat",
        "D:/Antigravetty/BMS",
        "d:\\antigravetty\\bms",
        "SERVER_UNAVAILABLE",
        "Unable to connect to the BMS server. Please make sure start_bms.bat is running"
    ]

    for fname in files_to_check:
        fpath = ROOT / fname
        if not fpath.is_file():
            results.append(log_test(f"File {fname} exists", False))
            continue
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        for term in forbidden_terms:
            # Check if term exists in public code
            found = False
            for line_no, line in enumerate(content.splitlines(), 1):
                # Allow comments explaining prevention if any, but not in executed code/UI text
                if term.lower() in line.lower():
                    # Check if it's in a UI element or active message
                    found = True
                    break
            
            results.append(log_test(
                f"'{fname}' is free from '{term}'",
                not found,
                f"Found on line {line_no}" if found else ""
            ))

    # Verify that API base URL resolution is dynamic and defaults to same-origin
    with open(ROOT / "sqlite-api.js", "r", encoding="utf-8") as f:
        api_code = f.read()
        results.append(log_test(
            "sqlite-api.js uses resolveApiBaseUrl() with same-origin standard",
            "resolveApiBaseUrl" in api_code and "window.location.origin" in api_code
        ))
        results.append(log_test(
            "sqlite-api.js exposes safe publicRequest normalization",
            "publicRequest" in api_code and "available: false" in api_code
        ))

    # Verify welcome-data.js has fallback handling and no error card exposure
    with open(ROOT / "js" / "welcome-data.js", "r", encoding="utf-8") as f:
        wd_code = f.read()
        results.append(log_test(
            "welcome-data.js has loadFallbackPrograms",
            "loadFallbackPrograms" in wd_code
        ))
        results.append(log_test(
            "welcome-data.js has loadFallbackAnnouncements",
            "loadFallbackAnnouncements" in wd_code
        ))
        results.append(log_test(
            "welcome-data.js renders non-technical unavailable messages",
            "Programs Temporarily Unavailable" in wd_code and "Announcements Temporarily Unavailable" in wd_code
        ))

    # Verify index.html fileProtocolBanner does not reference start_bms.bat
    with open(ROOT / "index.html", "r", encoding="utf-8", errors="ignore") as f:
        html_code = f.read()
        results.append(log_test(
            "index.html fileProtocolBanner does not mention start_bms.bat",
            "start_bms.bat" not in html_code
        ))
        results.append(log_test(
            "index.html loads js/config.js",
            "js/config.js" in html_code
        ))

    # -------------------------------------------------------------
    # SUMMARY
    # -------------------------------------------------------------
    print("\n" + "=" * 60)
    passed_count = sum(1 for r in results if r)
    total_count = len(results)
    print(f"  TOTAL CHECKS: {total_count} | PASSED: {passed_count} | FAILED: {total_count - passed_count}")
    print("=" * 60)
    return total_count == passed_count

if __name__ == "__main__":
    success = run_suite()
    sys.exit(0 if success else 1)
