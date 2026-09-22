"""Minimal Windows Shell Link (.lnk) parser.

We only need the resolved target path, so this implements just enough of
MS-SHLLINK to get it: the header, skip the optional LinkTargetIDList, read
the LinkInfo block's LocalBasePath (absolute local target) and fall back
to the StringData's NAME_STRING/relative path when LinkInfo is absent or
network-based. No external dependency: some `.lnk` readers pull in a heavy
COM-based library for this, we do not need one for a resolved path.

Reference: [MS-SHLLINK] 2.1 ShellLinkHeader, 2.2 LinkTargetIDList,
2.3 LinkInfo, 2.4 StringData.
"""
from __future__ import annotations

import struct
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_HEADER_SIZE = 0x4C
# Non-Unicode strings in a .lnk use the system ANSI code page. "mbcs" is
# exactly that on Windows but does not exist elsewhere; cp1252 is the ANSI
# page of Western-European Windows installs and a safe stand-in for tests.
_ANSI = "mbcs" if sys.platform == "win32" else "cp1252"

# LinkFlags bits we care about (2.1.1)
HAS_LINK_TARGET_ID_LIST = 0x0001
HAS_LINK_INFO = 0x0002
HAS_NAME = 0x0004
HAS_RELATIVE_PATH = 0x0008
HAS_WORKING_DIR = 0x0010
HAS_ARGUMENTS = 0x0020
IS_UNICODE = 0x0080


class LnkParseError(ValueError):
    pass


@dataclass
class ShellLink:
    target_path: Optional[str]
    relative_path: Optional[str]
    working_dir: Optional[str]
    name: Optional[str]


def _read_string_data_item(buf: bytes, offset: int, is_unicode: bool) -> tuple[str, int]:
    (count,) = struct.unpack_from("<H", buf, offset)
    offset += 2
    if is_unicode:
        raw = buf[offset : offset + count * 2]
        text = raw.decode("utf-16-le", errors="replace")
        offset += count * 2
    else:
        raw = buf[offset : offset + count]
        text = raw.decode(_ANSI, errors="replace")
        offset += count
    return text, offset


def _c_string(buf: bytes, start: int, end: int) -> str:
    stop = buf.find(b"\x00", start, end)
    return buf[start: stop if stop != -1 else end].decode(_ANSI, errors="replace")


def _c_wstring(buf: bytes, start: int, end: int) -> str:
    i = start
    while i + 1 < end and buf[i:i + 2] != b"\x00\x00":
        i += 2
    return buf[start:i].decode("utf-16-le", errors="replace")


def parse_lnk_bytes(buf: bytes) -> ShellLink:
    """Parse a Shell Link; any malformed or truncated input -> LnkParseError."""
    try:
        return _parse(buf)
    except (struct.error, IndexError) as exc:
        raise LnkParseError(f"truncated or malformed shell link: {exc}") from exc


def _parse(buf: bytes) -> ShellLink:
    if len(buf) < _HEADER_SIZE:
        raise LnkParseError("file too short for a ShellLinkHeader")
    header_size, guid = struct.unpack_from("<I16s", buf, 0)
    if header_size != _HEADER_SIZE:
        raise LnkParseError(f"unexpected HeaderSize {header_size:#x}")
    (link_flags,) = struct.unpack_from("<I", buf, 20)
    is_unicode = bool(link_flags & IS_UNICODE)

    offset = _HEADER_SIZE

    if link_flags & HAS_LINK_TARGET_ID_LIST:
        (id_list_size,) = struct.unpack_from("<H", buf, offset)
        offset += 2 + id_list_size

    local_base_path: Optional[str] = None
    if link_flags & HAS_LINK_INFO:
        link_info_start = offset
        # LinkInfo layout (relative to its start, MS-SHLLINK 2.3): 0 Size,
        # 4 HeaderSize, 8 Flags, 12 VolumeIDOffset, 16 LocalBasePathOffset,
        # 20 CommonNetworkRelativeLinkOffset, 24 CommonPathSuffixOffset and,
        # when HeaderSize >= 0x24, 28 LocalBasePathOffsetUnicode and
        # 32 CommonPathSuffixOffsetUnicode.
        size, header_size, info_flags, _vol, base_off, _net, suffix_off = struct.unpack_from("<7I", buf, offset)
        end = link_info_start + size
        if size < 28 or end > len(buf):
            raise LnkParseError("LinkInfo runs past the end of the file")
        if info_flags & 0x1 and base_off:
            base_uni_off = suffix_uni_off = 0
            if header_size >= 0x24:
                base_uni_off, suffix_uni_off = struct.unpack_from("<2I", buf, offset + 28)
            if base_uni_off:
                # The Unicode copy is lossless; the ANSI one turns anything
                # outside the code page into "?".
                base = _c_wstring(buf, link_info_start + base_uni_off, end)
                suffix = _c_wstring(buf, link_info_start + suffix_uni_off, end) if suffix_uni_off else ""
            else:
                base = _c_string(buf, link_info_start + base_off, end)
                suffix = _c_string(buf, link_info_start + suffix_off, end) if suffix_off else ""
            local_base_path = base + suffix
        offset = end

    name = None
    relative_path = None
    working_dir = None

    if link_flags & HAS_NAME:
        name, offset = _read_string_data_item(buf, offset, is_unicode)
    if link_flags & HAS_RELATIVE_PATH:
        relative_path, offset = _read_string_data_item(buf, offset, is_unicode)
    if link_flags & HAS_WORKING_DIR:
        working_dir, offset = _read_string_data_item(buf, offset, is_unicode)
    if link_flags & HAS_ARGUMENTS:
        _, offset = _read_string_data_item(buf, offset, is_unicode)

    return ShellLink(
        target_path=local_base_path,
        relative_path=relative_path,
        working_dir=working_dir,
        name=name,
    )


def parse_lnk_file(path: "Path | str") -> ShellLink:
    data = Path(path).read_bytes()
    return parse_lnk_bytes(data)


def resolve_target(link: ShellLink, lnk_dir: Optional[Path] = None) -> Optional[str]:
    """Best-effort absolute path for the link's target."""
    if link.target_path:
        return link.target_path
    if link.relative_path and lnk_dir is not None:
        try:
            return str((lnk_dir / link.relative_path).resolve())
        except OSError:
            return link.relative_path
    return link.relative_path
