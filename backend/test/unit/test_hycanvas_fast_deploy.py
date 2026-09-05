import gzip
import hashlib
import os
from pathlib import Path
import subprocess


def test_fast_deploy_restores_previous_release_when_health_check_fails(tmp_path: Path) -> None:
    script_name = "scripts/deploy-hycanvas-fast-server.sh"
    root = next(parent for parent in Path(__file__).resolve().parents if (parent / script_name).exists())
    deploy_dir = tmp_path / "deploy"
    release_root = deploy_dir / "releases-root"
    fake_bin = tmp_path / "bin"
    deploy_dir.mkdir()
    fake_bin.mkdir()
    (deploy_dir / ".env.prod").write_text("YUXI_VERSION=test\n")
    (deploy_dir / "docker-compose.prod.yml").write_text("services: {}\n")
    (deploy_dir / "docker-compose.hycanvas-fast.yml").write_text("services: {}\n")

    current_binary = tmp_path / "current-hycanvas"
    current_binary.write_bytes(b"working version")
    current_binary.chmod(0o755)
    candidate = b"broken version"
    archive = tmp_path / "candidate.gz"
    with gzip.open(archive, "wb") as output:
        output.write(candidate)

    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        """#!/usr/bin/env bash
set -euo pipefail
case "$1" in
  compose)
    if [[ " $* " == *" config "* ]]; then exit 0; fi
    if [[ " $* " == *" ps "* ]]; then echo fake-container; exit 0; fi
    if [[ " $* " == *" up "* ]]; then
      count=$(cat "$FAKE_UP_COUNT" 2>/dev/null || echo 0)
      echo $((count + 1)) > "$FAKE_UP_COUNT"
      exit 0
    fi
    ;;
  inspect)
    if [[ " $* " == *"Entrypoint"* ]]; then echo /app/hycanvas; else echo hycanvas:base; fi
    exit 0
    ;;
  cp)
    cp "$FAKE_CURRENT_BINARY" "$3"
    exit 0
    ;;
  exec)
    if [[ $(basename "$(readlink "$FAKE_RELEASE_ROOT/current")") == baseline-* ]]; then exit 0; fi
    exit 1
    ;;
  logs) exit 0 ;;
esac
exit 1
"""
    )
    fake_docker.chmod(0o755)
    up_count = tmp_path / "up-count"
    env = os.environ | {
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "DEPLOY_DIR": str(deploy_dir),
        "RELEASE_ROOT": str(release_root),
        "HEALTH_ATTEMPTS": "1",
        "HEALTH_INTERVAL_SECONDS": "0",
        "FAKE_CURRENT_BINARY": str(current_binary),
        "FAKE_UP_COUNT": str(up_count),
        "FAKE_RELEASE_ROOT": str(release_root),
    }
    result = subprocess.run(
        [
            "bash",
            str(root / script_name),
            "candidate",
            str(archive),
            hashlib.sha256(candidate).hexdigest(),
        ],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "已自动回滚到" in result.stderr
    assert up_count.read_text().strip() == "2"
    assert (release_root / "current" / "hycanvas").read_bytes() == b"working version"
