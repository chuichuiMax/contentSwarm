import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).parents[4]
SCRIPT = ROOT / "scripts/source-deploy/check-runtime-lock.sh"


def run_script(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [os.environ.get("BASH_EXECUTABLE", "bash"), SCRIPT.as_posix(), *args],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_runtime_lock_round_trip_and_mismatch(tmp_path: Path):
    root_lock = tmp_path / "root.lock"
    package_lock = tmp_path / "package.lock"
    expected = tmp_path / "expected.sha256"
    root_lock.write_text("root-v1", encoding="utf-8")
    package_lock.write_text("package-v1", encoding="utf-8")

    assert run_script("write", expected.as_posix(), root_lock.as_posix(), package_lock.as_posix()).returncode == 0
    assert run_script("verify", expected.as_posix(), root_lock.as_posix(), package_lock.as_posix()).returncode == 0

    package_lock.write_text("package-v2", encoding="utf-8")
    result = run_script("verify", expected.as_posix(), root_lock.as_posix(), package_lock.as_posix())

    assert result.returncode != 0
    assert "基础镜像依赖摘要不匹配" in result.stderr


@pytest.mark.skipif(not os.environ.get("SOURCE_DEPLOY_API_IMAGE"), reason="未指定待验收的 API 基础镜像")
def test_api_runtime_image_has_dependencies_but_no_business_source():
    image = os.environ["SOURCE_DEPLOY_API_IMAGE"]

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            image,
            "sh",
            "-ec",
            "test ! -e /app/server; "
            "test ! -e /app/package/yuxi; "
            "test ! -e /build/backend; "
            "test -s /opt/runtime-locks/api.sha256; "
            "python -c 'import fastapi, patchright'",
        ],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
