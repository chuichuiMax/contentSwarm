import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[4]
PLANNER = Path(os.environ.get("INCREMENTAL_RELEASE_PLANNER", ROOT / "scripts" / "plan-incremental-release.sh"))


@pytest.mark.parametrize(
    ("paths", "mode", "components"),
    [
        (["backend/server/main.py"], "incremental", "api"),
        (["backend/package/yuxi/agents/skills/buildin/demo/SKILL.md"], "incremental", "api"),
        (["web/src/App.vue"], "incremental", "web"),
        (["apps/hycanvas/backend/main.go"], "incremental", "hycanvas"),
        (
            ["backend/server/main.py", "web/src/App.vue", "apps/hycanvas/backend/main.go"],
            "incremental",
            "api,web,hycanvas",
        ),
        (["backend/uv.lock"], "full_required", ""),
        (["docker-compose.prod.yml"], "full_required", ""),
        (["docker/incremental/api.Dockerfile"], "incremental", ""),
        (["apps/hycanvas/go.mod"], "full_required", ""),
        (["docs/index.md", "backend/test/unit/test_demo.py"], "incremental", ""),
    ],
)
def test_incremental_release_plan(tmp_path: Path, paths: list[str], mode: str, components: str):
    paths_file = tmp_path / "paths.txt"
    paths_file.write_text("\n".join(paths), encoding="utf-8")

    result = subprocess.run(
        ["bash", str(PLANNER), "--paths-file", str(paths_file)],
        check=True,
        capture_output=True,
        text=True,
    )

    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    assert values["release_mode"] == mode
    assert values["components"] == components
