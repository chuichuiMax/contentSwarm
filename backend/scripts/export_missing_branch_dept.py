"""导出生产环境缺少分部/部门的员工为 xlsx（需 SSH 到 172.16.103.17）。"""
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font

SQL = """
\\copy (
  SELECT employee_code, name, login_account, role,
         coalesce(current_branch, ''), coalesce(current_department, '')
  FROM content_employees
  WHERE btrim(coalesce(current_branch, '')) = ''
     OR btrim(coalesce(current_department, '')) = ''
  ORDER BY name
) TO STDOUT WITH (FORMAT csv, DELIMITER E'\\t', HEADER true, ENCODING 'UTF8')
"""


def fetch_rows(host: str) -> list[list[str]]:
    remote = (
        f"docker exec postgres psql -U postgres -d yuxi -c "
        f"\"COPY (SELECT employee_code, name, login_account, role, "
        f"coalesce(current_branch, ''), coalesce(current_department, '') "
        f"FROM content_employees WHERE btrim(coalesce(current_branch, '')) = '' "
        f"OR btrim(coalesce(current_department, '')) = '' ORDER BY name) "
        f"TO STDOUT WITH (FORMAT csv, DELIMITER E'\\t', HEADER true, ENCODING 'UTF8')\""
    )
    raw = subprocess.check_output(["ssh", "-o", "BatchMode=yes", f"root@{host}", remote], text=True)
    lines = [line for line in raw.splitlines() if line.strip()]
    if not lines:
        return []
    header = lines[0].split("\t")
    rows = [line.split("\t") for line in lines[1:]]
    return [header, *rows]


def write_xlsx(rows: list[list[str]], out: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "缺少分部或部门"
    headers = ["员工编码", "姓名", "登录账号", "角色", "当前分部", "当前部门"]
    if rows:
        headers = rows[0]
        data = rows[1:]
    else:
        data = []
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for row in data:
        ws.append(row)
    for col, width in zip("ABCDEF", (16, 14, 16, 14, 18, 22), strict=True):
        ws.column_dimensions[col].width = width
    ws.freeze_panes = "A2"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="172.16.103.17")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(r"c:\Users\10533\Desktop\员工\缺少分部部门员工_20261023.xlsx"),
    )
    args = parser.parse_args()
    rows = fetch_rows(args.host)
    write_xlsx(rows, args.output)
    count = max(0, len(rows) - 1)
    print(f"已写入 {args.output}（{count} 人）")


if __name__ == "__main__":
    main()
