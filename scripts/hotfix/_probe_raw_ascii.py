#!/usr/bin/env python3
from __future__ import annotations

import urllib.request

BASE = "http://124.232.148.28:8080"


def check(path: str) -> None:
    for q in ["", "?import", "?raw", "?import&raw", "?url", "?import&url"]:
        url = f"{BASE}{path}{q}"
        try:
            with urllib.request.urlopen(url, timeout=20) as r:
                data = r.read(80)
                print("OK", r.status, path.split("/")[-1], q or "(none)", data[:50])
        except Exception as e:  # noqa: BLE001
            print("FAIL", path.split("/")[-1], q or "(none)", e)


def main() -> None:
    check("/src/assets/content-test-cases/%E6%88%90%E9%83%BD%E6%8A%A5%E4%BB%B7003.txt")
    check("/src/assets/css/base.css")
    # create probe via known ascii asset if any
    check("/src/assets/contentflow-homepage-logo.svg")


if __name__ == "__main__":
    main()
