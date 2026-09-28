import json
from pathlib import Path


TEST_CASE_DIR = Path(__file__).parents[4] / "web" / "src" / "assets" / "content-test-cases"


def test_content_studio_mock_cases_use_current_dangjia_json_shape():
    files = sorted(TEST_CASE_DIR.glob("*.txt"))
    assert files

    for path in files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        requirement = payload["requirementType"]

        assert isinstance(requirement["houseInfo"], dict), path.name
        assert requirement["houseInfo"].get("mySite"), path.name
        assert isinstance(requirement["prices"], list), path.name
        assert "quotationInfo" not in requirement, path.name
        assert "mySite" not in requirement, path.name
        assert "titlePrice" not in requirement, path.name

        if requirement["typeName"] == "施工报价":
            assert len(requirement["prices"]) == 1, path.name
            assert requirement["prices"][0].get("titlePrice"), path.name
