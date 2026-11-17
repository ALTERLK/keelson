"""Reading and editing files never changes bytes nobody asked to change."""

from __future__ import annotations

import codecs

import pytest

from keelson import textio, tools


def _bytes(path):
    return path.read_bytes()


# ─── detection ──────────────────────────────────────────────


@pytest.mark.parametrize(("raw", "encoding", "bom", "newline"), [
    ("中文\n".encode(), "utf-8", b"", "\n"),
    (codecs.BOM_UTF8 + "中文\r\n".encode(), "utf-8", codecs.BOM_UTF8, "\r\n"),
    ("中文注释\r\n".encode("gbk"), "gbk", b"", "\r\n"),
    (codecs.BOM_UTF16_LE + "hi\n".encode("utf-16-le"), "utf-16-le", codecs.BOM_UTF16_LE, "\n"),
])
def test_encoding_bom_and_line_endings_are_detected(raw, encoding, bom, newline):
    tf = textio.decode(raw)
    assert (tf.encoding, tf.bom, tf.newline) == (encoding, bom, newline)
    assert tf.encode(tf.text) == raw


def test_binary_and_undecodable_files_are_refused():
    with pytest.raises(textio.NotTextError):
        textio.decode(b"\x89PNG\r\n\x1a\n\x00\x00\x00")
    with pytest.raises(textio.NotTextError):
        textio.decode(b"abc \x80 not text")


def test_mixed_line_endings_are_reported():
    tf = textio.decode(b"a\r\nb\r\nc\n")
    assert tf.newline == "\r\n" and tf.mixed_newlines


# ─── edit_file ──────────────────────────────────────────────


def _edit(path, old, new, **extra):
    return tools._edit_file({"file_path": str(path), "old_string": old, "new_string": new, **extra})


def test_editing_a_crlf_file_changes_only_the_edited_line(tmp_path):
    f = tmp_path / "a.py"
    f.write_bytes(b"line1\r\nline2\r\nline3\r\n")
    assert _edit(f, "line2", "LINE2").startswith("Successfully")
    assert _bytes(f) == b"line1\r\nLINE2\r\nline3\r\n"


def test_multiline_edit_in_a_crlf_file_keeps_crlf(tmp_path):
    f = tmp_path / "a.py"
    f.write_bytes(b"def f():\r\n    return 1\r\n")
    assert _edit(f, "def f():\n    return 1", "def f():\n    x = 1\n    return x").startswith("Successfully")
    assert _bytes(f) == b"def f():\r\n    x = 1\r\n    return x\r\n"


def test_editing_a_gbk_file_keeps_it_gbk(tmp_path):
    f = tmp_path / "legacy.c"
    f.write_bytes("// 旧的注释\r\nint x = 1;\r\n".encode("gbk"))
    assert _edit(f, "int x = 1;", "int x = 2; // 改了").startswith("Successfully")
    assert _bytes(f) == "// 旧的注释\r\nint x = 2; // 改了\r\n".encode("gbk")


def test_a_character_gbk_cannot_hold_is_refused_not_mangled(tmp_path):
    f = tmp_path / "legacy.c"
    original = "// 注释\nint x;\n".encode("gbk")
    f.write_bytes(original)
    result = _edit(f, "int x;", "int x; // 😀")
    assert result.startswith("Error") and "GBK" in result
    assert _bytes(f) == original


def test_bom_is_kept(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes(codecs.BOM_UTF8 + b"hello\n")
    _edit(f, "hello", "world")
    assert _bytes(f) == codecs.BOM_UTF8 + b"world\n"


def test_new_files_at_an_absolute_path_are_written_there_not_in_the_working_folder(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.chdir(work)
    target = tmp_path / "elsewhere" / "deep" / "new.py"
    tools._write_file({"file_path": str(target), "content": "x = 1\n"})
    assert target.read_text(encoding="utf-8") == "x = 1\n"
    assert list(work.iterdir()) == []


def test_binary_files_are_not_edited(tmp_path):
    f = tmp_path / "blob.bin"
    data = b"abc\x00def"
    f.write_bytes(data)
    assert "binary" in _edit(f, "abc", "xyz")
    assert _bytes(f) == data


def test_ambiguous_match_names_the_lines_and_replace_all_works(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("x = 1\ny = 2\nx = 1\n", encoding="utf-8")
    result = _edit(f, "x = 1", "x = 3")
    assert "2 times" in result and "lines 1, 3" in result
    assert f.read_text(encoding="utf-8") == "x = 1\ny = 2\nx = 1\n"
    assert "2 occurrences" in _edit(f, "x = 1", "x = 3", replace_all=True)
    assert f.read_text(encoding="utf-8") == "x = 3\ny = 2\nx = 3\n"


def test_uniqueness_is_checked_on_the_quote_normalized_match(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("print(“hi”)\nprint(“hi”)\n", encoding="utf-8")
    # The model types straight quotes; both lines match after normalization.
    assert "2 times" in _edit(f, 'print("hi")', 'print("bye")')


def test_edit_shows_a_unified_diff(tmp_path):
    f = tmp_path / "a.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    result = _edit(f, "b", "B")
    assert "-b" in result and "+B" in result and "@@" in result


def test_empty_old_string_creates_a_new_file_only(tmp_path):
    f = tmp_path / "new.py"
    assert _edit(f, "", "x = 1\n").startswith("Successfully")
    assert f.read_text(encoding="utf-8") == "x = 1\n"
    assert _edit(f, "", "y").startswith("Error")


# ─── write_file / read_file ─────────────────────────────────


def test_overwriting_keeps_the_existing_encoding_and_line_endings(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes("旧\r\n".encode("gbk"))
    tools._write_file({"file_path": str(f), "content": "新的\n内容\n"})
    assert _bytes(f) == "新的\r\n内容\r\n".encode("gbk")


def test_new_files_are_utf8_with_lf(tmp_path):
    f = tmp_path / "sub" / "new.md"
    tools._write_file({"file_path": str(f), "content": "中文\n"})
    assert _bytes(f) == "中文\n".encode()


def test_no_temporary_files_are_left_behind(tmp_path):
    folder = tmp_path / "only"
    f = folder / "a.txt"
    tools._write_file({"file_path": str(f), "content": "x\n"})
    _edit(f, "x", "y")
    assert sorted(p.name for p in folder.iterdir()) == ["a.txt"]


def test_read_file_pages_with_offset_and_limit(tmp_path):
    f = tmp_path / "long.txt"
    f.write_text("\n".join(f"row {i}" for i in range(1, 51)) + "\n", encoding="utf-8")
    out = tools._read_file({"file_path": str(f), "offset": 10, "limit": 3})
    assert out.splitlines()[:3] == ["  10 | row 10", "  11 | row 11", "  12 | row 12"]
    assert "lines 10-12 of 51" in out
    assert "past the end" in tools._read_file({"file_path": str(f), "offset": 999})


def test_read_file_says_when_a_file_is_gbk_crlf(tmp_path):
    f = tmp_path / "a.txt"
    f.write_bytes("中文\r\n".encode("gbk"))
    out = tools._read_file({"file_path": str(f)})
    assert "   1 | 中文" in out and "GBK" in out and "CRLF" in out


def test_read_file_refuses_binaries_and_directories(tmp_path):
    (tmp_path / "x.bin").write_bytes(b"\x00\x01\x02")
    assert "binary" in tools._read_file({"file_path": str(tmp_path / "x.bin")})
    assert "directory" in tools._read_file({"file_path": str(tmp_path)})
