import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/remove_national_standard_lexicon_term.py"
SPEC = importlib.util.spec_from_file_location("remove_national_standard_lexicon_term", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_removes_only_whole_term_preserving_custom_entries_and_line_endings(newline):
    source = newline.join(["标准化施工工艺", " 国标施工规范 ", "用户新增内容", "关于国标施工规范的备注", ""])
    expected = newline.join(["标准化施工工艺", "用户新增内容", "关于国标施工规范的备注", ""])
    assert MODULE.remove_term(source.encode()) == expected.encode()


def test_deletion_is_idempotent_and_preserves_no_final_newline():
    source = "国标施工规范\n保留词条".encode()
    first = MODULE.remove_term(source)
    assert first == "保留词条".encode()
    assert MODULE.remove_term(first) == first


def test_invalid_encoding_fails_before_changes():
    with pytest.raises(UnicodeDecodeError):
        MODULE.remove_term(b"\xff")
