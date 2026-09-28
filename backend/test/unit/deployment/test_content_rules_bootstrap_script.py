from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess


ROOT = Path(__file__).parents[4]
BOOTSTRAP_SCRIPT = ROOT / "scripts/source-deploy/bootstrap-content-rules-test-server.sh"


def write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


def arrange_bootstrap(tmp_path: Path, *, version: int = 4, tasks: int = 0, manual_versions: int = 0):
    repo = tmp_path / "repo"
    fake_bin = tmp_path / "bin"
    backup_root = tmp_path / "backups"
    docker_log = tmp_path / "docker.log"
    version_file = tmp_path / "version"
    import_log = tmp_path / "import.log"
    repo.mkdir()
    fake_bin.mkdir()
    (repo / "backend/data").mkdir(parents=True)
    (repo / "backend/scripts").mkdir(parents=True)
    (repo / ".env.test").write_text("COMPOSE_PROJECT_NAME=bootstrap-test\n", encoding="utf-8")
    (repo / "docker-compose-test.yml").write_text("services: {}\n", encoding="utf-8")
    (repo / "backend/data/content-rules.sql").write_text(
        "INSERT INTO content_rule_versions VALUES "
        "('content-rules-platform-v11', NULL, 11, 'published');\n",
        encoding="utf-8",
    )
    version_file.write_text(str(version), encoding="utf-8")

    write_executable(
        repo / "backend/scripts/import_content_rules.sh",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_IMPORT_LOG"
printf '%s\n' 11 > "$FAKE_VERSION_FILE"
""",
    )
    write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >> "$FAKE_DOCKER_LOG"
if [[ "$*" == *"MAX(version)"* ]]; then
  cat "$FAKE_VERSION_FILE"
elif [[ "$*" == *"COUNT(*) FROM content_tasks"* ]]; then
  printf '%s\n' "$FAKE_TASK_COUNT"
elif [[ "$*" == *"created_by IS DISTINCT FROM"* ]]; then
  printf '%s\n' "$FAKE_MANUAL_VERSION_COUNT"
elif [[ "$*" == *"pg_dump"* ]]; then
  printf '%s\n' fake-database-dump
fi
""",
    )

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}{os.pathsep}{env['PATH']}",
            "CONTENTSWARM_TEST_REPO": str(repo),
            "CONTENTSWARM_TEST_BACKUP_ROOT": str(backup_root),
            "FAKE_DOCKER_LOG": str(docker_log),
            "FAKE_VERSION_FILE": str(version_file),
            "FAKE_IMPORT_LOG": str(import_log),
            "FAKE_TASK_COUNT": str(tasks),
            "FAKE_MANUAL_VERSION_COUNT": str(manual_versions),
        }
    )
    return repo, backup_root, docker_log, import_log, env


def run_bootstrap(repo: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(BOOTSTRAP_SCRIPT)],
        cwd=repo,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_bootstrap_imports_snapshot_and_restarts_business_services(tmp_path: Path):
    repo, backup_root, docker_log, import_log, env = arrange_bootstrap(tmp_path)

    result = run_bootstrap(repo, env)

    assert result.returncode == 0, result.stderr
    assert "内容规则首次初始化完成: v4 -> v11" in result.stdout
    assert import_log.exists()
    backups = list(backup_root.glob("*.dump"))
    assert len(backups) == 1
    assert backups[0].stat().st_size > 0
    calls = docker_log.read_text(encoding="utf-8")
    assert " stop api worker" in calls
    assert " up -d --wait --no-deps api worker" in calls


def test_bootstrap_skips_when_target_version_is_already_published(tmp_path: Path):
    repo, backup_root, docker_log, import_log, env = arrange_bootstrap(tmp_path, version=11)

    result = run_bootstrap(repo, env)

    assert result.returncode == 0, result.stderr
    assert "无需初始化" in result.stdout
    assert not import_log.exists()
    assert not backup_root.exists()
    assert " stop api worker" not in docker_log.read_text(encoding="utf-8")


def test_bootstrap_rejects_database_with_existing_content_tasks(tmp_path: Path):
    repo, backup_root, docker_log, import_log, env = arrange_bootstrap(tmp_path, tasks=4)

    result = run_bootstrap(repo, env)

    assert result.returncode != 0
    assert "当前存在 4 个内容任务" in result.stderr
    assert not import_log.exists()
    assert not backup_root.exists()
    assert " stop api worker" not in docker_log.read_text(encoding="utf-8")


def test_bootstrap_rejects_non_system_rule_versions(tmp_path: Path):
    repo, backup_root, docker_log, import_log, env = arrange_bootstrap(tmp_path, manual_versions=1)

    result = run_bootstrap(repo, env)

    assert result.returncode != 0
    assert "当前存在 1 个非系统规则版本" in result.stderr
    assert not import_log.exists()
    assert not backup_root.exists()
    assert " stop api worker" not in docker_log.read_text(encoding="utf-8")
