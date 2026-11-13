"""通过 HTTP API 把 Excel 文案提示词同步到目标环境。提示词为空跳过；缺失风格可创建。"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from urllib import error, parse, request

PAREN_SUFFIX = re.compile(r"[（(].*$")


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", str(value or "").strip())


def _base_name(value: str) -> str:
    return _norm(PAREN_SUFFIX.sub("", value))


def load_rows(path: Path) -> list[dict[str, str]]:
    rows = json.loads(path.read_text(encoding="utf-8-sig"))
    return [
        {
            "content_type_name": str(item.get("content_type_name") or "").strip(),
            "name": str(item.get("name") or "").strip(),
            "prompt": str(item.get("prompt") or "").strip(),
        }
        for item in rows
        if str(item.get("name") or "").strip()
    ]


def match_style(excel_name: str, styles: list[dict]) -> dict | None:
    exact = [item for item in styles if _norm(item.get("name")) == _norm(excel_name)]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        return None
    base = _base_name(excel_name)
    soft = [
        item
        for item in styles
        if _base_name(str(item.get("name") or "")) == base or _norm(str(item.get("name") or "")) == base
    ]
    if len(soft) == 1:
        return soft[0]
    return None


def http_json(method: str, url: str, *, token: str | None = None, data: dict | None = None, form: dict | None = None):
    headers = {"Accept": "application/json"}
    body = None
    if form is not None:
        body = parse.urlencode(form).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"{method} {url} -> HTTP {exc.code}: {detail}") from exc


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True, help="例如 http://172.16.103.15:8080")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--create-missing", action="store_true")
    args = parser.parse_args()
    base = args.base_url.rstrip("/")

    token_payload = http_json(
        "POST",
        f"{base}/api/auth/token",
        form={"username": args.username, "password": args.password},
    )
    token = str(token_payload.get("access_token") or "")
    if not token:
        raise SystemExit(f"login failed: {token_payload}")

    styles_payload = http_json("GET", f"{base}/api/creative-styles", token=token)
    styles = list(styles_payload.get("creative_styles") or [])
    by_type: dict[str, list[dict]] = {}
    type_id_by_name: dict[str, str] = {}
    for item in styles:
        type_name = str(item.get("content_type_name") or "").strip()
        type_id = str(item.get("content_type_id") or "").strip()
        if type_name and type_id:
            type_id_by_name[_norm(type_name)] = type_id
        by_type.setdefault(type_id, []).append(item)

    # content types endpoint for missing style create
    types_payload = http_json("GET", f"{base}/api/content-types", token=token)
    for item in types_payload.get("content_types") or []:
        name = str(item.get("name") or "").strip()
        type_id = str(item.get("id") or "").strip()
        if name and type_id:
            type_id_by_name[_norm(name)] = type_id

    rows = load_rows(args.source)
    todo = [row for row in rows if row["prompt"]]
    skipped_empty = [row for row in rows if not row["prompt"]]
    updated = created = unchanged = missing_type = unmatched = 0

    for row in todo:
        type_id = type_id_by_name.get(_norm(row["content_type_name"]))
        if not type_id:
            print(f"MISSING_TYPE {row['content_type_name']} / {row['name']}")
            missing_type += 1
            continue
        style = match_style(row["name"], by_type.get(type_id, []))
        if style is None:
            if not args.create_missing:
                print(f"UNMATCHED {row['content_type_name']} / {row['name']}")
                unmatched += 1
                continue
            print(f"CREATE {row['content_type_name']} / {row['name']} prompt_len={len(row['prompt'])}")
            created += 1
            if args.apply:
                created_item = http_json(
                    "POST",
                    f"{base}/api/creative-styles",
                    token=token,
                    data={
                        "content_type_id": type_id,
                        "name": row["name"],
                        "description": "",
                        "prompt": row["prompt"],
                        "enabled": True,
                    },
                )
                by_type.setdefault(type_id, []).append(created_item)
            continue
        current = str(style.get("prompt") or "").strip()
        if current == row["prompt"]:
            print(f"SAME {row['content_type_name']} / {style.get('name')}")
            unchanged += 1
            continue
        print(
            f"UPDATE {row['content_type_name']} / {style.get('name')} "
            f"({style.get('id')}) {len(current)}->{len(row['prompt'])}"
        )
        updated += 1
        if args.apply:
            http_json(
                "PATCH",
                f"{base}/api/creative-styles/{style.get('id')}",
                token=token,
                data={"prompt": row["prompt"]},
            )

    print(
        f"summary with_prompt={len(todo)} empty_skipped={len(skipped_empty)} "
        f"updated={updated} created={created} unchanged={unchanged} "
        f"missing_type={missing_type} unmatched={unmatched} apply={args.apply}"
    )


if __name__ == "__main__":
    main()
