from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


MODULE_PATH = Path(__file__).resolve().parents[3] / "package" / "yuxi" / "services" / "mp_works_logic.py"
SPEC = importlib.util.spec_from_file_location("mp_works_logic", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class MpWorksLogicTest(unittest.TestCase):
    def test_visible_work_items_keeps_only_visible_succeeded_output_assets(self):
        items = MODULE.visible_work_items(
            [
                {
                    "id": "job-new",
                    "status": "succeeded",
                    "created_at": "2026-09-16T10:00:00",
                    "result_json": {"asset_ids": ["output-visible", "output-hidden", "source-asset"]},
                },
                {
                    "id": "job-failed",
                    "status": "failed",
                    "created_at": "2026-09-16T11:00:00",
                    "result_json": {"asset_ids": ["failed-output"]},
                },
                {
                    "id": "job-retry",
                    "status": "succeeded",
                    "created_at": "2026-09-16T09:00:00",
                    "result_json": {"asset_ids": ["output-visible"]},
                },
            ],
            {
                "output-visible": {
                    "id": "output-visible", "role": "output", "hidden_from_works_at": None,
                    "created_at": "2026-09-16T10:02:00",
                },
                "output-hidden": {
                    "id": "output-hidden", "role": "output", "hidden_from_works_at": "2026-09-16T10:01:00"
                },
                "source-asset": {"id": "source-asset", "role": "source", "hidden_from_works_at": None},
                "failed-output": {"id": "failed-output", "role": "output", "hidden_from_works_at": None},
            },
        )

        self.assertEqual(
            items,
            [
                {
                    "id": "output-visible",
                    "job_id": "job-new",
                    "created_at": "2026-09-16T10:00:00",
                    "uploaded_at": "2026-09-16T10:02:00",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
