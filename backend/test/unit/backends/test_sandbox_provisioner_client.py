import httpx

from yuxi.agents.backends.sandbox.provisioner_client import ProvisionerClient


def test_create_waits_for_cold_start_without_extending_other_requests(monkeypatch):
    calls = []

    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return httpx.Response(200, json={"sandbox_id": "test", "sandbox_url": "http://sandbox:8080"})

    monkeypatch.setattr(httpx, "request", request)
    client = ProvisionerClient("http://provisioner:8002")
    record = client.create("test", "thread", "owner")
    assert record.sandbox_id == "test"
    assert calls[0][2]["timeout"].read == 360
    assert calls[0][2]["timeout"].connect == 20
    assert calls[0][2]["json"]["uid"] == "owner"
    assert client.health()
    assert calls[1][2]["timeout"].read == 20


def test_create_reports_startup_failure(monkeypatch):
    import pytest

    monkeypatch.setattr(httpx, "request", lambda *args, **kwargs: httpx.Response(500, text="not ready"))
    with pytest.raises(RuntimeError, match="500 not ready"):
        ProvisionerClient("http://provisioner:8002").create("test", "thread", "owner")
