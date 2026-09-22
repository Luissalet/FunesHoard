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
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_HEADER_SIZE = 0x4C
_CLSID = bytes.fromhex("01140200000000000000000000000046")  # unused, header id is fixed size check below

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
        text = raw.decode("mbcs" if hasattr(bytes, "decode") else "latin-1", errors="replace")
        offset += count
    return text, offset


def parse_lnk_bytes(buf: bytes) -> ShellLink:
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
        (link_info_size,) = struct.unpack_from("<I", buf, offset)
        (link_info_header_size,) = struct.unpack_from("<I", buf, offset + 4)
        (link_info_flags,) = struct.unpack_from("<I", buf, offset + 8)
        # LinkInfo layout (relative to link_info_start): 0 LinkInfoSize,
        # 4 LinkInfoHeaderSize, 8 LinkInfoFlags, 12 VolumeIDOffset,
        # 16 LocalBasePathOffset, 20 CommonNetworkRelativeLinkOffset,
        # 24 CommonPathSuffixOffset. (MS-SHLLINK 2.3)
        (local_base_path_offset,) = struct.unpack_from("<I", buf, offset + 16)
        if link_info_flags & 0x1 and local_base_path_offset:
            abs_off = link_info_start + local_base_path_offset
            end = buf.find(b"\x00", abs_off)
            if end == -1:
                end = len(buf)
            local_base_path = buf[abs_off:end].decode("mbcs" if False else "latin-1", errors="replace")
        offset = link_info_start + link_info_size

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
