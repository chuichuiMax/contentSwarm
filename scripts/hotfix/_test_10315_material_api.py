import json
import os
import sys

import paramiko

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
item = sys.argv[1] if len(sys.argv) > 1 else "mli_1f0e4947f9fe42a19dcf4a846d0b3fe6"
if not pw:
    sys.exit(1)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("172.16.103.15", username="root", password=pw, timeout=30)

script = r"""
import json, os, urllib.request, urllib.parse

def load_admin_creds():
    env = {}
    for line in open('/srv/contentswarm/.env', encoding='utf-8', errors='replace'):
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        env[k.strip()] = v.strip().strip('"').strip("'")
    return env.get('YUXI_ADMIN_USERNAME') or env.get('ADMIN_USERNAME') or 'admin', env.get('YUXI_ADMIN_PASSWORD') or env.get('ADMIN_PASSWORD') or ''

user, password = load_admin_creds()
if not password:
    print('no admin password in .env')
    raise SystemExit(1)

login = json.dumps({'username': user, 'password': password}).encode()
req = urllib.request.Request('http://127.0.0.1:5050/api/auth/login', data=login, headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=30) as r:
    token = json.load(r).get('access_token') or json.load(r)
print('login ok', bool(token) if isinstance(token, str) else token)

# re-login simpler
req = urllib.request.Request('http://127.0.0.1:5050/api/auth/login', data=login, headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=30) as r:
    body = json.load(r)
token = body['access_token']
headers = {'Authorization': f'Bearer {token}'}

item_id = %r
for path in (f'/api/material-library/items/{item_id}/thumbnail', f'/api/material-library/items/{item_id}/file'):
    url = 'http://127.0.0.1:5050' + path
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = r.read()
            print(path, r.status, r.headers.get('Content-Type'), len(data))
    except Exception as e:
        print(path, 'ERR', e)
""" % item

cmd = f"docker exec api-dev python -c {json.dumps(script)}"
_, o, e = c.exec_command(cmd, timeout=180)
print(o.read().decode())
print(e.read().decode()[:1500])
c.close()
