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

"""Toolchain values and pairing rules, independent of discovery and the UI."""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


MINGW = "mingw"
MSVC = "msvc"


class _DescendingVersion:
    __slots__ = ("parts",)

    def __init__(self, text: str):
        self.parts = version_key(text)

    def __lt__(self, other: "_DescendingVersion") -> bool:
        return self.parts > other.parts

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _DescendingVersion) and self.parts == other.parts


@dataclass(frozen=True)
class Toolchain:
    """
    A Qt kit or a compiler. MinGW is described by its GCC version, MSVC by its cl.exe version.
    """

    directory: Path
    arch: str
    gcc: str = ""
    version: str = ""
    kind: str = MINGW
    msvc: str = ""
    name: str = ""
    toolset: str = ""
    host: str = ""

    @property
    def label(self) -> str:
        return self.name or self.directory.parent.name

    def matches(self, compiler: "Toolchain") -> bool:
        if not self.arch or self.arch != compiler.arch or self.kind != compiler.kind:
            return False
        if self.kind == MSVC:
            return bool(compiler.msvc) and version_key(compiler.msvc) >= version_key(self.msvc)
        return not self.gcc or not compiler.gcc or self.gcc == compiler.gcc


def version_key(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", text))


def matching_compiler(kit: Toolchain, compilers: Iterable[Toolchain]) -> Toolchain | None:
    nearby_tools = [parent / "Tools" for parent in kit.directory.parents[:3]]
    return min(
        (compiler for compiler in compilers if kit.matches(compiler)),
        key=lambda compiler: (
            bool(kit.gcc and compiler.gcc != kit.gcc),
            kit.kind == MINGW and not compiler.gcc,
            not any(compiler.directory.is_relative_to(location) for location in nearby_tools),
            _DescendingVersion(compiler.msvc),
            str(compiler.directory).casefold(),
        ),
        default=None,
    )
