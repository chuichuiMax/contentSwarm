import json
import os
import sys

import paramiko

ITEM = sys.argv[1] if len(sys.argv) > 1 else "mli_1f0e4947f9fe42a19dcf4a846d0b3fe6"
pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit(1)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)

py = f"""
import json, os, urllib.request, urllib.error

def env(k):
    for line in open('/srv/contentswarm/.env', encoding='utf-8', errors='replace'):
        if line.startswith(k + '='):
            return line.split('=', 1)[1].strip().strip('"').strip("'")
    return ''

user = env('YUXI_SUPER_ADMIN_NAME') or env('ADMIN_USERNAME') or 'admin'
password = env('YUXI_SUPER_ADMIN_PASSWORD') or env('ADMIN_PASSWORD') or ''
if not password:
    raise SystemExit('no admin password')

req = urllib.request.Request(
    'http://127.0.0.1:5050/api/auth/login',
    data=json.dumps({{'username': user, 'password': password}}).encode(),
    headers={{'Content-Type': 'application/json'}},
    method='POST',
)
with urllib.request.urlopen(req, timeout=30) as r:
    token = json.load(r)['access_token']

item = {json.dumps(ITEM)}
headers = {{'Authorization': f'Bearer {{token}}'}}
for path in (f'/api/material-library/items/{{item}}/thumbnail', f'/api/material-library/items/{{item}}/file'):
    url = 'http://127.0.0.1:5050' + path
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
            print(path, r.status, r.headers.get('Content-Type'), len(data))
    except urllib.error.HTTPError as e:
        body = e.read()[:300]
        print(path, 'HTTP', e.code, body)
"""

cmd = "docker exec api-dev python -c " + json.dumps(py)
_, o, e = c.exec_command(cmd, timeout=180)
print(o.read().decode())
err = e.read().decode().strip()
if err:
    print("stderr:", err[:800])
c.close()
