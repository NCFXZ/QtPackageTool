# Qt Package Tool - A PyQt6-based application
# Copyright (c) 2025 NCFXZ
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""Read Windows PE architecture and load-time imports without Qt dependencies."""

import mmap
import struct
from pathlib import Path


def _pe_imports(image: mmap.mmap) -> tuple[int, list[str]]:
    if image[:2] != b"MZ":
        raise ValueError("Missing DOS header")
    pe, = struct.unpack_from("<I", image, 0x3C)
    if image[pe:pe + 4] != b"PE\0\0":
        raise ValueError("Missing PE signature")
    machine, section_count, _, _, _, optional_size, _ = struct.unpack_from("<HHIIIHH", image, pe + 4)
    optional = pe + 24
    magic, = struct.unpack_from("<H", image, optional)
    directories = {0x10B: 96, 0x20B: 112}.get(magic)
    if directories is None or optional_size < directories:
        raise ValueError("Invalid PE optional header")
    directory_count, = struct.unpack_from("<I", image, optional + directories - 4)
    if directory_count < 2:
        return machine, []
    if optional_size < directories + 16:
        raise ValueError("Truncated PE import directory")
    import_rva, import_size = struct.unpack_from("<II", image, optional + directories + 8)
    if import_rva == 0:
        return machine, []

    section_table = optional + optional_size
    sections = [
        struct.unpack_from("<III", image, section_table + number * 40 + 12)
        for number in range(section_count)
    ]

    def file_range(rva: int) -> tuple[int, int]:
        for address, size, offset in sections:
            if address <= rva < address + size and offset + size <= len(image):
                return offset + rva - address, offset + size
        raise ValueError("Import data reference outside the PE sections")

    start, section_end = file_range(import_rva)
    end = min(section_end, start + import_size) if import_size else section_end
    imports = []
    for descriptor in range(start, end - 19, 20):
        fields = struct.unpack_from("<5I", image, descriptor)
        if not any(fields):
            return machine, imports
        offset, end_of_name = file_range(fields[3])
        terminator = image.find(b"\0", offset, end_of_name)
        if terminator <= offset:
            raise ValueError("Invalid imported library name")
        imports.append(image[offset:terminator].decode("mbcs"))
    if import_size and imports and (end - start) == len(imports) * 20:
        return machine, imports
    raise ValueError("Unterminated PE import directory")


def read_pe_imports(binary: Path) -> tuple[int, list[str]]:
    try:
        with binary.open("rb") as stream, mmap.mmap(stream.fileno(), 0, access=mmap.ACCESS_READ) as image:
            return _pe_imports(image)
    except (ValueError, struct.error) as exc:
        raise ValueError(f"Could not read PE imports from {binary.name}: {exc}") from exc
