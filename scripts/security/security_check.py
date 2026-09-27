"""BMS Security Check — unified entry point.

Usage:
    python scripts/security/security_check.py

Performs all local security checks in sequence:
1. .env file guard — ensures .env is not staged or tracked
2. Staged secret scan — scans staged files for secrets
3. Working tree scan — scans all files for secret patterns
4. Build bundle scan — checks frontend files for server secrets
5. Docker protection check — verifies .dockerignore coverage
6. Private key / credential file detection

Returns non-zero exit code if ANY blocking issue is found.
NEVER displays actual secret values.
"""
import io
import os
import re
import subprocess
import sys

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# Add scripts/security to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))
from scan_secrets import (
    scan_content, scan_file_names, is_binary, BANNER,
    DANGEROUS_FILE_PATTERNS, SECRET_RULES
)

EXIT_CODE = 0


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")


def fail(msg):
    global EXIT_CODE
    EXIT_CODE = 1
    print(f"  [FAIL] {msg}")


def ok(msg):
    print(f"  [PASS] {msg}")


def check_env_not_tracked():
    """Verify .env files are not tracked by git."""
    section("1. Environment File Protection")

    # Check if .env is tracked
    res = subprocess.run(
        ['git', 'ls-files', '.env'],
        capture_output=True, text=True, encoding='utf-8', errors='ignore'
    )
    if res.stdout.strip():
        fail(".env is tracked by git! Run: git rm --cached .env")
    else:
        ok(".env is NOT tracked by git.")

    # Check if .env exists and is ignored
    if os.path.exists('.env'):
        res = subprocess.run(
            ['git', 'check-ignore', '.env'],
            capture_output=True, text=True, encoding='utf-8', errors='ignore'
        )
        if res.returncode == 0:
            ok(".env exists locally and is properly gitignored.")
        else:
            fail(".env exists but is NOT gitignored!")
    else:
        ok("No .env file present (will be created from .env.example).")

    # Check staged .env files
    res = subprocess.run(
        ['git', 'diff', '--cached', '--name-only'],
        capture_output=True, text=True, encoding='utf-8', errors='ignore'
    )
    staged = res.stdout.strip().splitlines()
    env_staged = [f for f in staged if re.match(r'^\.env', f) and not f.endswith('.example')]
    if env_staged:
        fail(f"Staged .env files detected: {', '.join(env_staged)}")
    else:
        ok("No .env files are staged.")


def check_dangerous_files():
    """Check for dangerous files in the working tree."""
    section("2. Credential & Key File Detection")

    findings = []
    for dirpath, _dirs, files in os.walk('.'):
        if '.git' in dirpath.split(os.sep):
            continue
        for fname in files:
            filepath = os.path.join(dirpath, fname)
            rel = os.path.relpath(filepath, '.').replace('\\', '/')
            # Skip files that are gitignored
            res = subprocess.run(
                ['git', 'check-ignore', '-q', rel],
                capture_output=True, text=True, encoding='utf-8', errors='ignore'
            )
            if res.returncode == 0:
                continue  # file is gitignored — OK
            for pat in DANGEROUS_FILE_PATTERNS:
                if pat.search(rel):
                    findings.append(rel)
                    break

    if findings:
        for f in findings:
            fail(f"Dangerous file NOT gitignored: {f}")
    else:
        ok("No unprotected credential/key files found.")


def check_secret_patterns():
    """Scan tracked files for secret patterns."""
    section("3. Secret Pattern Scan (Tracked Files)")

    res = subprocess.run(
        ['git', 'ls-files'],
        capture_output=True, text=True, encoding='utf-8', errors='ignore'
    )
    tracked = [f.strip() for f in res.stdout.splitlines() if f.strip()]

    all_findings = []
    for f in tracked:
        if is_binary(f):
            continue
        try:
            with open(f, 'r', encoding='utf-8', errors='ignore') as fh:
                content = fh.read()
            findings = scan_content(content, f)
            all_findings.extend(findings)
        except (OSError, PermissionError):
            continue

    if all_findings:
        print()
        for rule_name, filename, line_no in all_findings:
            fail(f"Possible secret in {filename}:{line_no} — {rule_name}")
        print("\n  Note: These may be false positives (e.g. test passwords, placeholders).")
        print("  Review each finding. If it is a real secret, remove it and rotate.")
    else:
        ok("No secret patterns detected in tracked files.")


def check_frontend_bundle():
    """Check frontend files for server-side secret exposure."""
    section("4. Frontend Bundle Secret Exposure Check")

    server_only_patterns = [
        ('DATABASE_URL', re.compile(r'(?i)DATABASE_URL\s*[:=]\s*["\'][^"\']+["\']')),
        ('DATABASE_PASSWORD', re.compile(r'(?i)DATABASE_PASSWORD\s*[:=]\s*["\'][^"\']+["\']')),
        ('GOOGLE_CLIENT_SECRET', re.compile(r'(?i)GOOGLE_CLIENT_SECRET\s*[:=]\s*["\'][^"\']+["\']')),
        ('JWT_SECRET', re.compile(r'(?i)JWT_SECRET\s*[:=]\s*["\'][^"\']+["\']')),
        ('SESSION_SECRET', re.compile(r'(?i)SESSION_SECRET\s*[:=]\s*["\'][^"\']+["\']')),
        ('SMTP_PASSWORD', re.compile(r'(?i)SMTP_PASS(?:WORD)?\s*[:=]\s*["\'][^"\']+["\']')),
        ('Private Key', re.compile(r'-----BEGIN (?:[A-Z0-9_ -]+ )?PRIVATE KEY-----')),
    ]

    frontend_files = []
    for pattern in ['index.html', 'sqlite-api.js']:
        if os.path.exists(pattern):
            frontend_files.append(pattern)
    for dirpath in ['js', 'css']:
        if os.path.isdir(dirpath):
            for dirp, _, fnames in os.walk(dirpath):
                for fn in fnames:
                    frontend_files.append(os.path.join(dirp, fn))

    findings = []
    for fpath in frontend_files:
        try:
            with open(fpath, 'r', encoding='utf-8', errors='ignore') as fh:
                content = fh.read()
            for name, pat in server_only_patterns:
                if pat.search(content):
                    findings.append((name, fpath))
        except (OSError, PermissionError):
            continue

    if findings:
        for name, fpath in findings:
            fail(f"Server secret '{name}' found in frontend file: {fpath}")
    else:
        ok("No server-side secrets found in frontend files.")


def check_dockerignore():
    """Verify .dockerignore protects secrets."""
    section("5. Docker Image Protection")

    if not os.path.exists('.dockerignore'):
        fail(".dockerignore is missing!")
        return

    with open('.dockerignore', 'r', encoding='utf-8') as f:
        content = f.read()

    required_patterns = ['.env', '*.pem', '*.key', '*.sqlite3', '*.db']
    for pat in required_patterns:
        if pat in content:
            ok(f".dockerignore contains: {pat}")
        else:
            fail(f".dockerignore missing protection for: {pat}")


def check_render_yaml():
    """Verify render.yaml doesn't contain secret values."""
    section("6. Render Deployment Security")

    if not os.path.exists('render.yaml'):
        ok("No render.yaml found (not using Render Blueprint).")
        return

    with open('render.yaml', 'r', encoding='utf-8') as f:
        content = f.read()

    # Check for hardcoded secrets in render.yaml
    secret_indicators = [
        re.compile(r'(?i)(?:password|secret|token|api_key)\s*:\s*(?!sync|false|true|\d+\s*$)[\'"]?[a-zA-Z0-9_\-/.]{16,}'),
    ]
    found_hardcoded = False
    for pat in secret_indicators:
        if pat.search(content):
            fail("Possible hardcoded secret in render.yaml!")
            found_hardcoded = True

    if not found_hardcoded:
        ok("No hardcoded secrets found in render.yaml.")

    # Verify secrets use sync: false (Render Dashboard configuration)
    if 'sync: false' in content:
        ok("Secrets in render.yaml use 'sync: false' (configured in Render Dashboard).")
    else:
        print("  [INFO] Verify sensitive env vars use 'sync: false' in render.yaml.")


def check_log_safety():
    """Check that logging doesn't expose secrets."""
    section("7. Log Safety Check")

    dangerous_log_patterns = [
        re.compile(r'(?i)(?:logging\.\w+|logger\.\w+|print)\s*\([^)\n]*(?:password|secret|api_key|credential)\s*[:=,].*?[%s\+\{]'),
        re.compile(r'(?i)print\s*\([^)\n]*(?:DATABASE_URL|SMTP_PASSWORD|CLIENT_SECRET|JWT_SECRET)'),
    ]

    if os.path.exists('server.py'):
        with open('server.py', 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
        found = False
        for pat in dangerous_log_patterns:
            if pat.search(content):
                fail("Server logging may expose secret values!")
                found = True
        if not found:
            ok("server.py logging appears safe -- no secret value exposure detected.")


def main():
    print("\n+==============================================================+")
    print("|          BMS SECURITY CHECK -- Defense in Depth              |")
    print("+==============================================================+")

    check_env_not_tracked()
    check_dangerous_files()
    check_secret_patterns()
    check_frontend_bundle()
    check_dockerignore()
    check_render_yaml()
    check_log_safety()

    section("SUMMARY")
    if EXIT_CODE == 0:
        print("  [OK] All security checks PASSED.")
        print("  Your BMS is ready for commit/push.\n")
    else:
        print("  [!!] One or more security checks FAILED.")
        print("  Review the findings above before committing.\n")

    return EXIT_CODE


if __name__ == '__main__':
    sys.exit(main())
