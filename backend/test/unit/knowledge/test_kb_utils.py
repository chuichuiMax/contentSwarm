import pytest

from yuxi.knowledge.utils.kb_utils import apply_kb_name_prefix, is_minio_url, parse_minio_url, prepare_item_metadata


def test_is_minio_url_accepts_subpath_public_base(monkeypatch):
    monkeypatch.setenv("MINIO_PUBLIC_BASE_URL", "/boyun")
    url = "/boyun/knowledgebases/kb_demo/upload/demo.pdf"

    assert is_minio_url(url) is True
    assert parse_minio_url(url) == ("knowledgebases", "kb_demo/upload/demo.pdf")


def test_is_minio_url_accepts_http_url_with_subpath_prefix(monkeypatch):
    monkeypatch.setenv("MINIO_PUBLIC_BASE_URL", "/boyun")
    url = "http://47.110.157.215/boyun/knowledgebases/kb_demo/upload/demo.pdf"

    assert is_minio_url(url) is True
    assert parse_minio_url(url) == ("knowledgebases", "kb_demo/upload/demo.pdf")


def test_is_minio_url_rejects_unknown_bucket(monkeypatch):
    monkeypatch.delenv("MINIO_PUBLIC_BASE_URL", raising=False)

    assert is_minio_url("/unknown-bucket/demo.txt") is False


def test_apply_kb_name_prefix_adds_prefix_once():
    assert apply_kb_name_prefix("产品库", "博云") == "博云产品库"
    assert apply_kb_name_prefix("博云产品库", "博云") == "博云产品库"
    assert apply_kb_name_prefix("产品库", "") == "产品库"


async def test_prepare_item_metadata_preserves_uploaded_file_size():
    item = "minio://knowledgebases/db/upload/demo.txt"
    params = {
        "content_hashes": {item: "hash"},
        "file_sizes": {item: 1234},
    }

    metadata = await prepare_item_metadata(item, "file", "db", params=params)

    assert metadata["size"] == 1234
    assert "file_sizes" not in (metadata.get("processing_params") or {})


async def test_prepare_item_metadata_preserves_preprocessed_file_size():
    item = "minio://knowledgebases/db/upload/page.html"
    params = {
        "_preprocessed_map": {
            item: {
                "path": item,
                "content_hash": "hash",
                "filename": "https://example.com",
                "file_size": 5678,
            }
        }
    }

    metadata = await prepare_item_metadata(item, "file", "db", params=params)

    assert metadata["size"] == 5678
    assert "_preprocessed_map" not in (metadata.get("processing_params") or {})


async def test_prepare_item_metadata_rejects_direct_url_content_type():
    with pytest.raises(ValueError, match="Unsupported content_type"):
        await prepare_item_metadata("https://example.com", "url", "db")
