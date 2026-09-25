import urllib.request, json, urllib.error

# Login as PB
login_data = json.dumps({'username': 'punong_barangay', 'password': 'captain123'}).encode()
req = urllib.request.Request('http://127.0.0.1:8000/api/login', data=login_data)
req.add_header('Content-Type', 'application/json')
resp = urllib.request.urlopen(req)
token = json.loads(resp.read())['token']

# Get conversation 1 as PB
req2 = urllib.request.Request('http://127.0.0.1:8000/api/messages/1')
req2.add_header('Authorization', 'Bearer ' + token)
d = json.loads(urllib.request.urlopen(req2).read())
msgs = d.get('messages', [])
print(f'Messages in conv 1: {len(msgs)}')
for m in msgs:
    mtype = m.get('message_type')
    body_preview = str(m.get('body',''))[:50]
    has_task = bool(m.get('task'))
    tid = m.get('task_id')
    print(f'  type={mtype}, task_id={tid}, has_task={has_task}, body={body_preview}')
    if has_task:
        t = m['task']
        print(f'    title={t.get("title")}')
        print(f'    completedCount={t.get("completedCount")}, totalAssignees={t.get("totalAssignees")}')
        print(f'    completionSummary={t.get("completionSummary")}')
        for a in t.get('assignments', []):
            print(f'    -> {a.get("staff_name")}: status={a.get("status")}, progress={a.get("progress_percent")}')
