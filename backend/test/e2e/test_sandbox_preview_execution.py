"""Provision a dedicated sandbox and execute/read a real file through the application backend."""

import uuid

import pytest

from yuxi.agents.backends.sandbox.backend import ProvisionerSandboxBackend
from yuxi.agents.backends.sandbox.provider import get_sandbox_provider, sandbox_id_for_thread
from yuxi.utils.paths import VIRTUAL_PATH_PREFIX


@pytest.mark.e2e
def test_sandbox_executes_and_reads_result():
    uid = f"pytest_sandbox_{uuid.uuid4().hex}"
    thread_id = f"pytest-{uuid.uuid4().hex}"
    backend = ProvisionerSandboxBackend(thread_id=thread_id, uid=uid)
    path = f"/{VIRTUAL_PATH_PREFIX.strip('/')}/workspace/acceptance.txt"
    try:
        backend._get_client()
        result = backend.execute(f"printf 'material-integration-ok' > {path}; cat {path}", timeout=30)
        assert result.exit_code == 0, result
        assert "material-integration-ok" in result.output, result
        content = backend.read(path)
        assert content.error is None, content
        assert "material-integration-ok" in content.file_data["content"], content
    finally:
        get_sandbox_provider()._client.delete(sandbox_id_for_thread(thread_id, uid=uid))
