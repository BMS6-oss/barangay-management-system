"""Audit Git history for any committed secrets, credentials, or personal data.
Safely reports findings without revealing actual secret values.
"""
import re
import subprocess
import sys

# High-confidence secret detection rules
SECRET_RULES = [
    {
        'id': 'private_key',
        'name': 'Private Key (RSA/EC/DSA/OpenSSH/PGP)',
        'pattern': re.compile(r'-----BEGIN (?:[A-Z0-9_-]+ )?PRIVATE KEY-----')
    },
    {
        'id': 'aws_access_key',
        'name': 'AWS Access Key ID',
        'pattern': re.compile(r'\b(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b')
    },
    {
        'id': 'google_api_key',
        'name': 'Google Cloud / Maps / Firebase API Key',
        'pattern': re.compile(r'\bAIza[0-9A-Za-z\\-_]{35}\b')
    },
    {
        'id': 'github_token',
        'name': 'GitHub Personal Access / OAuth Token',
        'pattern': re.compile(r'\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{36,255}\b')
    },
    {
        'id': 'slack_token',
        'name': 'Slack API / Bot Token',
        'pattern': re.compile(r'\bxox[baprs]-[0-9a-zA-Z]{10,48}\b')
    },
    {
        'id': 'slack_webhook',
        'name': 'Slack Incoming Webhook URL',
        'pattern': re.compile(r'https:\/\/hooks\.slack\.com\/services\/T[0-9a-zA-Z]{8}\/B[0-9a-zA-Z]{8,12}\/[0-9a-zA-Z]{24}')
    },
    {
        'id': 'database_url_with_creds',
        'name': 'Database URI with Embedded Password',
        'pattern': re.compile(r'\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?):\/\/[^:\s]+:(?!(?:password|\$|%s|\{))[^\s@]+@[^\s]+', re.IGNORECASE)
    },
    {
        'id': 'stripe_key',
        'name': 'Stripe Secret / Restricted Key',
        'pattern': re.compile(r'\b(?:sk|rk)_(?:live|test)_[0-9a-zA-Z]{24,99}\b')
    },
    {
        'id': 'sendgrid_key',
        'name': 'SendGrid API Key',
        'pattern': re.compile(r'\bSG\.[a-zA-Z0-9_-]{22}\.[a-zA-Z0-9_-]{43}\b')
    },
    {
        'id': 'smtp_credentials',
        'name': 'SMTP User / Password assignment with value',
        'pattern': re.compile(r'(?i)\b(?:SMTP_PASSWORD|SMTP_PASS|GMAIL_APP_PASS(?:WORD)?)\s*[:=]\s*[\'\"](?!(?:your-|test|demo|placeholder|\s*|none|admin123))[^\'\"\n]{6,}[\'\"]')
    },
    {
        'id': 'google_oauth_secret',
        'name': 'Google OAuth Client Secret Assignment',
        'pattern': re.compile(r'(?i)\bGOOGLE_CLIENT_SECRET\s*[:=]\s*[\'\"](?!(?:your-|test|demo|placeholder|\s*|none))[GOCSPX-[0-9a-zA-Z_\-]{20,}|[0-9a-zA-Z_\-]{24,}][\'\"]')
    },
    {
        'id': 'jwt_secret',
        'name': 'JWT / Session Hardcoded Secret',
        'pattern': re.compile(r'(?i)\b(?:JWT_SECRET|SESSION_SECRET)\s*[:=]\s*[\'\"](?!(?:your-|test|demo|placeholder|\s*|none|secret))[0-9a-zA-Z_\-!@#$%^&*]{16,}[\'\"]')
    }
]

DANGEROUS_FILE_PATTERNS = [
    re.compile(r'(^|/)\.env(\.(?!example)[a-zA-Z0-9_-]+)?$', re.IGNORECASE),
    re.compile(r'\.(pem|key|pkcs12|p12|pfx|kdbx)$', re.IGNORECASE),
    re.compile(r'(^|/)(id_rsa|id_ecdsa|id_ed25519)(\.pub)?$', re.IGNORECASE),
    re.compile(r'(^|/)(credentials|secrets?)\.(json|ya?ml|txt|ini)$', re.IGNORECASE),
    re.compile(r'(^|/)(resident|residents|voters?|personal_data|db_dump|database_dump).*\.(csv|sql|dump|bak|sqlite3?)$', re.IGNORECASE)
]

def audit():
    # Get all commits
    res = subprocess.run(['git', 'rev-list', '--all'], capture_output=True, text=True, encoding='utf-8', errors='ignore')
    commits = [c.strip() for c in res.stdout.splitlines() if c.strip()]
    
    findings = []
    
    for commit in commits:
        # Check files added/modified in this commit
        cmd_files = ['git', 'diff-tree', '--no-commit-id', '--name-only', '-r', commit]
        f_res = subprocess.run(cmd_files, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        files = [f.strip() for f in f_res.stdout.splitlines() if f.strip()]
        
        for f in files:
            for d_pat in DANGEROUS_FILE_PATTERNS:
                if d_pat.search(f):
                    findings.append({
                        'commit': commit[:8],
                        'file': f,
                        'rule_id': 'dangerous_file',
                        'rule_name': f'Protected/Sensitive File: {f}',
                        'type': 'DANGEROUS_FILE'
                    })
        
        # Check diff content of this commit
        cmd_diff = ['git', 'show', '--format=%h %s', commit]
        d_res = subprocess.run(cmd_diff, capture_output=True, text=True, encoding='utf-8', errors='ignore')
        diff_text = d_res.stdout
        
        for line in diff_text.splitlines():
            if not line.startswith('+') or line.startswith('+++'):
                continue
            for rule in SECRET_RULES:
                if rule['pattern'].search(line):
                    # Redact secret: do not print line content or secret
                    findings.append({
                        'commit': commit[:8],
                        'file': 'git commit patch',
                        'rule_id': rule['id'],
                        'rule_name': rule['name'],
                        'type': 'SECRET_PATTERN'
                    })

    print("==================================================================")
    print("           BMS GIT HISTORY SECURITY AUDIT REPORT")
    print("==================================================================")
    print(f"Total commits inspected: {len(commits)}")
    print(f"Total findings: {len(findings)}")
    print("------------------------------------------------------------------")
    
    if not findings:
        print("[STATUS: CLEAN] No high-confidence secrets or prohibited files detected in repository history.")
        return 0
    else:
        print("[ALERT] The following potential exposures were detected in Git history:")
        for idx, item in enumerate(findings, 1):
            print(f"  [{idx}] Type:      {item['rule_name']}")
            print(f"      Commit:    {item['commit']}")
            print(f"      File:      {item['file']}")
            print(f"      Status:    Detected in history")
            print(f"      Action:    Rotate/Revoke if real; clean history with approval.")
        print("------------------------------------------------------------------")
        print("IMPORTANT: Actual secret values were not displayed.")
        return 1

if __name__ == '__main__':
    sys.exit(audit())
