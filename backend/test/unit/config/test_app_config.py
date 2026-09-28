from __future__ import annotations

from yuxi.config.app import Config


def test_dangjia_secret_is_redacted_from_config_response(monkeypatch, tmp_path):
    monkeypatch.setenv("DANGJIA_CALLBACK_BASE_URL", "https://callback.example.com")
    monkeypatch.setenv("DANGJIA_CALLBACK_API_KEY", "environment-secret")
    monkeypatch.setenv("DANGJIA_MEDIA_PUBLIC_BASE_URL", "https://content.example.com/media")

    payload = Config(save_dir=str(tmp_path)).dump_config()

    assert payload["dangjia_callback_base_url"] == "https://callback.example.com"
    assert payload["dangjia_media_public_base_url"] == "https://content.example.com/media"
    assert payload["dangjia_callback_api_key"] == ""
    assert payload["dangjia_callback_api_key_configured"] is True


def test_persisted_dangjia_settings_override_environment_for_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("DANGJIA_CALLBACK_BASE_URL", "https://environment.example.com")
    monkeypatch.setenv("DANGJIA_CALLBACK_API_KEY", "environment-secret")
    monkeypatch.setenv("DANGJIA_MEDIA_PUBLIC_BASE_URL", "https://environment.example.com/media")
    api_config = Config(save_dir=str(tmp_path))
    api_config.update(
        {
            "dangjia_callback_base_url": "https://configured.example.com/",
            "dangjia_callback_api_key": "configured-secret",
            "dangjia_media_public_base_url": "https://cdn.example.com/content/",
        }
    )
    api_config.save()

    worker_config = Config(save_dir=str(tmp_path))

    assert worker_config.resolve_dangjia_callback_settings() == {
        "dangjia_callback_base_url": "https://configured.example.com/",
        "dangjia_callback_api_key": "configured-secret",
        "dangjia_media_public_base_url": "https://cdn.example.com/content/",
    }
