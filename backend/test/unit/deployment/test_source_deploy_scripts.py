from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import subprocess

import pytest


ROOT = Path(__file__).parents[4]
DEPLOY_SCRIPT = ROOT / "scripts/source-deploy/deploy-source.sh"
ROLLBACK_SCRIPT = ROOT / "scripts/source-deploy/rollback-source.sh"


@dataclass(frozen=True)
class DeployWorkspace:
    repo: Path
    release_root: Path
    env_file: Path
    fake_bin: Path
    docker_log: Path
    curl_fail_file: Path
    old_sha: str
    new_sha: str
    env: dict[str, str]


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def write_release(release_root: Path, git_sha: str) -> None:
    version = release_root / git_sha
    (version / "web-dist").mkdir(parents=True)
    index = version / "web-dist/index.html"
    index.write_text(git_sha, encoding="utf-8")
    write_executable(version / "hycanvas", "#!/usr/bin/env sh\nexit 0\n")
    web_line = f"{hashlib.sha256(index.read_bytes()).hexdigest()}  ./index.html\n"
    web_sha = hashlib.sha256(web_line.encode()).hexdigest()
    hycanvas_sha = hashlib.sha256((version / "hycanvas").read_bytes()).hexdigest()
    (version / "manifest.env").write_text(
        f"FORMAT_VERSION=1\nGIT_SHA={git_sha}\nWEB_SHA256={web_sha}\nHYCANVAS_SHA256={hycanvas_sha}\n",
        encoding="utf-8",
    )


def arrange_deploy_workspace(tmp_path: Path) -> DeployWorkspace:
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    release_root = tmp_path / "releases"
    fake_bin = tmp_path / "bin"
    docker_log = tmp_path / "docker.log"
    curl_fail_file = tmp_path / "curl.fail"
    env_file = tmp_path / ".env.source"
    repo.mkdir()
    fake_bin.mkdir()
    release_root.mkdir()
    env_file.write_text("COMPOSE_PROJECT_NAME=source-deploy-test\n", encoding="utf-8")

    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.email", "test@example.com")
    run_git(repo, "config", "user.name", "Source Deploy Test")
    (repo / "tracked.txt").write_text("old", encoding="utf-8")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "old")
    old_sha = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "remote", "add", "origin", str(remote))
    run_git(repo, "push", "-u", "origin", "main")

    (repo / "tracked.txt").write_text("new", encoding="utf-8")
    run_git(repo, "commit", "-am", "new")
    new_sha = run_git(repo, "rev-parse", "HEAD")
    run_git(repo, "push", "origin", "main")
    run_git(repo, "reset", "--hard", old_sha)

    write_release(release_root, old_sha)
    (release_root / "current").symlink_to(old_sha, target_is_directory=True)

    write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
if [[ -n "${FAKE_DOCKER_FAIL_PATTERN:-}" && "$*" == *"$FAKE_DOCKER_FAIL_PATTERN"* ]]; then
  exit 19
fi
if [[ "$*" == *" config --images"* ]]; then
  printf '%s\n' source-api:test source-builder:test source-sandbox:test
fi
if [[ "$*" == *" run --rm release-builder"* ]]; then
  sha=$(git -C "$SOURCE_DEPLOY_REPO" rev-parse HEAD)
  old_target=""
  if [[ -L "$SOURCE_DEPLOY_RELEASE_ROOT/current" ]]; then
    old_target=$(readlink "$SOURCE_DEPLOY_RELEASE_ROOT/current")
  fi
  mkdir -p "$SOURCE_DEPLOY_RELEASE_ROOT/$sha/web-dist"
  printf '%s\n' "$sha" > "$SOURCE_DEPLOY_RELEASE_ROOT/$sha/web-dist/index.html"
  printf '#!/usr/bin/env sh\nexit 0\n' > "$SOURCE_DEPLOY_RELEASE_ROOT/$sha/hycanvas"
  chmod +x "$SOURCE_DEPLOY_RELEASE_ROOT/$sha/hycanvas"
  web_sha=$(cd "$SOURCE_DEPLOY_RELEASE_ROOT/$sha/web-dist" \
    && find . -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')
  hycanvas_sha=$(sha256sum "$SOURCE_DEPLOY_RELEASE_ROOT/$sha/hycanvas" | awk '{print $1}')
  printf 'FORMAT_VERSION=1\nGIT_SHA=%s\nWEB_SHA256=%s\nHYCANVAS_SHA256=%s\n' \
    "$sha" "$web_sha" "$hycanvas_sha" > "$SOURCE_DEPLOY_RELEASE_ROOT/$sha/manifest.env"
  if [[ -n "$old_target" && "$old_target" != "$sha" ]]; then
    ln -sfn "$old_target" "$SOURCE_DEPLOY_RELEASE_ROOT/previous"
  fi
  ln -sfn "$sha" "$SOURCE_DEPLOY_RELEASE_ROOT/current"
fi
exit 0
""",
    )
    write_executable(
        fake_bin / "curl",
        """#!/usr/bin/env bash
set -euo pipefail
if [[ -n "${FAKE_CURL_FAIL_FILE:-}" && -f "$FAKE_CURL_FAIL_FILE" ]]; then
  rm -f "$FAKE_CURL_FAIL_FILE"
  exit 22
fi
exit 0
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "FAKE_DOCKER_LOG": str(docker_log),
            "FAKE_CURL_FAIL_FILE": str(curl_fail_file),
            "SOURCE_DEPLOY_REPO": str(repo),
            "SOURCE_DEPLOY_RELEASE_ROOT": str(release_root),
            "SOURCE_DEPLOY_ENV_FILE": str(env_file),
            "SOURCE_DEPLOY_COMPOSE_FILE": str(ROOT / "docker-compose.source.yml"),
            "SOURCE_DEPLOY_BRANCH": "main",
            "HEALTH_ATTEMPTS": "1",
            "HEALTH_INTERVAL_SECONDS": "0",
        }
    )
    return DeployWorkspace(
        repo,
        release_root,
        env_file,
        fake_bin,
        docker_log,
        curl_fail_file,
        old_sha,
        new_sha,
        env,
    )


def run_script(
    workspace: DeployWorkspace,
    script: Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=workspace.repo,
        env=workspace.env,
        text=True,
        capture_output=True,
        check=False,
    )


def docker_calls(workspace: DeployWorkspace) -> str:
    if not workspace.docker_log.exists():
        return ""
    return workspace.docker_log.read_text(encoding="utf-8")


def state_values(workspace: DeployWorkspace) -> dict[str, str]:
    state_file = workspace.repo / ".deploy/source-deploy/current-state.env"
    return dict(line.split("=", 1) for line in state_file.read_text(encoding="utf-8").splitlines())


def test_deploy_rejects_dirty_tracked_worktree_before_stopping_services(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)
    (workspace.repo / "tracked.txt").write_text("dirty", encoding="utf-8")

    result = run_script(workspace, DEPLOY_SCRIPT)

    assert result.returncode != 0
    assert "未提交的已跟踪文件" in result.stderr
    assert " stop " not in docker_calls(workspace)


def test_validate_only_has_no_deployment_side_effects(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)

    result = run_script(workspace, DEPLOY_SCRIPT, "--validate-only")
    calls = docker_calls(workspace)

    assert result.returncode == 0, result.stderr
    assert " config --quiet" in calls
    assert "image inspect " in calls
    assert " stop " not in calls
    assert " run --rm release-builder" not in calls
    assert " up -d " not in calls
    assert run_git(workspace.repo, "rev-parse", "HEAD") == workspace.old_sha


def test_deploy_builds_before_recreating_runtime_services(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)

    result = run_script(workspace, DEPLOY_SCRIPT, "--no-pull")
    calls = docker_calls(workspace)

    assert result.returncode == 0, result.stderr
    assert calls.index("run --rm release-builder") < calls.index("up -d --wait --no-deps --force-recreate")


def test_deploy_never_force_recreates_persistent_services(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)

    result = run_script(workspace, DEPLOY_SCRIPT, "--no-pull")
    calls = docker_calls(workspace).splitlines()

    assert result.returncode == 0, result.stderr
    assert any("up -d --no-recreate postgres redis minio etcd milvus graph hycanvas-db" in call for call in calls)
    force_recreate_calls = [call for call in calls if "--force-recreate" in call]
    for service in ("postgres", "redis", "minio", "etcd", "milvus", "graph", "hycanvas-db"):
        assert all(service not in call.split() for call in force_recreate_calls)


def test_successful_deploy_reports_release_and_health_evidence(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)

    result = run_script(workspace, DEPLOY_SCRIPT)

    assert result.returncode == 0, result.stderr
    assert f"Git 更新: {workspace.old_sha} -> {workspace.new_sha}" in result.stdout
    assert "发布产物摘要:" in result.stdout
    assert "WEB_SHA256=" in result.stdout
    assert "HYCANVAS_SHA256=" in result.stdout
    assert "健康检查通过:" in result.stdout
    assert "容器状态:" in result.stdout
    assert " ps" in docker_calls(workspace)


def test_successful_deploy_records_current_and_previous_commits(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)

    result = run_script(workspace, DEPLOY_SCRIPT)

    assert result.returncode == 0, result.stderr
    assert run_git(workspace.repo, "rev-parse", "HEAD") == workspace.new_sha
    assert os.readlink(workspace.release_root / "current") == workspace.new_sha
    state = state_values(workspace)
    assert state["GIT_SHA"] == workspace.new_sha
    assert state["PREVIOUS_GIT_SHA"] == workspace.old_sha
    assert state["RELEASE_SHA"] == workspace.new_sha
    assert state["PREVIOUS_RELEASE_SHA"] == workspace.old_sha


def test_health_failure_restores_git_release_and_old_state(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)
    state_root = workspace.repo / ".deploy/source-deploy"
    state_root.mkdir(parents=True)
    (state_root / "current-state.env").write_text(
        f"GIT_SHA={workspace.old_sha}\nPREVIOUS_GIT_SHA=\nBRANCH=main\n"
        f"RELEASE_SHA={workspace.old_sha}\nPREVIOUS_RELEASE_SHA=\n",
        encoding="utf-8",
    )
    workspace.curl_fail_file.touch()

    result = run_script(workspace, DEPLOY_SCRIPT)

    assert result.returncode != 0
    assert "已回滚" in result.stderr
    assert run_git(workspace.repo, "rev-parse", "HEAD") == workspace.old_sha
    assert os.readlink(workspace.release_root / "current") == workspace.old_sha
    assert state_values(workspace)["GIT_SHA"] == workspace.old_sha
    assert docker_calls(workspace).count("up -d --wait --no-deps --force-recreate") == 2


@pytest.mark.parametrize(
    "fail_pattern",
    ("run --rm --no-deps api", "run --rm release-builder"),
)
def test_lock_or_builder_failure_restores_previous_git_and_release(tmp_path: Path, fail_pattern: str):
    workspace = arrange_deploy_workspace(tmp_path)
    workspace.env["FAKE_DOCKER_FAIL_PATTERN"] = fail_pattern

    result = run_script(workspace, DEPLOY_SCRIPT)

    assert result.returncode != 0
    assert "已回滚" in result.stderr
    assert run_git(workspace.repo, "rev-parse", "HEAD") == workspace.old_sha
    assert os.readlink(workspace.release_root / "current") == workspace.old_sha


def test_explicit_rollback_restores_selected_release(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)
    write_release(workspace.release_root, workspace.new_sha)
    (workspace.release_root / "current").unlink()
    (workspace.release_root / "current").symlink_to(workspace.new_sha, target_is_directory=True)
    run_git(workspace.repo, "switch", "--detach", workspace.new_sha)
    state_root = workspace.repo / ".deploy/source-deploy"
    state_root.mkdir(parents=True)
    (state_root / "current-state.env").write_text(
        f"GIT_SHA={workspace.new_sha}\nPREVIOUS_GIT_SHA={workspace.old_sha}\nBRANCH=main\n"
        f"RELEASE_SHA={workspace.new_sha}\nPREVIOUS_RELEASE_SHA={workspace.old_sha}\n",
        encoding="utf-8",
    )

    result = run_script(workspace, ROLLBACK_SCRIPT)

    assert result.returncode == 0, result.stderr
    assert run_git(workspace.repo, "rev-parse", "HEAD") == workspace.old_sha
    assert os.readlink(workspace.release_root / "current") == workspace.old_sha
    state = state_values(workspace)
    assert state["GIT_SHA"] == workspace.old_sha
    assert state["PREVIOUS_GIT_SHA"] == workspace.new_sha


def test_explicit_rollback_rejects_tampered_release_before_stopping_services(tmp_path: Path):
    workspace = arrange_deploy_workspace(tmp_path)
    write_release(workspace.release_root, workspace.new_sha)
    (workspace.release_root / workspace.old_sha / "web-dist/index.html").write_text("tampered", encoding="utf-8")
    state_root = workspace.repo / ".deploy/source-deploy"
    state_root.mkdir(parents=True)
    (state_root / "current-state.env").write_text(
        f"GIT_SHA={workspace.new_sha}\nPREVIOUS_GIT_SHA={workspace.old_sha}\nBRANCH=main\n"
        f"RELEASE_SHA={workspace.new_sha}\nPREVIOUS_RELEASE_SHA={workspace.old_sha}\n",
        encoding="utf-8",
    )

    result = run_script(workspace, ROLLBACK_SCRIPT)

    assert result.returncode != 0
    assert "缺少完整发布产物" in result.stderr
    assert " stop " not in docker_calls(workspace)
