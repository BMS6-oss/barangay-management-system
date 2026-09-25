import urllib.request, json

login_data = json.dumps({'username': 'punong_barangay', 'password': 'captain123'}).encode()
req = urllib.request.Request('http://127.0.0.1:8000/api/login', data=login_data)
req.add_header('Content-Type', 'application/json')
resp = urllib.request.urlopen(req)
token = json.loads(resp.read())['token']

req2 = urllib.request.Request('http://127.0.0.1:8000/api/messages')
req2.add_header('Authorization', 'Bearer ' + token)
convs = json.loads(urllib.request.urlopen(req2).read())
print('PB convs:', len(convs))
for c in convs[:5]:
    cid = c['id']
    name = c.get('otherParticipantName')
    role = c.get('otherParticipantRole')
    task_id = c.get('taskId')
    last_type = c.get('lastMessageType')
    unread = c.get('unreadCount')
    print(f'  id={cid} name={name} role={role} taskId={task_id} lastType={last_type} unread={unread}')
