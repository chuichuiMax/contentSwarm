import os
import sys

import paramiko

HOST = "172.16.103.15"
LOCAL = os.path.join(os.path.dirname(__file__), sys.argv[2] if len(sys.argv) > 2 else "_oss_download_test.py")
REMOTE_HOST = "/tmp/_oss_download_test.py"
REMOTE_CONTAINER = "/tmp/_oss_download_test.py"
ITEM = sys.argv[1] if len(sys.argv) > 1 else "mli_1f0e4947f9fe42a19dcf4a846d0b3fe6"

pw = os.environ.get("DEPLOY_SSH_PASSWORD", "")
if not pw:
    sys.exit(1)

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(HOST, username="root", password=pw, timeout=30)
sftp = c.open_sftp()
sftp.put(LOCAL, REMOTE_HOST)
sftp.close()
cmd = f"docker cp {REMOTE_HOST} api-dev:{REMOTE_CONTAINER} && docker exec api-dev python {REMOTE_CONTAINER} {ITEM}"
_, o, e = c.exec_command(cmd, timeout=120)
sys.stdout.buffer.write(o.read())
sys.stdout.buffer.write(e.read())
c.close()
