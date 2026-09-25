"""Test Suite for Clean & Organized Barangay Welcome Page and Hero Photo Management."""
import base64
import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

BASE_URL = 'http://127.0.0.1:8000'

def req(path, method='GET', data=None, token=None):
    url = f'{BASE_URL}{path}'
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    encoded = json.dumps(data).encode() if data is not None else None
    request = urllib.request.Request(url, data=encoded, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8')
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {'error': body}

def run_tests():
    print("\n=== TESTING CLEAN WELCOME PAGE & HERO MANAGEMENT ===\n")
    passed = 0
    failed = 0

    def assert_true(condition, message):
        nonlocal passed, failed
        if condition:
            print(f"  [PASS] {message}")
            passed += 1
        else:
            print(f"  [FAIL] {message}")
            failed += 1

    # 1. Public Info has Hero Fields
    status, info = req('/api/public/info')
    assert_true(status == 200, "GET /api/public/info returns 200")
    assert_true('heroImage' in info, f"heroImage present in public info ({info.get('heroImage')})")
    assert_true('heroImageFocalPosition' in info, f"heroImageFocalPosition present ({info.get('heroImageFocalPosition')})")
    assert_true('stats' in info and 'residents' in info['stats'], "Community stats present in public info")

    # 2. Login as admin
    status, auth = req('/api/login', 'POST', {'username': 'admin', 'password': 'admin123'})
    assert_true(status == 200 and 'token' in auth, "Admin login successful")
    admin_token = auth['token']

    # 3. Login as resident (non-admin)
    status, res_auth = req('/api/login', 'POST', {'username': 'resident', 'password': 'resident123'})
    assert_true(status == 200 and 'token' in res_auth, "Resident login successful")
    res_token = res_auth['token']

    # 4. Role Guard: Resident cannot access GET /api/admin/hero-image
    status, err = req('/api/admin/hero-image', token=res_token)
    assert_true(status == 403, "Non-admin rejected from GET /api/admin/hero-image with 403")

    # 5. Role Guard: Resident cannot access POST /api/admin/hero-image
    status, err = req('/api/admin/hero-image', 'POST', {'focalPosition': 'top'}, token=res_token)
    assert_true(status == 403, "Non-admin rejected from POST /api/admin/hero-image with 403")

    # 6. Admin can GET /api/admin/hero-image
    status, hero_settings = req('/api/admin/hero-image', token=admin_token)
    assert_true(status == 200, f"Admin GET /api/admin/hero-image returns 200: {hero_settings.get('heroImage')}")

    # 7. Admin updates focal position to 'bottom'
    status, update_res = req('/api/admin/hero-image', 'POST', {'focalPosition': 'bottom'}, token=admin_token)
    assert_true(status == 200 and update_res.get('heroImageFocalPosition') == 'bottom', "Admin updated focal position to 'bottom'")

    # Verify public info reflects 'bottom'
    status, info = req('/api/public/info')
    assert_true(info.get('heroImageFocalPosition') == 'bottom', "Public info reflects updated focal position 'bottom'")

    # 8. Admin uploads a real test image (using assets/hero2.jpg)
    hero2_bytes = Path('assets/hero2.jpg').read_bytes()
    data_uri = 'data:image/jpeg;base64,' + base64.b64encode(hero2_bytes).decode('ascii')
    status, upload_res = req('/api/admin/hero-image', 'POST', {
        'data': data_uri,
        'filename': 'new_hero_hall.jpg',
        'focalPosition': 'top'
    }, token=admin_token)
    assert_true(status == 200 and upload_res.get('ok') is True, f"Admin uploaded hero photo successfully: {upload_res.get('heroImage')}")
    uploaded_path = upload_res.get('heroImage', '')
    assert_true('uploads/branding/hero' in uploaded_path or 'uploads/protected/hero' in uploaded_path, f"Uploaded path is stored in uploads: {uploaded_path}")

    # 9. Verify uploaded file is statically served
    clean_rel = uploaded_path.lstrip('./')
    test_req = urllib.request.Request(f"{BASE_URL}/{clean_rel}")
    with urllib.request.urlopen(test_req) as s_res:
        assert_true(s_res.status == 200 and s_res.headers.get('Content-Type') == 'image/jpeg', "Uploaded image is served statically by BMS server with 200 and correct Content-Type")

    # 10. Admin resets hero image to default
    status, reset_res = req('/api/admin/hero-image', 'POST', {'reset': True}, token=admin_token)
    assert_true(status == 200 and reset_res.get('heroImage') == './assets/hero1.jpg', "Admin reset hero image back to default ./assets/hero1.jpg")

    status, info = req('/api/public/info')
    assert_true(info.get('heroImage') == './assets/hero1.jpg', "Public info reflects reset default hero image")

    # 11. Verify index.html contains no 3D elements and contains new photo hero elements
    index_content = Path('index.html').read_text(encoding='utf-8')
    assert_true('three.min.js' not in index_content, "index.html has NO three.min.js script tag")
    assert_true('welcome-3d.js' not in index_content, "index.html has NO welcome-3d.js script tag")
    assert_true('BMS3D' not in index_content, "index.html has NO BMS3D references")
    assert_true('threeHeroCanvas' not in index_content, "index.html has NO threeHeroCanvas element")
    assert_true('gov-topbar' in index_content, "index.html contains gov-topbar (top utility bar)")
    assert_true('hero-photo-section' in index_content or 'heroBgImage' in index_content, "index.html contains heroBgImage photo hero element")
    assert_true('adminHeroPreview' in index_content, "index.html contains adminHeroPreview management element")
    assert_true('pstClock' in index_content, "index.html contains live Philippine Standard Time (pstClock) element")

    # 12. Verify welcome.css contains photo hero and button animations
    css_content = Path('css/welcome.css').read_text(encoding='utf-8')
    assert_true('threeHeroCanvas' not in css_content, "css/welcome.css has NO threeHeroCanvas styles")
    assert_true('hero-photo-section' in css_content, "css/welcome.css contains hero-photo-section styles")
    assert_true('heroSlowZoom' in css_content, "css/welcome.css contains subtle slow zoom animation for hero")
    assert_true('admin-hero-preview-box' in css_content, "css/welcome.css contains admin hero preview styles")

    print(f"\n========================================")
    print(f"HERO TEST RESULTS: {passed} PASSED, {failed} FAILED")
    print(f"========================================\n")
    if failed > 0:
        sys.exit(1)

if __name__ == '__main__':
    run_tests()
