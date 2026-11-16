from pathlib import Path
import re

text = Path(__file__).with_name("_inspect_10315_task_body.out.txt").read_text(encoding="utf-8")
match = re.search(r"body_repr_head: (.*)\nversion_columns:", text, re.S)
if not match:
    raise SystemExit("body not found")
body = eval(match.group(1))
Path(__file__).with_name("_task_body_utf8.txt").write_text(body, encoding="utf-8")
print("len", len(body))
print("zaishou", "在手工地" in body)
print("zaijian", "在建工地" in body)
print("newlines", body.count("\n"))
print(body)
