import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from PIL import Image

from oss_import import (
    api_message,
    case_project_fields,
    category_label,
    child_needs_project_details,
    collect_images,
    encode_webp,
    group_images_by_folder,
    needs_design_style,
    uploadable_categories,
)


class OssImportTests(unittest.TestCase):
    def test_api_message_reads_nested_error(self):
        response = requests.Response()
        response.status_code = 422
        response._content = (
            '{"detail":{"error":{"code":"MATERIAL_STYLE_REQUIRED","message":"案例图库上传请选择设计风格"}}}'
        ).encode()
        self.assertEqual(api_message(response), "案例图库上传请选择设计风格")

    def test_collect_images_skips_oversize_and_other_files(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "a.jpg").write_bytes(b"jpeg")
            (root / "nested").mkdir()
            (root / "nested" / "b.png").write_bytes(b"png")
            (root / "note.txt").write_bytes(b"text")
            (root / "huge.webp").write_bytes(b"12345")
            with patch("oss_import.MAX_IMAGE_BYTES", 4):
                images, skipped = collect_images(root)
            self.assertEqual([path.name for path in images], ["a.jpg", "b.png"])
            self.assertEqual(len(skipped), 1)

    def test_case_gallery_without_style_requires_selection(self):
        parent = {"id": "case", "name": "案例图库", "visibility": "enterprise", "image_design_role": "reference"}
        child = {"id": "room", "name": "洋湖天序", "parent_id": "case", "visibility": "enterprise", "can_upload": True}
        rough = {"id": "rough", "name": "毛坯房图库", "visibility": "enterprise", "image_design_role": "rough", "can_upload": True}
        by_id = {"case": parent, "room": child, "rough": rough}
        self.assertTrue(needs_design_style(child, by_id))
        self.assertFalse(needs_design_style(rough, by_id))
        self.assertEqual(category_label(child, by_id), "企业 / 案例图库 / 洋湖天序")
        selected = uploadable_categories({"categories": [parent, child, rough]})
        self.assertEqual([item["id"] for item in selected], ["room", "rough"])

    def test_folder_groups_use_subdirectories_and_encode_webp(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            project = root / "宝能公馆-165㎡"
            project.mkdir()
            image = project / "room.jpg"
            Image.new("RGB", (32, 24), (180, 74, 58)).save(image, format="JPEG")
            (root / "note.txt").write_bytes(b"text")
            groups, skipped = group_images_by_folder(root)
            self.assertEqual(skipped, [])
            self.assertEqual([name for name, _paths in groups], ["宝能公馆-165㎡"])
            name, data = encode_webp(groups[0][1][0])
        self.assertEqual(name, "room.webp")
        self.assertTrue(data.startswith(b"RIFF") and data[8:12] == b"WEBP")
        self.assertEqual(case_project_fields("宝能公馆-165㎡"), ("宝能公馆", "165"))
        self.assertEqual(case_project_fields("洋湖天序"), (None, None))
        self.assertTrue(child_needs_project_details({"visibility": "enterprise", "image_design_role": "reference", "name": "案例图库"}))


if __name__ == "__main__":
    unittest.main()
