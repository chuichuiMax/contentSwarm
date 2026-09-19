from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).parents[4]
SCRIPT = ROOT / "scripts/source-deploy/build-release.sh"


def run(command: list[str], cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def git_head(repo: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], repo).stdout.strip()


def commit_change(repo: Path, value: str) -> None:
    marker = repo / "release-marker.txt"
    marker.write_text(value, encoding="utf-8")
    assert run(["git", "add", "release-marker.txt"], repo).returncode == 0
    assert run(["git", "commit", "-m", value], repo).returncode == 0


def arrange_fake_release_workspace(tmp_path: Path) -> tuple[Path, Path, dict[str, str]]:
    source = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    release_root = tmp_path / "releases"
    work_root = tmp_path / "work"
    fake_bin.mkdir()
    (source / "web").mkdir(parents=True)
    (source / "apps/hycanvas/backend/cmd/api").mkdir(parents=True)
    (source / "apps/hycanvas/backend/internal/webui").mkdir(parents=True)
    (source / "apps/hycanvas/frontend").mkdir(parents=True)
    (source / "web/package.json").write_text('{"scripts":{"build":"vite build"}}', encoding="utf-8")
    (source / "web/pnpm-lock.yaml").write_text("lockfileVersion: '9.0'", encoding="utf-8")
    (source / "apps/hycanvas/package.json").write_text('{"scripts":{}}', encoding="utf-8")
    (source / "apps/hycanvas/package-lock.json").write_text('{"lockfileVersion":3}', encoding="utf-8")
    (source / "apps/hycanvas/backend/go.mod").write_text("module example.test/hycanvas\n", encoding="utf-8")

    write_executable(
        fake_bin / "pnpm",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == "run build" ]]; then mkdir -p dist; printf web > dist/index.html; fi
""",
    )
    write_executable(
        fake_bin / "npm",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ "$*" == *"build:dist"* ]]; then mkdir -p frontend/out; printf hycanvas-web > frontend/out/index.html; fi
""",
    )
    write_executable(
        fake_bin / "go",
        """#!/usr/bin/env bash
set -euo pipefail
[[ "${FAKE_GO_EXIT:-0}" == 0 ]] || exit "$FAKE_GO_EXIT"
output=""
while [[ $# -gt 0 ]]; do
  if [[ "$1" == "-o" ]]; then output="$2"; shift 2; else shift; fi
done
mkdir -p "$(dirname "$output")"
printf '#!/usr/bin/env sh\nexit 0\n' > "$output"
chmod 0755 "$output"
""",
    )

    assert run(["git", "init"], source).returncode == 0
    assert run(["git", "config", "user.email", "test@example.com"], source).returncode == 0
    assert run(["git", "config", "user.name", "Source Deploy Test"], source).returncode == 0
    assert run(["git", "add", "."], source).returncode == 0
    assert run(["git", "commit", "-m", "initial"], source).returncode == 0

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "WORK_ROOT": str(work_root),
            "CACHE_ROOT": str(tmp_path / "cache"),
            "VITE_BASE_PATH": "/boyun/",
            "CONTENTSWARM_PUBLIC_URL": "https://content.example.test/boyun",
        }
    )
    return source, release_root, env


def run_builder(source: Path, release_root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    invocation_env = env | {"SOURCE_ROOT": str(source), "RELEASE_ROOT": str(release_root)}
    return run([os.environ.get("BASH_EXECUTABLE", "bash"), str(SCRIPT)], source, invocation_env)


def test_first_release_creates_version_and_current_link(tmp_path: Path):
    source, release_root, env = arrange_fake_release_workspace(tmp_path)

    result = run_builder(source, release_root, env)
    git_sha = git_head(source)

    assert result.returncode == 0, result.stderr
    version = release_root / git_sha
    assert (version / "web-dist/index.html").is_file()
    assert (version / "hycanvas").is_file()
    assert os.access(version / "hycanvas", os.X_OK)
    manifest = (version / "manifest.env").read_text(encoding="utf-8")
    assert f"GIT_SHA={git_sha}" in manifest
    assert "WEB_SHA256=" in manifest
    assert "HYCANVAS_SHA256=" in manifest
    assert (release_root / "current").resolve() == version.resolve()
    assert not (release_root / "previous").exists()


def test_hycanvas_failure_keeps_previous_release(tmp_path: Path):
    source, release_root, env = arrange_fake_release_workspace(tmp_path)
    assert run_builder(source, release_root, env).returncode == 0
    old_current = (release_root / "current").resolve()

    commit_change(source, "second")
    env["FAKE_GO_EXIT"] = "19"
    result = run_builder(source, release_root, env)

    assert result.returncode == 19
    assert (release_root / "current").resolve() == old_current
    assert not (release_root / git_head(source)).exists()


def test_second_release_moves_old_current_to_previous(tmp_path: Path):
    source, release_root, env = arrange_fake_release_workspace(tmp_path)
    assert run_builder(source, release_root, env).returncode == 0
    first_sha = git_head(source)

    commit_change(source, "second")
    result = run_builder(source, release_root, env)
    second_sha = git_head(source)

    assert result.returncode == 0, result.stderr
    assert (release_root / "current").resolve() == (release_root / second_sha).resolve()
    assert (release_root / "previous").resolve() == (release_root / first_sha).resolve()


def test_repeat_release_reuses_complete_version(tmp_path: Path):
    source, release_root, env = arrange_fake_release_workspace(tmp_path)
    assert run_builder(source, release_root, env).returncode == 0
    manifest = release_root / git_head(source) / "manifest.env"
    first_mtime = manifest.stat().st_mtime_ns

    result = run_builder(source, release_root, env)

    assert result.returncode == 0, result.stderr
    assert "复用已完成发布" in result.stdout
    assert manifest.stat().st_mtime_ns == first_mtime


def test_dirty_tracked_workspace_fails_before_staging(tmp_path: Path):
    source, release_root, env = arrange_fake_release_workspace(tmp_path)
    tracked = source / "web/package.json"
    tracked.write_text("dirty", encoding="utf-8")

    result = run_builder(source, release_root, env)

    assert result.returncode != 0
    assert "工作区存在未提交的已跟踪文件" in result.stderr
    assert not release_root.exists()
