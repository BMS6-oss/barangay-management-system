import urllib.request
import json
import sqlite3
import datetime
import sys

BASE_URL = "http://127.0.0.1:8000"

def request(endpoint, method="GET", data=None, token=None):
    url = BASE_URL + endpoint
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as resp:
            resp_body = resp.read().decode("utf-8").strip()
            try:
                return resp.status, json.loads(resp_body) if resp_body else {}
            except Exception:
                return resp.status, {"_raw": resp_body}
    except urllib.error.HTTPError as e:
        resp_body = e.read().decode("utf-8").strip()
        try:
            return e.code, json.loads(resp_body) if resp_body else {"error": str(e)}
        except Exception:
            return e.code, {"error": resp_body or str(e)}

def run_tests():
    print("========================================")
    print("RUNNING PUNONG BARANGAY ACCEPTANCE TESTS")
    print("========================================")
    passed = 0
    total = 12

    # 0. Setup / Login users
    # Admin login
    status, res = request("/api/login", "POST", {"username": "admin", "password": "admin"})
    assert status == 200, f"Admin login failed: {res}"
    admin_token = res["token"]
    admin_id = res["user"]["id"]

    # Punong Barangay login
    status, res = request("/api/login", "POST", {"username": "captain", "password": "captain123"})
    assert status == 200, f"PB login failed: {res}"
    pb_token = res["token"]
    pb_id = res["user"]["id"]
    print(f"Logged in: PB (ID {pb_id}), Admin (ID {admin_id})")

    # Staff login
    status, res = request("/api/login", "POST", {"username": "staff", "password": "staff"})
    assert status == 200, f"Staff login failed: {res}"
    staff_token = res["token"]
    staff_id = res["user"]["id"]
    print(f"Logged in: Staff (ID {staff_id})")

    # TEST 1: PB sends message to Staff -> Staff receives message in SQLite database
    print("\n--- TEST 1: PB sends message to Staff ---")
    msg_payload = {
        "recipient_type": "STAFF",
        "recipient_id": staff_id,
        "message_type": "URGENT",
        "title": "Clean-up Drive Coordination",
        "body": "Please coordinate with Zone 1 leaders for tomorrow's clean-up."
    }
    status, res = request("/api/punong-barangay/messages", "POST", msg_payload, pb_token)
    assert status in (200, 201) and (res.get("success") or res.get("ok")), f"Send message failed: {res}"
    conv_id = res.get("conversation_id") or res.get("conversationId")
    assert conv_id, "No conversation_id returned"

    # Verify directly in SQLite database
    conn = sqlite3.connect("bms.sqlite3")
    c = conn.cursor()
    c.execute("SELECT id, subject, body, message_type, sender_id FROM messages WHERE conversation_id = ? ORDER BY id DESC LIMIT 1", (conv_id,))
    msg_row = c.fetchone()
    assert msg_row is not None, "Message not found in SQLite"
    assert msg_row[1] == msg_payload["title"] or msg_row[2] == msg_payload["body"]
    assert msg_row[3] == "URGENT"
    assert msg_row[4] == pb_id

    # Verify recipient received it in SQLite
    c.execute("SELECT recipient_id, is_read FROM message_recipients WHERE message_id = ?", (msg_row[0],))
    recip_row = c.fetchone()
    assert recip_row is not None and recip_row[0] == staff_id, "Recipient record missing in SQLite"
    conn.close()

    # Also verify Staff API sees the conversation
    status, res = request("/api/messages", "GET", token=staff_token)
    assert status == 200, f"Staff list messages failed: {res}"
    convs = res if isinstance(res, list) else res.get("conversations", [])
    staff_conv = next((c for c in convs if c["id"] == conv_id), None)
    assert staff_conv is not None, "Staff did not receive conversation in API"
    print("PASS: TEST 1 - PB sent message, Staff received in SQLite & API.")
    passed += 1

    # TEST 2: Staff replies to PB -> PB receives reply in thread
    print("\n--- TEST 2: Staff replies to PB ---")
    reply_payload = {
        "body": "Understood, Captain. I am contacting Zone 1 leaders right now."
    }
    status, res = request(f"/api/messages/{conv_id}/reply", "POST", reply_payload, staff_token)
    assert status in (200, 201) and (res.get("success") or res.get("ok")), f"Staff reply failed: {res}"

    # PB reads conversation thread
    status, res = request(f"/api/messages/{conv_id}", "GET", token=pb_token)
    assert status == 200, f"PB get conversation failed: {res}"
    messages = res.get("messages", [])
    assert len(messages) >= 2, f"Expected at least 2 messages in thread, got {len(messages)}"
    last_msg = messages[-1]
    assert last_msg["sender_id"] == staff_id
    assert last_msg["body"] == reply_payload["body"]
    print("PASS: TEST 2 - Staff replied, PB received reply in thread.")
    passed += 1

    # TEST 3: PB creates task -> Staff assigned sees task on dashboard
    print("\n--- TEST 3: PB creates task for Staff ---")
    tomorrow = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
    task_payload = {
        "title": "Inspect Drainage Sector 4",
        "description": "Inspect drainage blockage reported along Sector 4.",
        "assigned_to": staff_id,
        "priority": "HIGH",
        "due_date": tomorrow,
        "due_time": "17:00",
        "requires_evidence": True,
        "instructions": "Take photos of cleared culverts."
    }
    status, res = request("/api/punong-barangay/tasks", "POST", task_payload, pb_token)
    assert status in (200, 201) and (res.get("success") or res.get("ok")), f"Task creation failed: {res}"
    task_id = res.get("task_id") or res.get("taskId") or (res.get("task") and res["task"]["id"])
    assert task_id, "No task_id returned"

    # Verify Staff sees task on their dashboard/tasks endpoint
    status, res = request("/api/staff/tasks", "GET", token=staff_token)
    assert status == 200, f"Staff get tasks failed: {res}"
    staff_task_list = res.get("all", []) if isinstance(res, dict) else res
    found_task = next((t for t in staff_task_list if t["id"] == task_id), None)
    assert found_task is not None, f"Staff cannot find assigned task {task_id}"
    assert found_task["priority"] == "HIGH"
    assert found_task["status"] == "PENDING"
    print("PASS: TEST 3 - PB created task, Staff sees task on dashboard.")
    passed += 1

    # TEST 4: Staff acknowledges task -> status changes to ACKNOWLEDGED in database
    print("\n--- TEST 4: Staff acknowledges task ---")
    status, res = request(f"/api/tasks/{task_id}/acknowledge", "POST", {}, staff_token)
    assert status in (200, 201) and (res.get("success") or res.get("ok")), f"Task acknowledge failed: {res}"

    # Verify in SQLite database
    conn = sqlite3.connect("bms.sqlite3")
    c = conn.cursor()
    c.execute("SELECT status, acknowledged_at FROM tasks WHERE id = ?", (task_id,))
    t_row = c.fetchone()
    assert t_row[0] == "ACKNOWLEDGED", f"Status in DB is {t_row[0]}, expected ACKNOWLEDGED"
    assert t_row[1] is not None, "acknowledged_at not set"
    conn.close()
    print("PASS: TEST 4 - Staff acknowledged task, status updated in database.")
    passed += 1

    # TEST 5: Staff submits progress update (25%, 50%) -> history recorded in database
    print("\n--- TEST 5: Staff submits progress updates ---")
    # 25% update
    status, res = request(f"/api/tasks/{task_id}/updates", "POST", {
        "progress_percent": 25,
        "notes": "Arrived on site and began clearing debris.",
        "status_change": "IN_PROGRESS"
    }, staff_token)
    assert status in (200, 201) and (res.get("success") or res.get("ok")), f"Progress 25% failed: {res}"

    # 50% update
    status, res = request(f"/api/tasks/{task_id}/updates", "POST", {
        "progress_percent": 50,
        "notes": "Culvert halfway cleared, second truck arrived."
    }, staff_token)
    assert status in (200, 201) and (res.get("success") or res.get("ok")), f"Progress 50% failed: {res}"

    # Verify updates in SQLite
    conn = sqlite3.connect("bms.sqlite3")
    c = conn.cursor()
    c.execute("SELECT progress_percent, update_text FROM task_updates WHERE task_id = ? ORDER BY id ASC", (task_id,))
    updates = c.fetchall()
    assert len(updates) >= 2, f"Expected >= 2 updates, got {len(updates)}"
    assert updates[-2][0] == 25
    assert updates[-1][0] == 50
    # Also verify current progress on tasks table
    c.execute("SELECT progress_percent, status FROM tasks WHERE id = ?", (task_id,))
    cur_task = c.fetchone()
    assert cur_task[0] == 50
    assert cur_task[1] == "IN_PROGRESS"
    conn.close()
    print("PASS: TEST 5 - Progress updates recorded with full audit history.")
    passed += 1

    # TEST 6: Staff marks task completed -> status changes to COMPLETED, PB receives notification
    print("\n--- TEST 6: Staff marks task completed ---")
    status, res = request(f"/api/tasks/{task_id}/complete", "POST", {
        "notes": "Culvert fully cleared. Water flow restored normal."
    }, staff_token)
    assert status in (200, 201) and (res.get("success") or res.get("ok")), f"Complete task failed: {res}"

    # Verify status in database
    conn = sqlite3.connect("bms.sqlite3")
    c = conn.cursor()
    c.execute("SELECT status, progress_percent, completion_date, completion_notes FROM tasks WHERE id = ?", (task_id,))
    t_row = c.fetchone()
    assert t_row[0] == "COMPLETED", f"Expected COMPLETED, got {t_row[0]}"
    assert t_row[1] == 100, f"Expected 100% progress, got {t_row[1]}"
    assert t_row[2] is not None, "Completion date is missing"
    assert "fully cleared" in (t_row[3] or "")

    # Verify PB received completion notification
    c.execute("SELECT title, body FROM notifications WHERE user_id = ? AND related_id = ? ORDER BY id DESC LIMIT 1", (pb_id, task_id))
    notif = c.fetchone()
    assert notif is not None, "PB notification missing"
    assert "Completed" in notif[0] or "completed" in notif[1].lower()
    conn.close()
    print("PASS: TEST 6 - Task completed, 100% set, PB notified.")
    passed += 1

    # TEST 7: Overdue task calculated correctly -> tasks past due date automatically marked or displayed as OVERDUE
    print("\n--- TEST 7: Overdue task calculation ---")
    past_date = (datetime.datetime.now() - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    overdue_payload = {
        "title": "Old Inspection Task",
        "description": "Task that was due 2 days ago.",
        "assigned_to": staff_id,
        "priority": "MEDIUM",
        "due_date": past_date,
        "due_time": "12:00"
    }
    status, res = request("/api/punong-barangay/tasks", "POST", overdue_payload, pb_token)
    assert status in (200, 201), f"Failed to create overdue test task: {res}"
    od_task_id = res.get("task_id") or res.get("taskId") or (res.get("task") and res["task"]["id"])

    # Now query PB tasks or Staff tasks to trigger overdue calculation
    status, res = request("/api/punong-barangay/tasks", "GET", token=pb_token)
    assert status == 200
    all_pb_tasks = res if isinstance(res, list) else res.get("tasks", [])
    od_task = next((t for t in all_pb_tasks if t["id"] == od_task_id), None)
    assert od_task is not None
    assert od_task["status"] == "OVERDUE", f"Expected OVERDUE status, got {od_task['status']}"

    # Also check PB dashboard stats count overdue
    status, res = request("/api/punong-barangay/dashboard", "GET", token=pb_token)
    assert status == 200
    assert res.get("overdueTasks", 0) >= 1 or res.get("stats", {}).get("overdueTasks", 0) >= 1
    print("PASS: TEST 7 - Overdue calculation verified accurately.")
    passed += 1

    # TEST 8: Staff attempts to assign task -> rejected with 403 Forbidden
    print("\n--- TEST 8: Staff attempts to assign task ---")
    status, res = request("/api/punong-barangay/tasks", "POST", {
        "title": "Unauthorized Task Assignment",
        "assigned_to": staff_id,
        "due_date": tomorrow
    }, staff_token)
    assert status == 403, f"Expected 403 Forbidden, got {status} ({res})"
    print(f"PASS: TEST 8 - Staff task assignment correctly rejected with 403: {res.get('error')}")
    passed += 1

    # TEST 9: Staff attempts to access Admin permissions -> rejected with 403 Forbidden
    print("\n--- TEST 9: Staff attempts to access Admin permissions ---")
    # Attempt to access /api/admin/punong-barangay or /api/admin/website-settings
    status, res = request("/api/admin/punong-barangay", "GET", token=staff_token)
    assert status == 403, f"Expected 403 Forbidden on /api/admin/punong-barangay, got {status}"
    status, res = request("/api/admin/website-settings", "GET", token=staff_token)
    assert status == 403, f"Expected 403 Forbidden on /api/admin/website-settings, got {status}"
    print("PASS: TEST 9 - Staff access to admin endpoints correctly rejected with 403.")
    passed += 1

    # TEST 10: Staff account archived -> login disabled, existing tasks & messages remain intact in history
    print("\n--- TEST 10: Staff account archived ---")
    # Archive staff via Admin
    status, res = request(f"/api/admin/staff/{staff_id}/archive", "PUT", {}, admin_token)
    assert status == 200 and (res.get("success") or res.get("ok")), f"Archive staff failed: {res}"

    # Try logging in with archived staff credentials
    status, res = request("/api/login", "POST", {"username": "staff", "password": "staff"})
    assert status in (401, 403), f"Archived staff should not be able to log in, got {status}: {res}"

    # Verify tasks and messages still retain staff info in SQLite
    conn = sqlite3.connect("bms.sqlite3")
    c = conn.cursor()
    c.execute("SELECT assigned_to_user_id, title FROM tasks WHERE id = ?", (task_id,))
    t_row = c.fetchone()
    assert t_row is not None and t_row[0] == staff_id, f"Historical task lost staff ID! {t_row}"

    c.execute("SELECT sender_id, body FROM messages WHERE conversation_id = ? AND sender_id = ?", (conv_id, staff_id))
    m_row = c.fetchone()
    assert m_row is not None and m_row[0] == staff_id, f"Historical message lost sender ID! {m_row}"
    conn.close()
    print("PASS: TEST 10 - Staff archived: login disabled, history preserved with ID.")
    passed += 1

    # TEST 11: PB account archived -> historical commands & tasks remain intact
    print("\n--- TEST 11: PB account archived ---")
    # Admin updates PB to archived
    status, res = request("/api/admin/punong-barangay", "PUT", {"account_status": "archived"}, admin_token)
    assert status == 200 and (res.get("success") or res.get("ok")), f"PB update failed: {res}"

    # Verify PB historical tasks still reference PB
    conn = sqlite3.connect("bms.sqlite3")
    c = conn.cursor()
    c.execute("SELECT created_by_user_id FROM tasks WHERE id = ?", (task_id,))
    t_row = c.fetchone()
    assert t_row is not None and t_row[0] == pb_id, "PB historical task lost created_by_user_id"
    conn.close()

    # Restore PB to active
    status, res = request("/api/admin/punong-barangay", "PUT", {"account_status": "active"}, admin_token)
    assert status == 200 and (res.get("success") or res.get("ok")), f"PB restore failed: {res}"
    print("PASS: TEST 11 - PB archived: historical records intact, PB restored to active.")
    passed += 1

    # TEST 12: Staff account restored -> account active again
    print("\n--- TEST 12: Staff account restored ---")
    status, res = request(f"/api/admin/staff/{staff_id}/restore", "PUT", {}, admin_token)
    assert status == 200 and (res.get("success") or res.get("ok")), f"Restore staff failed: {res}"

    # Verify restored staff can login again
    status, res = request("/api/login", "POST", {"username": "staff", "password": "staff"})
    assert status == 200, f"Restored staff login failed: {res}"
    print("PASS: TEST 12 - Staff restored and successfully authenticated.")
    passed += 1

    print("\n========================================")
    print(f"ALL {passed}/{total} ACCEPTANCE TESTS PASSED!")
    print("========================================")

if __name__ == "__main__":
    run_tests()
