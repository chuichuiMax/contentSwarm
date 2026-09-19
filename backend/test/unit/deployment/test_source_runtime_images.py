import os
import subprocess

import pytest


def run_image(image_env: str, command: str) -> subprocess.CompletedProcess[str]:
    image = os.environ.get(image_env)
    if not image:
        pytest.skip(f"未通过 {image_env} 指定待验收镜像")

    return subprocess.run(
        ["docker", "run", "--rm", image, "sh", "-ec", command],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )


def test_sandbox_runtime_has_dependencies_but_no_application():
    result = run_image(
        "SOURCE_DEPLOY_SANDBOX_IMAGE",
        "test ! -e /app/app.py; "
        "test ! -e /app/sandbox.env; "
        "command -v curl; "
        "python -c 'import docker, fastapi, kubernetes, uvicorn'",
    )

    assert result.returncode == 0, result.stderr


def test_web_builder_has_fixed_toolchain_but_no_source():
    result = run_image(
        "SOURCE_DEPLOY_WEB_BUILDER_IMAGE",
        "test ! -e /work/web; "
        "test \"$(pnpm --version)\" = 10.11.0; "
        "node --version | grep '^v24\\.'; "
        "command -v git; "
        "command -v rsync",
    )

    assert result.returncode == 0, result.stderr


def test_release_builder_has_node_and_go_but_no_source():
    result = run_image(
        "SOURCE_DEPLOY_RELEASE_BUILDER_IMAGE",
        "test ! -e /work/apps/hycanvas; "
        "test \"$(pnpm --version)\" = 10.11.0; "
        "node --version | grep '^v24\\.'; "
        "go version | grep 'go1.25\\.'; "
        "command -v npm",
    )

    assert result.returncode == 0, result.stderr


def test_hycanvas_runtime_has_runtime_packages_but_no_binary():
    result = run_image(
        "SOURCE_DEPLOY_HYCANVAS_RUNTIME_IMAGE",
        "test ! -e /app/hycanvas; "
        "test -d /app/.data/storage; "
        "command -v ffmpeg; "
        "command -v curl; "
        "test -d /usr/share/fonts/opentype/noto",
    )

    assert result.returncode == 0, result.stderr
