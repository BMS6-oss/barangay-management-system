import urllib.request
import json
import uuid

def req(url, method='GET', data=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    r = urllib.request.Request('http://127.0.0.1:8000' + url, method=method, headers=headers)
    if data:
        r.data = json.dumps(data).encode('utf-8')
    try:
        with urllib.request.urlopen(r) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as e:
        print(f"Error {e.code} on {method} {url}: {e.read().decode()}")
        return None

# Login
login_res = req('/api/login', method='POST', data={'username': 'admin', 'password': 'admin'})
if not login_res:
    print("Login failed")
    exit(1)
token = login_res['token']
print("Login successful.")

# Add Resident
ev = {
    'event': 'resident:added',
    'lastName': 'Test',
    'firstName': f'John_{uuid.uuid4().hex[:6]}',
    'birthDate': '2000-01-01',
    'gender': 'Male',
    'civilStatus': 'Single',
    'address': '123 Street',
    'contact': '1234567890',
    'voter': 'Yes',
    'householdNo': '1'
}
add_res = req('/api/events', method='POST', data=ev, token=token)
if add_res:
    print("Add resident successful:", add_res)
    res_id = add_res.get('residentId')
else:
    print("Add resident failed")
    exit(1)

# List Residents
residents = req('/api/residents', method='GET', token=token)
if residents:
    print(f"Total residents: {len(residents)}")
    new_res = next((r for r in residents if r['resident_id'] == res_id), None)
    if new_res:
        print("Found newly added resident in list:", new_res['first_name'])
        rec_id = new_res['id']
    else:
        print("Newly added resident not found in list")
        exit(1)
else:
    print("Failed to list residents")
    exit(1)

# Edit Resident
edit_data = {'firstName': 'Jonathan'}
edit_res = req(f'/api/residents/{rec_id}', method='PUT', data=edit_data, token=token)
if edit_res:
    print("Edit resident successful:", edit_res)
else:
    print("Edit resident failed")
    exit(1)

# Archive Resident
del_res = req(f'/api/residents/{rec_id}', method='DELETE', token=token)
if del_res:
    print("Archive resident successful:", del_res)
else:
    print("Archive resident failed")
    exit(1)

# List Residents again
residents2 = req('/api/residents', method='GET', token=token)
if residents2 is not None:
    found = any(r['resident_id'] == res_id for r in residents2)
    if found:
        print("Error: Archived resident still in list")
    else:
        print("Archived resident successfully removed from list")
else:
    print("Failed to list residents")
    exit(1)

print("All Resident CRUD operations validated successfully!")
