import json
import subprocess
import urllib.parse
import urllib.request
import http.cookiejar

API = "http://127.0.0.1:8005"
WS = subprocess.check_output(["docker", "exec", "api-dev", "printenv", "HYCANVAS_WORKSPACE_ID"], text=True).strip()
KEY = subprocess.check_output(["docker", "exec", "api-dev", "printenv", "HYCANVAS_API_KEY"], text=True).strip()
req = urllib.request.Request(
    f"{API}/api/v1/auth/integration-ticket/workspace/{WS}",
    method="POST",
    headers={"Authorization": f"Bearer {KEY}"},
)
ticket = json.loads(urllib.request.urlopen(req, timeout=60).read())["ticket"]
redeem = (
    f"{API}/api/v1/auth/integration?ticket={urllib.parse.quote(ticket)}"
    f"&workspaceId={WS}&next=%2Fdashboard%2F"
)
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
opener.open(redeem, timeout=60)
for label, qs in [("all", ""), ("tag_oc_font", "?tag=oc-font")]:
    with opener.open(f"{API}/api/v1/workspaces/{WS}/assets{qs}", timeout=120) as resp:
        raw = resp.read()
        arr = json.loads(raw)
        fonts = sum(1 for a in arr if (a.get("kind") or "").lower() == "font")
        print(label, "count", len(arr), "bytes", len(raw), "fonts", fonts)
