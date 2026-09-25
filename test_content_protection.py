"""Test Suite for BMS Content Protection & Image Security System."""
import base64
import io
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

def create_sample_jpeg_base64():
    """Create a minimal valid JPEG in-memory for testing image upload."""
    try:
        from PIL import Image
        img = Image.new('RGB', (100, 100), color=(73, 109, 137))
        buffer = io.BytesIO()
        img.save(buffer, format='JPEG')
        return base64.b64encode(buffer.getvalue()).decode('ascii')
    except Exception:
        # Fallback 1x1 minimal JPEG
        minimal_jpg = b'\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00H\x00H\x00\x00\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\' ",#\x1c\x1c(7),01444\x1f\'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9'
        return base64.b64encode(minimal_jpg).decode('ascii')

def run_tests():
    print("\n=== TESTING BMS CONTENT PROTECTION & IMAGE SECURITY ===\n")
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

    # 1. Public Protection Settings
    status, pub_ps = req('/api/public/protection-settings')
    assert_true(status == 200, f"GET /api/public/protection-settings returns 200 (status={status})")
    assert_true('enabled' in pub_ps, f"Public settings includes 'enabled': {pub_ps.get('enabled')}")
    assert_true('watermarkEnabled' in pub_ps, f"Public settings includes 'watermarkEnabled': {pub_ps.get('watermarkEnabled')}")
    assert_true('watermarkText' in pub_ps, f"Public settings includes watermarkText: '{pub_ps.get('watermarkText')}'")
    assert_true('watermarkOpacity' in pub_ps, f"Public settings includes watermarkOpacity: {pub_ps.get('watermarkOpacity')}")
    assert_true('watermarkPosition' in pub_ps, f"Public settings includes watermarkPosition: {pub_ps.get('watermarkPosition')}")
    assert_true('rightClickProtection' in pub_ps, f"Public settings includes rightClickProtection: {pub_ps.get('rightClickProtection')}")
    assert_true('dragPrevention' in pub_ps, f"Public settings includes dragPrevention: {pub_ps.get('dragPrevention')}")

    # 2. Login as admin
    status, auth = req('/api/login', 'POST', {'username': 'admin', 'password': 'admin123'})
    assert_true(status == 200 and 'token' in auth, "Admin login successful")
    admin_token = auth['token']

    # 3. Login as resident (non-admin)
    status, res_auth = req('/api/login', 'POST', {'username': 'resident', 'password': 'resident123'})
    assert_true(status == 200 and 'token' in res_auth, "Resident login successful")
    res_token = res_auth['token']

    # 4. Role Guard: Resident cannot access GET /api/admin/protection-settings
    status, res_ps = req('/api/admin/protection-settings', 'GET', token=res_token)
    assert_true(status == 403, f"Resident GET /api/admin/protection-settings blocked with 403 (got {status})")

    # 5. Role Guard: Resident cannot access PUT /api/admin/protection-settings
    status, res_put = req('/api/admin/protection-settings', 'PUT', {'enabled': False}, token=res_token)
    assert_true(status == 403, f"Resident PUT /api/admin/protection-settings blocked with 403 (got {status})")

    # 6. Admin can read protection settings
    status, admin_ps = req('/api/admin/protection-settings', 'GET', token=admin_token)
    assert_true(status == 200, "Admin GET /api/admin/protection-settings returns 200")
    assert_true('pillowAvailable' in admin_ps, f"pillowAvailable flag present: {admin_ps.get('pillowAvailable')}")

    # 7. Admin updates protection settings
    new_settings = {
        'enabled': True,
        'watermarkEnabled': True,
        'watermarkText': 'OFFICIAL TEST WATERMARK',
        'watermarkOpacity': 0.35,
        'watermarkPosition': 'bottom-right',
        'rightClickProtection': True,
        'dragPrevention': True
    }
    status, save_res = req('/api/admin/protection-settings', 'PUT', new_settings, token=admin_token)
    assert_true(status == 200 and save_res.get('ok') is True, f"Admin PUT /api/admin/protection-settings returns 200 ok (got {status})")

    # Verify settings persisted
    status, updated_pub = req('/api/public/protection-settings')
    assert_true(status == 200 and updated_pub.get('watermarkText') == 'OFFICIAL TEST WATERMARK',
                f"Public protection settings updated to 'OFFICIAL TEST WATERMARK' (got '{updated_pub.get('watermarkText')}')")
    assert_true(updated_pub.get('watermarkOpacity') == 0.35,
                f"Watermark opacity updated to 0.35 (got {updated_pub.get('watermarkOpacity')})")

    # 8. Restore standard watermark text
    restore_settings = {
        'enabled': True,
        'watermarkEnabled': True,
        'watermarkText': 'OFFICIAL BARANGAY WEBSITE',
        'watermarkOpacity': 0.30,
        'watermarkPosition': 'bottom-right',
        'rightClickProtection': True,
        'dragPrevention': True
    }
    status, _ = req('/api/admin/protection-settings', 'PUT', restore_settings, token=admin_token)
    assert_true(status == 200, "Protection settings restored to defaults")

    # 9. Test Hero Image Upload with Watermarking
    b64_img = create_sample_jpeg_base64()
    upload_payload = {
        'filename': 'test_hero.jpg',
        'data': f"data:image/jpeg;base64,{b64_img}",
        'focalPosition': 'center'
    }
    status, upload_res = req('/api/admin/hero-image', 'POST', upload_payload, token=admin_token)
    assert_true(status == 200 and upload_res.get('ok') is True, f"Admin hero image upload returns 200 ok (got {status})")
    assert_true('heroImage' in upload_res, f"heroImage returned in upload response: {upload_res.get('heroImage')}")

    # Check if public info now points to the watermarked image
    status, info = req('/api/public/info')
    assert_true(status == 200, "GET /api/public/info returns 200 after hero upload")
    public_hero = info.get('heroImage', '')
    print(f"     Public hero image path: {public_hero}")
    if admin_ps.get('pillowAvailable'):
        assert_true('uploads/protected/hero/' in public_hero,
                    f"Public hero points to protected watermarked version: {public_hero}")
        # Verify watermarked file physically exists
        rel_path = public_hero.lstrip('./').replace('/', os.sep)
        assert_true(os.path.exists(rel_path), f"Watermarked image file exists on disk: {rel_path}")

    # 10. Reset hero image back to default
    status, reset_res = req('/api/admin/hero-image', 'POST', {'reset': True}, token=admin_token)
    assert_true(status == 200 and reset_res.get('ok') is True, f"Hero image reset returns 200 ok (got {status})")

    status, info_after_reset = req('/api/public/info')
    assert_true(info_after_reset.get('heroImage') == './assets/hero1.jpg',
                f"Hero image reverted to default: {info_after_reset.get('heroImage')}")

    # 11. Static files check
    def check_static(path, expected_status=200):
        url = f'{BASE_URL}{path}'
        req_obj = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req_obj) as resp:
                return resp.status
        except urllib.error.HTTPError as e:
            return e.code

    assert_true(check_static('/js/content-protection.js') == 200, "Static GET /js/content-protection.js returns 200")
    assert_true(check_static('/css/welcome.css') == 200, "Static GET /css/welcome.css returns 200")

    print(f"\nSummary: {passed} passed, {failed} failed\n")
    if failed > 0:
        sys.exit(1)

if __name__ == '__main__':
    run_tests()
