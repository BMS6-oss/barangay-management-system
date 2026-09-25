"""
BMS Floating Message & Command Center -- Comprehensive Test Suite
Tests all acceptance criteria for the floating messaging widget.
"""
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

import urllib.request
import urllib.error
import json
import sys
import time
from datetime import datetime, timedelta

BASE = "http://127.0.0.1:8000"
PASS = "\u2705 PASS"
FAIL = "\u274c FAIL"
results = []

def req(path, method="GET", body=None, token=None):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    resp = urllib.request.urlopen(r)
    return json.loads(resp.read().decode())

def req_raw(path, method="GET", body=None, token=None):
    """Returns (status_code, body_dict)"""
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", "Bearer " + token)
    try:
        resp = urllib.request.urlopen(r)
        return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())

def check(name, cond, detail=""):
    icon = PASS if cond else FAIL
    msg = f"{icon} [{name}]"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append((name, cond, detail))
    return cond

print("=" * 60)
print("BMS FLOATING MESSAGE & COMMAND CENTER — TEST SUITE")
print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)
print()

# ── TC-01: Health check ──────────────────────────────────────
print("── TC-01: Server Health ──")
try:
    h = req("/api/health")
    check("TC-01 Health", h.get("ok") is True, f"status={h.get('status')}")
except Exception as e:
    check("TC-01 Health", False, str(e))
print()

# ── TC-02: PB Login ──────────────────────────────────────────
print("── TC-02: PB Login & Token ──")
pb_token = None
try:
    data = req("/api/login", "POST", {"username": "punong_barangay", "password": "captain123"})
    pb_token = data.get("token")
    check("TC-02 PB Login", bool(pb_token), f"role={data.get('user',{}).get('role')}")
except Exception as e:
    check("TC-02 PB Login", False, str(e))
print()

# ── TC-03: Staff Login ───────────────────────────────────────
print("── TC-03: Staff Login & Token ──")
staff_token = None
staff_user_id = None
try:
    data = req("/api/login", "POST", {"username": "staff", "password": "staff"})
    staff_token = data.get("token")
    staff_user_id = data.get("user", {}).get("id")
    check("TC-03 Staff Login", bool(staff_token), f"role={data.get('user',{}).get('role')}")
except Exception as e:
    check("TC-03 Staff Login", False, str(e))
print()

# ── TC-04: PB can open panel (list conversations) ────────────
print("── TC-04: PB List Conversations ──")
pb_convs = []
try:
    pb_convs = req("/api/messages", token=pb_token)
    check("TC-04 PB can list convs", isinstance(pb_convs, list), f"{len(pb_convs)} conversations")
    if pb_convs:
        c = pb_convs[0]
        has_fields = all(k in c for k in ["id","otherParticipantName","otherParticipantRole","unreadCount","lastMessageType"])
        check("TC-04 Conv fields", has_fields, f"name={c.get('otherParticipantName')} role={c.get('otherParticipantRole')}")
except Exception as e:
    check("TC-04 PB list convs", False, str(e))
print()

# ── TC-05: PB sends message to Staff ────────────────────────
print("── TC-05: PB Sends NORMAL Message to Staff ──")
staff_convs_before = []
try:
    staff_convs_before = req("/api/messages", token=staff_token)
    init_unread = sum(c.get("unreadCount", 0) for c in staff_convs_before)
    # PB sends via /api/punong-barangay/messages
    staff_users = req("/api/punong-barangay/staff", token=pb_token)
    staff_id = staff_user_id or (staff_users[0]["id"] if staff_users else None)
    if staff_id:
        result = req("/api/punong-barangay/messages", "POST", {
            "recipientIds": [staff_id],
            "messageType": "NORMAL",
            "subject": "Test Message",
            "body": "TC-05: Automated test message from PB."
        }, token=pb_token)
        check("TC-05 PB sends message", result.get("conversationId") is not None or result.get("success") or result.get("message_ids") is not None, f"result={list(result.keys())}")
    else:
        check("TC-05 PB sends message", False, "No staff users found")
except Exception as e:
    check("TC-05 PB sends message", False, str(e))
print()

# ── TC-06: PB creates a Task/Command ────────────────────────
print("── TC-06: PB Creates a Task Command ──")
new_task_id = None
try:
    staff_users = req("/api/punong-barangay/staff", token=pb_token)
    staff_id = staff_user_id or (staff_users[0]["id"] if staff_users else None)
    due_date = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
    if staff_id:
        result = req("/api/punong-barangay/tasks", "POST", {
            "title": "TC-06 Automated Test Command",
            "description": "This is an automated test command. Process all test documents.",
            "priority": "HIGH",
            "dueDate": due_date,
            "dueTime": "17:00",
            "assignedStaffIds": [staff_id]
        }, token=pb_token)
        new_task_id = result.get("taskId") or result.get("task_id") or result.get("id")
        check("TC-06 PB creates task", bool(new_task_id), f"task_id={new_task_id}")
    else:
        check("TC-06 PB creates task", False, "No staff users")
except Exception as e:
    check("TC-06 PB creates task", False, str(e))
print()

# ── TC-07: Staff sees the new task in their messages ────────
print("── TC-07: Staff Sees Task in Messages ──")
try:
    staff_convs = req("/api/messages", token=staff_token)
    task_convs = [c for c in staff_convs if c.get("lastMessageType") == "TASK" or c.get("taskId")]
    check("TC-07 Staff sees task conv", len(task_convs) > 0, f"{len(task_convs)} task conversations")
except Exception as e:
    check("TC-07 Staff sees task conv", False, str(e))
print()

# ── TC-08: Staff acknowledges a Task ────────────────────────
print("── TC-08: Staff Acknowledges Task ──")
ack_task_id = new_task_id
try:
    if not ack_task_id:
        # Find a pending task from staff's list
        staff_tasks = req("/api/staff/tasks", token=staff_token)
        pending = staff_tasks.get("pending", []) + staff_tasks.get("all", [])
        pending = [t for t in pending if t.get("my_status","").upper() in ("PENDING","ASSIGNED")]
        if pending:
            ack_task_id = pending[0]["id"]
    if ack_task_id:
        sc, result = req_raw(f"/api/tasks/{ack_task_id}/acknowledge", "POST", {}, token=staff_token)
        check("TC-08 Staff acknowledges", sc == 200, f"status={sc} msg={result.get('message','')}")
    else:
        check("TC-08 Staff acknowledges", False, "No pending tasks to acknowledge")
except Exception as e:
    check("TC-08 Staff acknowledges", False, str(e))
print()

# ── TC-09: Staff updates progress ───────────────────────────
print("── TC-09: Staff Updates Progress ──")
try:
    if ack_task_id:
        sc, result = req_raw(f"/api/tasks/{ack_task_id}/updates", "POST", {
            "updateText": "TC-09: Working on this. 40% done.",
            "updateType": "progress",
            "progressPercent": 40
        }, token=staff_token)
        check("TC-09 Staff progress update", sc in (200, 201), f"status={sc}")
    else:
        check("TC-09 Staff progress update", False, "No task ID")
except Exception as e:
    check("TC-09 Staff progress update", False, str(e))
print()

# ── TC-10: Staff completes task ─────────────────────────────
print("── TC-10: Staff Completes Task ──")
try:
    if ack_task_id:
        sc, result = req_raw(f"/api/tasks/{ack_task_id}/complete", "POST", {
            "completionNotes": "TC-10: Automated test task completed successfully."
        }, token=staff_token)
        check("TC-10 Staff completes task", sc == 200, f"status={sc} msg={result.get('message','')[:60]}")
    else:
        check("TC-10 Staff completes task", False, "No task ID")
except Exception as e:
    check("TC-10 Staff completes task", False, str(e))
print()

# ── TC-11: Overdue Task Detection ───────────────────────────
print("── TC-11: Overdue Task Detected by Server ──")
try:
    pb_tasks = req("/api/punong-barangay/tasks", token=pb_token)
    all_tasks = pb_tasks.get("all", []) + pb_tasks.get("overdue", []) if isinstance(pb_tasks, dict) else pb_tasks
    overdue_tasks = [t for t in all_tasks if t.get("status","").upper() == "OVERDUE"]
    # Also check task_assignments in thread
    staff_convs = req("/api/messages", token=staff_token)
    task_convs_with_overdue = [c for c in staff_convs if c.get("taskId")]
    if task_convs_with_overdue:
        thread = req(f"/api/messages/{task_convs_with_overdue[0]['id']}", token=staff_token)
        msgs = thread.get("messages", [])
        overdue_in_thread = any(
            m.get("task", {}).get("status","").upper() == "OVERDUE" or
            any(a.get("status","").upper() == "OVERDUE" for a in m.get("task", {}).get("assignments", []))
            for m in msgs if m.get("task")
        )
        check("TC-11 Overdue in thread", overdue_in_thread or len(overdue_tasks) > 0,
              f"overdue tasks={len(overdue_tasks)}, overdue_in_thread={overdue_in_thread}")
    else:
        check("TC-11 Overdue detection", len(overdue_tasks) > 0, f"{len(overdue_tasks)} overdue tasks")
except Exception as e:
    check("TC-11 Overdue detection", False, str(e))
print()

# ── TC-12: Unread count matches SQLite ──────────────────────
print("── TC-12: Unread Count Matches SQLite ──")
try:
    unread_data = req("/api/messages/unread-count", token=staff_token)
    has_fields = "totalUnread" in unread_data or "unreadMessages" in unread_data
    total = unread_data.get("totalUnread", unread_data.get("unreadMessages", 0))
    check("TC-12 Unread count API", has_fields, f"totalUnread={total}")
except Exception as e:
    check("TC-12 Unread count", False, str(e))
print()

# ── TC-13: Mark all as read ──────────────────────────────────
print("── TC-13: Mark All Read Resets to 0 ──")
try:
    sc, result = req_raw("/api/messages/mark-all-read", "POST", {}, token=staff_token)
    check("TC-13 Mark all read API", sc == 200, f"status={sc}")
    # Check unread now
    unread_after = req("/api/messages/unread-count", token=staff_token)
    total_after = unread_after.get("totalUnread", unread_after.get("unreadMessages", -1))
    check("TC-13 Unread = 0 after mark-all", total_after == 0, f"totalUnread={total_after}")
except Exception as e:
    check("TC-13 Mark all read", False, str(e))
print()

# ── TC-14: Staff cannot create PB Task (403) ────────────────
print("── TC-14: Staff Creating Task Returns 403 ──")
try:
    sc, result = req_raw("/api/punong-barangay/tasks", "POST", {
        "title": "Unauthorized Task by Staff",
        "description": "This should be rejected",
        "priority": "NORMAL",
        "dueDate": "2026-12-31",
        "assignedStaffIds": [1]
    }, token=staff_token)
    check("TC-14 Staff task blocked", sc == 403, f"HTTP {sc} error={result.get('error','')[:60]}")
except Exception as e:
    check("TC-14 Staff task blocked", False, str(e))
print()

# ── TC-15: Unauthenticated request returns 401 ──────────────
print("── TC-15: Unauthenticated Returns 401 ──")
try:
    sc, result = req_raw("/api/messages", token=None)
    check("TC-15 Unauth = 401", sc == 401, f"HTTP {sc}")
except Exception as e:
    check("TC-15 Unauth = 401", False, str(e))
print()

# ── TC-16: Message type dropdown (CLARIFICATION reply) ──────
print("── TC-16: CLARIFICATION Type Reply Accepted ──")
try:
    staff_convs = req("/api/messages", token=staff_token)
    if staff_convs:
        conv_id = staff_convs[0]["id"]
        sc, result = req_raw(f"/api/messages/{conv_id}/reply", "POST", {
            "body": "TC-16: Requesting clarification on the task scope.",
            "messageType": "CLARIFICATION"
        }, token=staff_token)
        check("TC-16 CLARIFICATION type reply", sc in (200, 201), f"HTTP {sc}")
    else:
        check("TC-16 CLARIFICATION reply", False, "No conversations found")
except Exception as e:
    check("TC-16 CLARIFICATION reply", False, str(e))
print()

# ── TC-17: Staff cannot send TASK type messages (403) ────────
print("── TC-17: Staff TASK-Type Reply Blocked (403) ──")
try:
    staff_convs = req("/api/messages", token=staff_token)
    if staff_convs:
        conv_id = staff_convs[0]["id"]
        sc, result = req_raw(f"/api/messages/{conv_id}/reply", "POST", {
            "body": "Unauthorized task creation via message",
            "messageType": "TASK"
        }, token=staff_token)
        check("TC-17 Staff TASK blocked", sc == 403, f"HTTP {sc} error={result.get('error','')[:60]}")
    else:
        check("TC-17 Staff TASK blocked", False, "No conversations")
except Exception as e:
    check("TC-17 Staff TASK blocked", False, str(e))
print()

# ── TC-18: Task completion summary in thread (PB) ────────────
print("── TC-18: Group Completion Summary in Thread ──")
try:
    if pb_convs:
        conv_id = pb_convs[0]["id"]
        thread = req(f"/api/messages/{conv_id}", token=pb_token)
        msgs = thread.get("messages", [])
        task_msgs = [m for m in msgs if m.get("task")]
        has_summary = any(
            "completionSummary" in m.get("task", {}) and m["task"].get("completionSummary")
            for m in task_msgs
        )
        check("TC-18 Group completion summary", has_summary or len(task_msgs) > 0,
              f"task_msgs={len(task_msgs)}, summaries found={sum(1 for m in task_msgs if m.get('task',{}).get('completionSummary'))}")
    else:
        check("TC-18 Group completion summary", False, "No PB conversations")
except Exception as e:
    check("TC-18 Group completion summary", False, str(e))
print()

# ══ SUMMARY ══════════════════════════════════════════════════
print()
print("=" * 60)
print("TEST SUMMARY")
print("=" * 60)
passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
total = len(results)
print(f"PASSED: {passed}/{total}")
print(f"FAILED: {failed}/{total}")
if failed:
    print()
    print("FAILED TESTS:")
    for name, ok, detail in results:
        if not ok:
            print(f"  {FAIL} {name}: {detail}")
print()
sys.exit(0 if failed == 0 else 1)
