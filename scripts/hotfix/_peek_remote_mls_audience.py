#!/usr/bin/env python3
import os
import paramiko

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(
    "172.16.103.15",
    username="root",
    password=os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@",
    timeout=20,
)
cmd = r"""
sed -n '850,980p' /srv/contentswarm/backend/package/yuxi/services/material_library_service.py
echo '===== ensure calls ====='
grep -n 'ensure_enterprise_gallery_audiences\|def ensure_material_categories\|ContentMaterialAudience\|audience_id' /srv/contentswarm/backend/package/yuxi/services/material_library_service.py | head -40
echo '===== stub ====='
sed -n '1,80p' /srv/contentswarm/backend/package/yuxi/services/enterprise_gallery_audience.py
echo '===== serialize cover ====='
sed -n '800,860p' /srv/contentswarm/backend/package/yuxi/services/material_library_service.py
"""
_, o, e = c.exec_command(cmd, timeout=60)
print(o.read().decode("utf-8", "replace"))
err = e.read().decode("utf-8", "replace")
if err.strip():
    print(err)
c.close()
