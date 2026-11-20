#!/usr/bin/env python3
from __future__ import annotations

import re
import urllib.request

BASE = "http://124.232.148.28:8080"


def main() -> None:
    body = urllib.request.urlopen(f"{BASE}/src/data/contentTestCases.js", timeout=30).read()
    print("has_utf8_chengdu", "成都".encode("utf-8") in body)
    print("has_gbk_chengdu", "成都".encode("gbk") in body)
    m = re.search(br'from "(/src/assets/content-test-cases/[^"]+)"', body)
    print("first_import_bytes", m.group(1) if m else None)
    if m:
        path = m.group(1).decode("utf-8")
        print("first_import_str", path)
        for q in ["", "?import", "?raw", "?import&raw"]:
            url = f"{BASE}{path}{q}"
            try:
                with urllib.request.urlopen(url, timeout=20) as r:
                    print("OK", r.status, q or "(none)", "clen", r.headers.get("Content-Length"))
            except Exception as e:  # noqa: BLE001
                print("FAIL", q or "(none)", e)

    enc = "/src/assets/content-test-cases/%E6%88%90%E9%83%BD%E6%8A%A5%E4%BB%B7003.txt"
    for q in ["", "?import", "?raw", "?import&raw"]:
        url = f"{BASE}{enc}{q}"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = r.read(60)
                print("encOK", r.status, q or "(none)", data[:40])
        except Exception as e:  # noqa: BLE001
            print("encFAIL", q or "(none)", e)


if __name__ == "__main__":
    main()
