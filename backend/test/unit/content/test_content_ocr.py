from types import SimpleNamespace

import numpy as np
from yuxi.knowledge.parser.rapid_ocr import RapidOCRParser
from yuxi.storage.postgres.models_content import ContentOCRResult


class FakeRapidOCR:
    def __init__(self):
        self.runtime_params = None

    def __call__(self, image, **runtime_params):
        assert image == b"image-bytes"
        self.runtime_params = runtime_params
        return SimpleNamespace(
            txts=("作品拍摄申请单", "面积 600 平方米"),
            scores=(0.987654321, 0.876543219),
            boxes=np.array(
                [
                    [[10, 20], [180, 20], [180, 48], [10, 48]],
                    [[10, 60], [160, 60], [160, 88], [10, 88]],
                ]
            ),
        )


def test_rapid_ocr_parser_returns_text_blocks_coordinates_and_confidence():
    parser = RapidOCRParser()
    parser.ocr = FakeRapidOCR()

    result = parser.process_image_result(b"image-bytes")

    assert result["text"] == "作品拍摄申请单\n面积 600 平方米"
    assert result["blocks"] == [
        {
            "text": "作品拍摄申请单",
            "confidence": 0.987654,
            "box": [[10, 20], [180, 20], [180, 48], [10, 48]],
        },
        {
            "text": "面积 600 平方米",
            "confidence": 0.876543,
            "box": [[10, 60], [160, 60], [160, 88], [10, 88]],
        },
    ]
    assert result["processing_ms"] >= 0


def test_rapid_ocr_parser_forwards_bounded_runtime_detection_profile():
    parser = RapidOCRParser()
    parser.ocr = FakeRapidOCR()

    parser.process_image_result(
        b"image-bytes",
        params={"text_score": 0.32, "box_thresh": 0.3, "unclip_ratio": 1.8, "ignored": "value"},
    )

    assert parser.ocr.runtime_params == {"text_score": 0.32, "box_thresh": 0.3, "unclip_ratio": 1.8}


def test_ocr_result_keeps_raw_text_and_prefers_saved_correction():
    item = ContentOCRResult(
        id="cor_1",
        task_id="ct_1",
        original_file_name="application.png",
        content_type="image/png",
        file_size=1024,
        image_width=800,
        image_height=600,
        bucket_name="private",
        object_name="private/object.png",
        status="completed",
        raw_text="原始识别文本",
        corrected_text="人工校对文本",
        blocks_json=[{"text": "原始识别文本", "confidence": 0.9, "box": []}],
        created_by="1",
    )

    payload = item.to_dict()

    assert payload["raw_text"] == "原始识别文本"
    assert payload["corrected_text"] == "人工校对文本"
    assert payload["effective_text"] == "人工校对文本"
    assert "bucket_name" not in payload
    assert "object_name" not in payload
