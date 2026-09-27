"""BMS Secret & Credential Scanner -- defense-in-depth layer.

Operates in three modes:
  --staged      Scan staged files for secrets before commit (pre-commit hook)
  --env-guard   Block any .env files that are staged (pre-commit hook)
  --pre-push    Scan commits about to be pushed for secrets (pre-push hook)
  --full        Full-scan the working tree (manual / CI)
  --check-build Scan a directory for secrets in built/bundled files

NEVER prints actual secret values. Exit code 1 = BLOCKED.
"""
import argparse
import io
import os
import re
import subprocess
import sys

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# ---------------------------------------------------------------------------
# Secret detection rules — high-confidence patterns
# ---------------------------------------------------------------------------
SECRET_RULES = [
    ('Private Key Block',
     re.compile(r'-----BEGIN (?:[A-Z0-9_ -]+ )?PRIVATE KEY-----')),
    ('AWS Access Key',
     re.compile(r'\b(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b')),
    ('Google API Key',
     re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b')),
    ('GitHub Token',
     re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{36,255}\b')),
    ('Slack Token',
     re.compile(r'\bxox[baprs]-[0-9a-zA-Z]{10,48}\b')),
    ('Stripe Secret Key',
     re.compile(r'\b(?:sk|rk)_(?:live|test)_[0-9a-zA-Z]{24,99}\b')),
    ('SendGrid API Key',
     re.compile(r'\bSG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}\b')),
    ('Database URI with Password',
     re.compile(r'\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://[^:\s]+:[^@\s]+@[^\s]+', re.I)),
    ('JWT / Encoded Token',
     re.compile(r'\beyJ[a-zA-Z0-9_-]{10,}\.eyJ[a-zA-Z0-9_-]{10,}\.[a-zA-Z0-9_-]{10,}\b')),
    ('OpenAI / Anthropic Key',
     re.compile(r'\b(?:sk-[a-zA-Z0-9]{20,}|sk-ant-[a-zA-Z0-9_-]{20,})\b')),
    ('PyPI Token',
     re.compile(r'\bpypi-[A-Za-z0-9_-]{16,}\b')),
    ('Telegram Bot Token',
     re.compile(r'\b[0-9]{9,10}:[A-Za-z0-9_-]{35}\b')),
    ('Generic Hardcoded Password Assignment',
     re.compile(
         r'(?i)(?:password|passwd|pwd|secret|api_key|apikey|auth_token|access_token|private_key)'
         r'\s*[:=]\s*["\']'
         r'(?!'  # negative lookahead — skip known safe placeholders
         r'(?:'
         r'your-|test|demo|placeholder|example|changeme|xxx|none|null|false|true|admin123|'
         r'staff123|resident123|password|secret|\s*|___'
         r')'
         r')'
         r'[^"\'\n]{8,}'
         r'["\']'
     )),
    ('Gmail App Password Pattern',
     re.compile(r'\b[a-z]{4} [a-z]{4} [a-z]{4} [a-z]{4}\b')),
]

# Files that should NEVER be committed
DANGEROUS_FILE_PATTERNS = [
    re.compile(r'(^|/)\.env(\.(?!example)[a-zA-Z0-9_-]+)?$', re.I),
    re.compile(r'\.(pem|key|pkcs12|p12|pfx|kdbx)$', re.I),
    re.compile(r'(^|/)(id_rsa|id_ecdsa|id_ed25519)$', re.I),
    re.compile(r'(^|/)(credentials|secrets?)\.(json|ya?ml|txt|ini)$', re.I),
    re.compile(r'(^|/)service[-_]?account.*\.json$', re.I),
]

# Binary extensions to skip content scanning
BINARY_EXTS = frozenset([
    '.png', '.jpg', '.jpeg', '.gif', '.ico', '.webp', '.svg',
    '.woff', '.woff2', '.ttf', '.eot', '.otf',
    '.zip', '.gz', '.tar', '.7z', '.rar',
    '.mp3', '.mp4', '.avi', '.mov', '.wav',
    '.pdf', '.doc', '.docx', '.xls', '.xlsx',
    '.sqlite3', '.db', '.sqlite', '.pyc',
])

BANNER = """
+==============================================================+
|              BMS SECURITY CHECK FAILED                      |
|                                                             |
|  A possible secret or credential was detected.              |
|                                                             |
|  Remove the secret and use an environment variable or       |
|  secure secret manager instead.                             |
|                                                             |
|  The actual secret value will NOT be displayed.             |
+==============================================================+
"""


def is_binary(filename):
    _, ext = os.path.splitext(filename)
    return ext.lower() in BINARY_EXTS


def scan_content(content, filename='<unknown>'):
    """Return list of (rule_name, line_number) for detected secrets."""
    findings = []
    for line_no, line in enumerate(content.splitlines(), 1):
        stripped = line.strip()
        # Skip comments and safe patterns
        if stripped.startswith('#') or stripped.startswith('//'):
            continue
        for rule_name, pattern in SECRET_RULES:
            if pattern.search(line):
                findings.append((rule_name, filename, line_no))
    return findings


def scan_file_names(filenames):
    """Check filenames against dangerous patterns."""
    findings = []
    for f in filenames:
        for pat in DANGEROUS_FILE_PATTERNS:
            if pat.search(f):
                findings.append(('Dangerous File', f, 0))
                break
    return findings


def get_staged_files():
    """Get list of staged files and their contents."""
    res = subprocess.run(
        ['git', 'diff', '--cached', '--name-only', '--diff-filter=ACMR'],
        capture_output=True, text=True, encoding='utf-8', errors='ignore'
    )
    return [f.strip() for f in res.stdout.splitlines() if f.strip()]


def get_staged_content(filename):
    """Get staged content for a file."""
    res = subprocess.run(
        ['git', 'show', f':{filename}'],
        capture_output=True, text=True, encoding='utf-8', errors='ignore'
    )
    return res.stdout


def get_push_commits():
    """Get commits that would be pushed (not yet on remote)."""
    res = subprocess.run(
        ['git', 'log', '--format=%H', '@{push}..HEAD'],
        capture_output=True, text=True, encoding='utf-8', errors='ignore'
    )
    if res.returncode != 0:
        # Fallback: compare with origin/main
        res = subprocess.run(
            ['git', 'log', '--format=%H', 'origin/main..HEAD'],
            capture_output=True, text=True, encoding='utf-8', errors='ignore'
        )
    return [c.strip() for c in res.stdout.splitlines() if c.strip()]


def report_findings(findings, mode_label):
    """Print a safe report without revealing secret values."""
    if not findings:
        print(f"  [PASS] {mode_label}: No secrets detected.")
        return 0

    print(BANNER)
    print(f"  {mode_label} — {len(findings)} potential secret(s) detected:\n")
    for idx, (rule_name, filename, line_no) in enumerate(findings, 1):
        loc = f"line {line_no}" if line_no else "filename"
        print(f"    [{idx}] Detection:  {rule_name}")
        print(f"         File:       {filename}")
        print(f"         Location:   {loc}")
        print()
    print("  Action Required: Remove the secret and use an environment variable")
    print("                   or secure secret manager instead.\n")
    return 1


def cmd_staged(_args):
    """Pre-commit: scan staged files."""
    staged = get_staged_files()
    if not staged:
        print("  [PASS] No staged files to scan.")
        return 0

    all_findings = []
    all_findings.extend(scan_file_names(staged))

    for f in staged:
        if is_binary(f):
            continue
        content = get_staged_content(f)
        all_findings.extend(scan_content(content, f))

    return report_findings(all_findings, "Pre-Commit Secret Scan")


def cmd_env_guard(_args):
    """Block .env files from being staged."""
    staged = get_staged_files()
    env_files = [f for f in staged if re.match(r'^\.env(\.(?!example)[a-zA-Z0-9_-]+)?$', f, re.I)]
    if env_files:
        print(BANNER)
        print("  BLOCKED: The following .env file(s) are staged for commit:\n")
        for f in env_files:
            print(f"    - {f}")
        print("\n  Run: git reset HEAD <file> to unstage.")
        print("  .env files must NEVER be committed to version control.\n")
        return 1
    print("  [PASS] No .env files staged.")
    return 0


def cmd_pre_push(_args):
    """Pre-push: scan commits about to be pushed."""
    commits = get_push_commits()
    if not commits:
        print("  [PASS] No new commits to push.")
        return 0

    all_findings = []
    for commit in commits:
        # Get files changed in this commit
        res = subprocess.run(
            ['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', commit],
            capture_output=True, text=True, encoding='utf-8', errors='ignore'
        )
        files = [f.strip() for f in res.stdout.splitlines() if f.strip()]
        all_findings.extend(scan_file_names(files))

        # Get the diff content
        res = subprocess.run(
            ['git', 'diff-tree', '-p', commit],
            capture_output=True, text=True, encoding='utf-8', errors='ignore'
        )
        for line_no, line in enumerate(res.stdout.splitlines(), 1):
            if not line.startswith('+') or line.startswith('+++'):
                continue
            for rule_name, pattern in SECRET_RULES:
                if pattern.search(line):
                    all_findings.append((rule_name, f'commit:{commit[:8]}', line_no))

    return report_findings(all_findings, "Pre-Push Secret Scan")


def cmd_full(_args):
    """Full scan of working tree."""
    all_findings = []
    for dirpath, _dirnames, filenames in os.walk('.'):
        # Skip .git directory
        if '.git' in dirpath.split(os.sep):
            continue
        for fname in filenames:
            filepath = os.path.join(dirpath, fname)
            rel = os.path.relpath(filepath, '.')
            all_findings.extend(scan_file_names([rel]))
            if is_binary(rel):
                continue
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as fh:
                    content = fh.read()
                all_findings.extend(scan_content(content, rel))
            except (OSError, PermissionError):
                continue

    return report_findings(all_findings, "Full Working Tree Secret Scan")


def cmd_check_build(args):
    """Scan built/bundled frontend files for leaked secrets."""
    build_dir = args.build_dir or '.'
    if not os.path.isdir(build_dir):
        print(f"  [SKIP] Build directory not found: {build_dir}")
        return 0

    all_findings = []
    # Only server-side secrets should be checked in builds
    server_patterns = [
        ('DATABASE_URL in build', re.compile(r'(?i)DATABASE_URL\s*[:=]\s*["\'][^"\']+["\']')),
        ('DATABASE_PASSWORD in build', re.compile(r'(?i)DATABASE_PASSWORD\s*[:=]\s*["\'][^"\']+["\']')),
        ('GOOGLE_CLIENT_SECRET in build', re.compile(r'(?i)GOOGLE_CLIENT_SECRET\s*[:=]\s*["\'][^"\']+["\']')),
        ('JWT_SECRET in build', re.compile(r'(?i)JWT_SECRET\s*[:=]\s*["\'][^"\']+["\']')),
        ('SESSION_SECRET in build', re.compile(r'(?i)SESSION_SECRET\s*[:=]\s*["\'][^"\']+["\']')),
        ('SMTP_PASSWORD in build', re.compile(r'(?i)SMTP_PASS(?:WORD)?\s*[:=]\s*["\'][^"\']+["\']')),
    ] + list(SECRET_RULES)

    for dirpath, _dirnames, filenames in os.walk(build_dir):
        for fname in filenames:
            ext = os.path.splitext(fname)[1].lower()
            if ext not in ('.js', '.html', '.css', '.json', '.map', '.txt'):
                continue
            filepath = os.path.join(dirpath, fname)
            rel = os.path.relpath(filepath, build_dir)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as fh:
                    content = fh.read()
                for rule_name, pattern in server_patterns:
                    for match in pattern.finditer(content):
                        all_findings.append((rule_name, rel, 0))
            except (OSError, PermissionError):
                continue

    if all_findings:
        print(BANNER)
        print("  SECURITY CHECK FAILED:")
        print("  A possible server-side secret was detected in the production")
        print("  frontend bundle.\n")
        print("  Build blocked.\n")
        for idx, (rule_name, filename, _) in enumerate(all_findings, 1):
            print(f"    [{idx}] Detection:  {rule_name}")
            print(f"         File:       {filename}")
        print()
        return 1

    print(f"  [PASS] Build directory scan clean: {build_dir}")
    return 0


def main():
    parser = argparse.ArgumentParser(description='BMS Secret & Credential Scanner')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--staged', action='store_true', help='Scan staged files (pre-commit)')
    group.add_argument('--env-guard', action='store_true', help='Block .env files from commit')
    group.add_argument('--pre-push', action='store_true', help='Scan commits for push (pre-push)')
    group.add_argument('--full', action='store_true', help='Full working tree scan')
    group.add_argument('--check-build', action='store_true', help='Scan build directory for secrets')
    parser.add_argument('--build-dir', default=None, help='Build directory to scan')

    args = parser.parse_args()

    print("\n  +---------------------------------------------+")
    print("  |        BMS Security Gate -- Secret Scanner  |")
    print("  +---------------------------------------------+\n")

    if args.staged:
        sys.exit(cmd_staged(args))
    elif args.env_guard:
        sys.exit(cmd_env_guard(args))
    elif args.pre_push:
        sys.exit(cmd_pre_push(args))
    elif args.full:
        sys.exit(cmd_full(args))
    elif args.check_build:
        sys.exit(cmd_check_build(args))


if __name__ == '__main__':
    main()
