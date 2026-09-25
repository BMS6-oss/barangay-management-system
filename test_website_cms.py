#!/usr/bin/env python3
"""
Comprehensive automated test suite for BMS Dynamic Welcome Page & Admin CMS.
Tests all public endpoints, admin officials CRUD, gallery CRUD, website settings,
resident profile photo security, and role-based permissions.
"""
import base64
import json
import os
import sys
import unittest
import urllib.request
import urllib.error
import http.cookiejar

BASE_URL = "http://127.0.0.1:8000"

# 1x1 transparent PNG base64
TINY_PNG_BASE64 = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

class BMSWebsiteCMSTests(unittest.TestCase):
    def setUp(self):
        self.cj = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cj))

    def _request(self, path, method="GET", data=None, token=None):
        url = f"{BASE_URL}{path}"
        headers = {}
        payload = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(data).encode("utf-8")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with self.opener.open(req, timeout=10) as resp:
                code = resp.getcode()
                body = resp.read().decode("utf-8")
                try:
                    return code, json.loads(body)
                except Exception:
                    return code, body
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                return e.code, json.loads(err_body)
            except Exception:
                return e.code, err_body

    def _login(self, username, password):
        code, body = self._request("/api/login", method="POST", data={"username": username, "password": password})
        self.assertEqual(code, 200, f"Login failed for {username}: {body}")
        token = body.get("token") or body.get("sessionToken") or body.get("accessToken")
        return token

    # ================= 1. PUBLIC ENDPOINTS =================
    def test_01_public_info_cms_fields(self):
        code, data = self._request("/api/public/info")
        self.assertEqual(code, 200)
        self.assertIn("barangayName", data)
        self.assertIn("welcomeBadge", data)
        self.assertIn("welcomeTitle", data)
        self.assertIn("welcomeSubtitle", data)
        self.assertIn("heroOverlayOpacity", data)
        self.assertIn("btnPortalLabel", data)
        self.assertIn("btnServicesLabel", data)
        self.assertIn("contactAddress", data)
        self.assertIn("contactPhone", data)
        self.assertIn("contactEmail", data)
        self.assertIn("emergencyHotline", data)
        self.assertIn("stats", data)
        self.assertIn("officials", data)
        self.assertIn("gallery", data)

    def test_02_public_officials(self):
        code, officials = self._request("/api/public/officials")
        self.assertEqual(code, 200)
        self.assertIsInstance(officials, list)
        self.assertGreaterEqual(len(officials), 1)
        first = officials[0]
        self.assertIn("name", first)
        self.assertIn("position", first)
        self.assertIn("display_order", first)

    def test_03_public_gallery(self):
        code, gallery = self._request("/api/public/gallery")
        self.assertEqual(code, 200)
        self.assertIsInstance(gallery, list)
        self.assertGreaterEqual(len(gallery), 1)
        first = gallery[0]
        self.assertIn("caption", first)
        self.assertIn("public_path", first)

    # ================= 2. ADMIN OFFICIALS CRUD =================
    def test_04_admin_officials_crud_and_reorder(self):
        admin_token = self._login("admin", "admin")

        # 1. List
        code, officials = self._request("/api/admin/officials?status=all", token=admin_token)
        self.assertEqual(code, 200)
        initial_count = len(officials)

        # 2. Create
        new_official = {
            "name": "Hon. Test Kagawad",
            "position": "Barangay Kagawad",
            "committee": "Committee on Youth & Sports",
            "term_years": "2023 - 2026",
            "display_order": 99,
            "status": "active",
            "is_visible": True,
            "photo": TINY_PNG_BASE64
        }
        code, created = self._request("/api/admin/officials", method="POST", data=new_official, token=admin_token)
        self.assertEqual(code, 201)
        self.assertIn("official", created)
        official_id = created["official"]["id"]
        self.assertEqual(created["official"]["name"], "Hon. Test Kagawad")
        self.assertTrue(bool(created["official"]["photo_path"]))

        # 3. Verify in public list
        code, pub_officials = self._request("/api/public/officials")
        self.assertTrue(any(o["id"] == official_id for o in pub_officials))

        # 4. Update
        update_data = {
            "name": "Hon. Test Kagawad Updated",
            "committee": "Committee on Education",
            "display_order": 95,
            "is_visible": True
        }
        code, updated = self._request(f"/api/admin/officials/{official_id}", method="PUT", data=update_data, token=admin_token)
        self.assertEqual(code, 200)
        self.assertEqual(updated["official"]["name"], "Hon. Test Kagawad Updated")

        # 5. Archive
        code, arch = self._request(f"/api/admin/officials/{official_id}/archive", method="POST", token=admin_token)
        self.assertEqual(code, 200)
        self.assertEqual(arch["official"]["status"], "archived")

        # Verify hidden from public list
        code, pub_officials = self._request("/api/public/officials")
        self.assertFalse(any(o["id"] == official_id for o in pub_officials))

        # 6. Restore
        code, rest = self._request(f"/api/admin/officials/{official_id}/restore", method="POST", token=admin_token)
        self.assertEqual(code, 200)
        self.assertEqual(rest["official"]["status"], "active")

        # 7. Reorder
        code, reorder_res = self._request("/api/admin/officials/reorder", method="PUT", data={"items": [{"id": official_id, "display_order": 500}]}, token=admin_token)
        self.assertEqual(code, 200)

        # 8. Delete
        code, del_res = self._request(f"/api/admin/officials/{official_id}", method="DELETE", token=admin_token)
        self.assertEqual(code, 200)

        # Verify deleted
        code, final_officials = self._request("/api/admin/officials?status=all", token=admin_token)
        self.assertEqual(len(final_officials), initial_count)

    # ================= 3. ADMIN GALLERY CRUD =================
    def test_05_admin_gallery_crud(self):
        admin_token = self._login("admin", "admin")

        # 1. Create photo
        gallery_payload = {
            "caption": "Automated Test Community Project",
            "description": "High resolution community testing photograph",
            "display_order": 90,
            "status": "active",
            "is_visible": True,
            "photo": TINY_PNG_BASE64
        }
        code, created = self._request("/api/admin/gallery", method="POST", data=gallery_payload, token=admin_token)
        self.assertEqual(code, 201)
        self.assertIn("item", created)
        gallery_id = created["item"]["id"]
        self.assertTrue(bool(created["item"]["public_path"]))

        # 2. Verify in public gallery
        code, pub_gallery = self._request("/api/public/gallery")
        self.assertTrue(any(g["id"] == gallery_id for g in pub_gallery))

        # 3. Update
        code, updated = self._request(f"/api/admin/gallery/{gallery_id}", method="PUT", data={"caption": "Updated Test Caption"}, token=admin_token)
        self.assertEqual(code, 200)
        self.assertEqual(updated["item"]["caption"], "Updated Test Caption")

        # 4. Archive
        code, arch = self._request(f"/api/admin/gallery/{gallery_id}/archive", method="POST", token=admin_token)
        self.assertEqual(code, 200)

        # Verify hidden from public
        code, pub_gallery = self._request("/api/public/gallery")
        self.assertFalse(any(g["id"] == gallery_id for g in pub_gallery))

        # 5. Restore
        code, rest = self._request(f"/api/admin/gallery/{gallery_id}/restore", method="POST", token=admin_token)
        self.assertEqual(code, 200)

        # 6. Delete
        code, del_res = self._request(f"/api/admin/gallery/{gallery_id}", method="DELETE", token=admin_token)
        self.assertEqual(code, 200)

    # ================= 4. WEBSITE SETTINGS =================
    def test_06_website_settings(self):
        admin_token = self._login("admin", "admin")

        # 1. Get current settings
        code, settings = self._request("/api/admin/website-settings", token=admin_token)
        self.assertEqual(code, 200)

        # 2. Update settings
        update_payload = {
            "welcome_badge": "Test Portal Badge 2026",
            "welcome_title": "WELCOME TO OUR TEST COMMUNITY",
            "welcome_subtitle": "Testing dynamic database driven mission statement",
            "hero_overlay_opacity": 82,
            "contact_address": "Test Street 123, Poblacion",
            "contact_phone": "(02) 9999-8888",
            "emergency_hotline": "911-TEST",
            "btn_portal_label": "ENTER SYSTEM NOW"
        }
        code, result = self._request("/api/admin/website-settings", method="PUT", data=update_payload, token=admin_token)
        self.assertEqual(code, 200)

        # 3. Verify public info reflects update
        code, info = self._request("/api/public/info")
        self.assertEqual(code, 200)
        self.assertEqual(info["welcomeBadge"], "Test Portal Badge 2026")
        self.assertEqual(info["welcomeTitle"], "WELCOME TO OUR TEST COMMUNITY")
        self.assertEqual(info["heroOverlayOpacity"], 82)
        self.assertEqual(info["contactAddress"], "Test Street 123, Poblacion")
        self.assertEqual(info["contactPhone"], "(02) 9999-8888")
        self.assertEqual(info["emergencyHotline"], "911-TEST")
        self.assertEqual(info["btnPortalLabel"], "ENTER SYSTEM NOW")

    # ================= 5. RESIDENT PROFILE PHOTO =================
    def test_07_resident_profile_photo_security_and_storage(self):
        # 1. Login as resident (resident)
        res_token = self._login("resident", "resident")

        # 2. Upload profile photo
        code, upload_res = self._request("/api/resident/profile-photo", method="POST", data={"photo": TINY_PNG_BASE64}, token=res_token)
        self.assertEqual(code, 200, f"Upload failed: {upload_res}")
        profile_path = upload_res.get("profile_image")
        self.assertTrue(bool(profile_path))
        self.assertIn("uploads/residents/", profile_path)

        # 3. Check profile endpoint
        code, prof = self._request("/api/my-profile", token=res_token)
        self.assertEqual(code, 200)
        self.assertEqual(prof["profile"]["profile_image"], profile_path)

        # 4. Security check: unauthenticated GET to resident profile picture must return 401
        url = f"{BASE_URL}/{profile_path.lstrip('/')}"
        req_unauth = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req_unauth) as resp:
                status_code = resp.getcode()
        except urllib.error.HTTPError as e:
            status_code = e.code
        self.assertEqual(status_code, 401, "Resident photo was served without session authentication!")

        # 5. Authenticated GET to resident profile picture must return 200
        req_auth = urllib.request.Request(url, headers={"Authorization": f"Bearer {res_token}"}, method="GET")
        with urllib.request.urlopen(req_auth) as resp:
            self.assertEqual(resp.getcode(), 200)
            img_bytes = resp.read()
            self.assertGreater(len(img_bytes), 0)

        # 6. Delete profile photo
        code, del_res = self._request("/api/resident/profile-photo", method="DELETE", token=res_token)
        self.assertEqual(code, 200)

        # Verify profile image removed
        code, prof_after = self._request("/api/my-profile", token=res_token)
        self.assertFalse(bool(prof_after["profile"]["profile_image"]))

    # ================= 6. ROLE PERMISSIONS & VALIDATION =================
    def test_08_role_permissions_and_validation(self):
        res_token = self._login("resident", "resident")

        # Resident attempting to modify officials must get 403 Forbidden
        code, err = self._request("/api/admin/officials", method="POST", data={"name": "Fake Official", "position": "Captain"}, token=res_token)
        self.assertEqual(code, 403)

        # Resident attempting to modify gallery must get 403 Forbidden
        code, err = self._request("/api/admin/gallery", method="POST", data={"caption": "Fake Gallery"}, token=res_token)
        self.assertEqual(code, 403)

        # Resident attempting to update website settings must get 403 Forbidden
        code, err = self._request("/api/admin/website-settings", method="PUT", data={"welcome_title": "Hacked"}, token=res_token)
        self.assertEqual(code, 403)

        # Admin uploading invalid file (not an image) must get 400 Bad Request
        admin_token = self._login("admin", "admin")
        bad_base64 = "data:text/plain;base64," + base64.b64encode(b"hello text file").decode("utf-8")
        code, err = self._request("/api/admin/officials", method="POST", data={"name": "Bad Image", "position": "Staff", "photo": bad_base64}, token=admin_token)
        self.assertEqual(code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
