# Barangay Management System (BMS) — Security Policy & Defense-in-Depth Gates

## 1. Overview & Policy

This repository enforces a strict, defense-in-depth security gate to prevent credentials, server secrets, private keys, database connection strings, and resident personal data (Data Privacy Act of 2012) from ever being committed to Git or pushed to remote repositories.

### Absolute Rules:
1. **Never commit `.env` or any environment override files (`.env.*`, `.env.local`, `.env.production`).** Only `.env.example` with sanitized placeholders is allowed.
2. **Never hardcode secrets, API keys, passwords, or tokens in source code or frontend bundles.**
3. **Never commit cryptographic keys, certificates, or keystores (`*.pem`, `*.key`, `*.p12`, `id_rsa`).**
4. **Never commit resident data, database files, or backups (`*.sqlite3`, `*resident*.csv`, `*.dump`).**
5. **Never display actual secret values in error messages, logs, reports, or console outputs.**

---

## 2. Multi-Layer Defense-in-Depth Architecture

The BMS secret protection system operates across multiple distinct boundaries:

| Layer | Component | Mechanism | Action |
|---|---|---|---|
| **Layer 1: Git Ignore** | `.gitignore` | Prohibits `.env*`, `secrets/`, `*.pem`, `*.key`, `*.sqlite3`, resident exports | Untracked by default |
| **Layer 2: Docker Ignore** | `.dockerignore` | Excludes secrets, local databases, docs, and git files from build context | Prevents container leakage |
| **Layer 3: Pre-Commit Hook** | `.pre-commit-config.yaml` | Runs Yelp `detect-secrets`, `detect-private-key`, `bms-secret-scan`, `bms-env-guard` | Blocks `git commit` |
| **Layer 4: Pre-Push Hook** | Git hook & `scan_secrets.py --pre-push` | Inspects outgoing commits not yet on remote | Blocks `git push` |
| **Layer 5: Build Scanner** | `scan_secrets.py --check-build` | Inspects HTML/JS/CSS assets for server-side secret patterns | Blocks deployment build |
| **Layer 6: Unified Health Check**| `scripts/security/security_check.py` | 7-point audit (.env, keys, patterns, bundle, docker, render, logs) | Non-zero exit code on failure |
| **Layer 7: CI/CD Pipeline** | `.github/workflows/security.yml` | Automated checks on push & pull request to `main` branch | Blocks PR merge |
| **Layer 8: Baseline & Audit** | `.secrets.baseline` & `audit_history.py` | Tracks approved baselines, audits entire commit tree | Verifies historical cleanliness |
| **Layer 9: Cloud Isolation** | `render.yaml` | All secrets configured with `sync: false` in Render Dashboard | Keeps secrets out of repo |

---

## 3. Local Developer Setup

Every developer working on BMS must install and verify local hooks:

### Step 1: Install Dependencies
```bash
pip install -r requirements.txt
pip install pre-commit detect-secrets
```

### Step 2: Install Git Hooks
```bash
pre-commit install
pre-commit install --hook-type pre-push
```

### Step 3: Run the Security Verification
```bash
python scripts/security/security_check.py
```

Expected result: All 7 security checks report `[PASS]` with exit code `0`.

---

## 4. Security Tooling CLI Reference

### `scripts/security/scan_secrets.py`
The primary secret scanning engine. Never displays secret strings.

- **Pre-commit staged scan:**
  ```bash
  python scripts/security/scan_secrets.py --staged
  ```
- **Environment file guard:**
  ```bash
  python scripts/security/scan_secrets.py --env-guard
  ```
- **Pre-push outgoing commits scan:**
  ```bash
  python scripts/security/scan_secrets.py --pre-push
  ```
- **Full repository scan:**
  ```bash
  python scripts/security/scan_secrets.py --full
  ```
- **Frontend bundle scan:**
  ```bash
  python scripts/security/scan_secrets.py --check-build --build-dir .
  ```

### `scripts/security/audit_history.py`
Scans entire Git commit history back to the root commit for committed secrets or dangerous files:
```bash
python scripts/security/audit_history.py
```

### `scripts/security/security_check.py`
Unified test suite running all local validations:
```bash
python scripts/security/security_check.py
```

---

## 5. GitHub Repository Settings (Action Required on GitHub.com)

To complete the defense-in-depth protection, the repository administrator must enable the following on GitHub:

1. **GitHub Secret Scanning & Push Protection:**
   - Navigate to: **Settings** -> **Code security and analysis**.
   - Under **Secret scanning**, click **Enable**.
   - Under **Push protection**, click **Enable**.
   - *Push protection prevents any developer from pushing known secrets directly to GitHub.*

2. **Branch Protection for `main`:**
   - Navigate to: **Settings** -> **Branches** -> **Add branch ruleset / protection rule**.
   - Branch name pattern: `main`
   - Check **Require a pull request before merging**.
   - Check **Require status checks to pass before merging**:
     - Select `BMS Security Gate & Secret Scan` (defined in `.github/workflows/security.yml`).
   - Check **Do not allow bypassing the above settings**.

---

## 6. Deployment & Secret Management

### Render Configuration
- **Never put secrets into `render.yaml`.**
- All production credentials (`GOOGLE_CLIENT_SECRET`, database passwords, etc.) must be defined in the **Render Dashboard -> Environment Variables**.
- `render.yaml` sets `sync: false` for all secret keys, guaranteeing Render will prompt the admin in the dashboard without storing them in Git.

### Docker Image Safety
- The `Dockerfile` uses an explicit, whitelist-style `COPY` strategy (`COPY server.py config.py ...`).
- `.dockerignore` prevents `.env`, `*.sqlite3`, `*.pem`, `*.key`, and secret directories from entering the Docker build context.
- The container runs under a non-root `appuser` (UID 10001).

---

## 7. Handling False Positives

If a legitimate placeholder or test dummy value triggers `detect-secrets`:

1. **Option A: Inline allowlist pragma:**
   Add `# pragma: allowlist secret` at the end of the line containing the dummy value.
2. **Option B: Update the baseline:**
   ```bash
   detect-secrets scan --baseline .secrets.baseline
   ```
   Inspect `.secrets.baseline` using `git diff` to ensure no real secrets were captured before committing the updated baseline.

---

## 8. Incident Response: What to Do If a Secret is Exposed

If a credential or secret was ever pushed to a remote repository:

1. **IMMEDIATELY REVOKE AND ROTATE THE SECRET:**
   - Consider the exposed secret fully compromised.
   - Immediately regenerate the API key, database password, or OAuth client secret in the respective service console (Google Cloud, Render, etc.).
   - Update production environment variables in the Render Dashboard.
2. **AUDIT LOGS:**
   - Check provider access logs for unauthorized use during the window of exposure.
3. **PURGE REPOSITORY HISTORY (If authorized):**
   - Use `git-filter-repo` or BFG Repo-Cleaner to rewrite history and expunge the secret blob.
   - Force-push with lease after team coordination.
   - Re-verify repository with `python scripts/security/audit_history.py`.
