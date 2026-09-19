from pathlib import Path

import yaml


ROOT = Path(__file__).parents[4]
COMPOSE_FILE = ROOT / "docker-compose.source.yml"


def load_compose() -> dict:
    return yaml.safe_load(COMPOSE_FILE.read_text(encoding="utf-8"))


def test_business_services_use_prebuilt_images_without_compose_builds():
    compose = load_compose()
    business_services = (
        "api",
        "worker",
        "xhs-browser-gateway",
        "sandbox-provisioner",
        "web",
        "hycanvas-app",
        "release-builder",
    )

    for name in business_services:
        service = compose["services"][name]
        assert "build" not in service
        assert service["image"]


def test_python_services_verify_locks_and_mount_source_read_only():
    compose = load_compose()
    shared_mounts = {
        "${SOURCE_ROOT:?SOURCE_ROOT must be set}/backend/server:/app/server:ro",
        "${SOURCE_ROOT:?SOURCE_ROOT must be set}/backend/package:/app/package:ro",
        "${SOURCE_ROOT:?SOURCE_ROOT must be set}/backend/uv.lock:/opt/source-locks/backend.uv.lock:ro",
        "${SOURCE_ROOT:?SOURCE_ROOT must be set}/backend/package/uv.lock:/opt/source-locks/package.uv.lock:ro",
        "${SOURCE_ROOT:?SOURCE_ROOT must be set}/scripts/source-deploy:/opt/source-deploy:ro",
    }

    for name in ("api", "worker", "xhs-browser-gateway"):
        service = compose["services"][name]
        assert shared_mounts.issubset(set(service["volumes"]))
        command = " ".join(service["command"])
        assert "check-runtime-lock.sh verify" in command
        assert "--reload" not in command


def test_sandbox_verifies_requirements_and_mounts_application_read_only():
    sandbox = load_compose()["services"]["sandbox-provisioner"]

    assert "${SOURCE_ROOT:?SOURCE_ROOT must be set}/docker/sandbox_provisioner/app.py:/app/app.py:ro" in sandbox[
        "volumes"
    ]
    assert (
        "${SOURCE_ROOT:?SOURCE_ROOT must be set}/docker/sandbox_provisioner/requirements.txt:"
        "/opt/source-locks/sandbox.requirements.txt:ro"
    ) in sandbox["volumes"]
    assert "check-runtime-lock.sh verify" in " ".join(sandbox["command"])


def test_builder_and_runtime_artifact_mounts_are_separated():
    compose = load_compose()
    builder = compose["services"]["release-builder"]
    web = compose["services"]["web"]
    hycanvas = compose["services"]["hycanvas-app"]

    assert builder["profiles"] == ["release"]
    assert "${SOURCE_ROOT:?SOURCE_ROOT must be set}:/source:ro" in builder["volumes"]
    assert "${RELEASE_ROOT:?RELEASE_ROOT must be set}:/releases" in builder["volumes"]
    assert "${CACHE_ROOT:?CACHE_ROOT must be set}:/cache" in builder["volumes"]
    assert "${RELEASE_ROOT:?RELEASE_ROOT must be set}/current/web-dist:/usr/share/nginx/html:ro" in web["volumes"]
    assert "${RELEASE_ROOT:?RELEASE_ROOT must be set}/current/hycanvas:/app/hycanvas:ro" in hycanvas["volumes"]
    assert all("/source" not in volume for volume in web["volumes"] + hycanvas["volumes"])


def test_external_config_and_data_roots_are_used():
    compose = load_compose()

    for name in ("api", "worker", "xhs-browser-gateway", "mineru-api"):
        assert compose["services"][name]["env_file"] == [
            "${CONFIG_ROOT:?CONFIG_ROOT must be set}/.env.prod"
        ]

    data_services = ("api", "worker", "xhs-browser-gateway", "sandbox-provisioner", "hycanvas-db", "hycanvas-app")
    data_services += ("graph", "etcd", "minio", "milvus", "postgres", "redis", "paddlex")
    for name in data_services:
        writable_host_mounts = [
            volume for volume in compose["services"][name].get("volumes", []) if not volume.endswith(":ro")
        ]
        assert writable_host_mounts
        assert all(
            volume.startswith("${DATA_ROOT:?DATA_ROOT must be set}/")
            or volume == "/var/run/docker.sock:/var/run/docker.sock"
            for volume in writable_host_mounts
        )


def test_optional_heavy_services_keep_all_profile():
    compose = load_compose()

    assert compose["services"]["mineru-api"]["profiles"] == ["all"]
    assert compose["services"]["paddlex"]["profiles"] == ["all"]


def test_source_deployment_docs_cover_operational_workflows():
    content = (ROOT / "docs/advanced/source-deployment.md").read_text(encoding="utf-8")

    for required in (
        "首次部署",
        "日常更新",
        "基础镜像升级",
        "数据备份",
        "回滚",
        "--validate-only",
        "git pull --ff-only",
        "docker compose",
    ):
        assert required in content
