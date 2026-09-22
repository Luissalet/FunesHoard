"""Round-trip test for the minimal .lnk (Shell Link) parser against a
fixture we build ourselves in-memory (no binary blob committed to the
repo, and it works identically on Linux and Windows)."""
import struct

from funes_hoard.core.lnk import HAS_LINK_INFO, HAS_NAME, IS_UNICODE, parse_lnk_bytes, resolve_target

HEADER_FORMAT = "<I16sIIQQQIIIHHII"


def build_lnk(local_base_path: str, name: str) -> bytes:
    link_flags = HAS_LINK_INFO | HAS_NAME | IS_UNICODE
    header = struct.pack(
        HEADER_FORMAT,
        0x4C, b"\x00" * 16, link_flags, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0,
    )
    assert len(header) == 0x4C

    path_bytes = local_base_path.encode("latin-1") + b"\x00"
    suffix_bytes = b"\x00"
    header_size = 28
    local_base_path_offset = header_size
    common_path_suffix_offset = header_size + len(path_bytes)
    link_info_size = header_size + len(path_bytes) + len(suffix_bytes)
    link_info = struct.pack(
        "<IIIIIII",
        link_info_size, header_size, 0x1, 0, local_base_path_offset, 0, common_path_suffix_offset,
    ) + path_bytes + suffix_bytes

    name_bytes = name.encode("utf-16-le")
    name_item = struct.pack("<H", len(name)) + name_bytes

    return header + link_info + name_item


def test_parse_lnk_reads_local_base_path():
    target = r"C:\Users\demo\Desktop\Side projects\Atlas\README.md"
    data = build_lnk(target, "README shortcut")
    link = parse_lnk_bytes(data)
    assert link.target_path == target
    assert link.name == "README shortcut"


def test_resolve_target_prefers_absolute_local_base_path():
    target = r"C:\Users\demo\file.py"
    data = build_lnk(target, "file")
    link = parse_lnk_bytes(data)
    assert resolve_target(link) == target


def test_parse_lnk_rejects_too_short_buffer():
    import pytest

    from funes_hoard.core.lnk import LnkParseError

    with pytest.raises(LnkParseError):
        parse_lnk_bytes(b"short")


# --- review regressions ----------------------------------------------------
def build_lnk_unicode(ansi_path: bytes, unicode_path: str, suffix: str = "", ansi_name: bytes = b"") -> bytes:
    """LinkInfo with LinkInfoHeaderSize 0x24, i.e. with the optional
    LocalBasePathOffsetUnicode / CommonPathSuffixOffsetUnicode fields that
    Windows writes for Recent Items, plus an optional non-Unicode NAME."""
    from funes_hoard.core.lnk import HAS_NAME as _HN

    flags = HAS_LINK_INFO | (_HN if ansi_name else 0)  # IS_UNICODE off: StringData is ANSI
    header = struct.pack(HEADER_FORMAT, 0x4C, b"\x00" * 16, flags, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0)
    header_size = 0x24
    ansi = ansi_path + b"\x00"
    ansi_suffix = suffix.encode("ascii") + b"\x00"
    uni = unicode_path.encode("utf-16-le") + b"\x00\x00"
    uni_suffix = suffix.encode("utf-16-le") + b"\x00\x00"
    off_ansi = header_size
    off_suffix = off_ansi + len(ansi)
    off_uni = off_suffix + len(ansi_suffix)
    off_uni_suffix = off_uni + len(uni)
    size = off_uni_suffix + len(uni_suffix)
    info = struct.pack("<IIIIIIIII", size, header_size, 0x1, 0, off_ansi, 0, off_suffix, off_uni, off_uni_suffix)
    info += ansi + ansi_suffix + uni + uni_suffix
    name = (struct.pack("<H", len(ansi_name)) + ansi_name) if ansi_name else b""
    return header + info + name


def test_unicode_local_base_path_wins_over_lossy_ansi():
    path = "C:\\Users\\demo\\Documents\\\u65e5\u672c\u8a9e \u2013 notas.docx"
    link = parse_lnk_bytes(build_lnk_unicode(b"C:\\Users\\demo\\Documents\\??? ? notas.docx", path))
    assert link.target_path == path


def test_common_path_suffix_is_appended():
    link = parse_lnk_bytes(build_lnk_unicode(b"C:\\Users\\demo\\", "C:\\Users\\demo\\", suffix="Atlas\\README.md"))
    assert link.target_path == "C:\\Users\\demo\\Atlas\\README.md"


def test_ansi_string_data_decodes_on_every_platform():
    # "mbcs" only exists on Windows; it used to raise LookupError elsewhere.
    link = parse_lnk_bytes(build_lnk_unicode(b"C:\\a.txt", "C:\\a.txt", ansi_name="Espa\xf1a".encode("cp1252")))
    assert link.name == "Espa\u00f1a"


def test_truncated_file_raises_parse_error_not_struct_error():
    import pytest

    from funes_hoard.core.lnk import LnkParseError

    data = build_lnk_unicode(b"C:\\a.txt", "C:\\a.txt")
    for cut in (0x4C + 2, 0x4C + 10, len(data) - 3):
        with pytest.raises(LnkParseError):
            parse_lnk_bytes(data[:cut])
