#!/usr/bin/env python3
from __future__ import annotations

import os
import time
from pathlib import Path

import paramiko

HOST = "172.16.103.15"
PASSWORD = os.environ.get("DEPLOY_SSH_PASSWORD") or "Hirun2026@"
OUT = Path(__file__).resolve().with_suffix(".out.txt")


def run(c, cmd, timeout=90):
    chan = c.get_transport().open_session()
    chan.settimeout(timeout)
    chan.exec_command(cmd)
    chunks = []
    deadline = time.time() + timeout
    while time.time() < deadline:
        if chan.recv_ready():
            chunks.append(chan.recv(65535))
        if chan.recv_stderr_ready():
            chunks.append(chan.recv_stderr(65535))
        if chan.exit_status_ready():
            while chan.recv_ready():
                chunks.append(chan.recv(65535))
            while chan.recv_stderr_ready():
                chunks.append(chan.recv_stderr(65535))
            break
        time.sleep(0.05)
    else:
        chan.close()
        raise TimeoutError(cmd[:120])
    return b"".join(chunks).decode("utf-8", "replace")


def main() -> int:
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username="root", password=PASSWORD, timeout=20)
    try:
        out = run(
            c,
            "docker logs api-dev --tail 400 2>&1 | "
            "python3 -c \"import sys; t=sys.stdin.read(); "
            "keys=('NoneType','strip','Traceback','AttributeError','content'); "
            "lines=t.splitlines(); "
            "[print(l) for i,l in enumerate(lines) if any(k in l for k in keys) or "
            "(i>0 and 'Traceback' in lines[i-1]) or (i>1 and 'Traceback' in lines[max(0,i-8):i])]; "
            "print('---FULL_TAIL---'); print('\\n'.join(lines[-80:]))\"",
            timeout=90,
        )
        OUT.write_text(out, encoding="utf-8")
        print(out[-12000:])
    finally:
        c.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
