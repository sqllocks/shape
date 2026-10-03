"""AUD-security2 #282: a workbook whose parts inflate hundreds of times is refused before it is
parsed, at sizes far below the old 256 MiB floor."""

from __future__ import annotations

import zipfile

import pytest

from shape.io.excel import WorkbookError, check_workbook_file


def test_shared_strings_that_inflate_400_times_are_refused(tmp_path):
    path = tmp_path / "bomb.xlsx"
    item = b"<si><t>" + b"A" * 50 + b"</t></si>"
    body = b'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    body += item * ((32 << 20) // len(item)) + b"</sst>"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", b"<Types/>")
        zf.writestr("xl/sharedStrings.xml", body)
    info = zipfile.ZipFile(path).getinfo("xl/sharedStrings.xml")
    assert info.file_size > 200 * info.compress_size  # the shape of the reported bomb
    with pytest.raises(WorkbookError, match="sharedStrings.xml inflates"):
        check_workbook_file(path)
