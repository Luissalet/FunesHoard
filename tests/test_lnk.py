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
