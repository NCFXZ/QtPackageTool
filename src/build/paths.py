# SPDX-License-Identifier: GPL-3.0-or-later

"""Validate package paths and resource entries without following linked destinations."""

import os
import re
import stat
from pathlib import Path


def entry_status(path: Path) -> os.stat_result | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    # Windows name-surrogate reparse points redirect access, including junctions.
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0) & 0x20000000:
        raise ValueError(f"Linked deployment path is not allowed: {path}")
    return info


def deployment_path(root: Path, destination: str) -> Path:
    # The UI and dependency config use one leading slash for the package root.
    relative = Path(destination.removeprefix("/"))
    if not relative.parts or relative.anchor or any(
        part.endswith((".", " ")) or re.search(r'[<>:"|?*]', part) or Path(part).is_reserved()
        for part in relative.parts
    ):
        raise ValueError(f"Invalid path inside the package: {destination}")

    target = root
    entry_status(target)
    for part in relative.parts:
        target = target / part
        entry_status(target)
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"Destination is outside the package: {destination}")
    return target


def resource_source(entry: dict) -> Path:
    source, destination = entry.get("Source"), entry.get("Destination")
    if not source or not destination:
        raise ValueError(f"Resource source and destination are required: {entry}")
    path = Path(source)
    info = entry_status(path)
    if info is None:
        raise FileNotFoundError(f"Required resource not found: {path}")
    if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
        raise ValueError(f"Unsupported resource type: {path}")
    return path.resolve(strict=True)


def remove_readonly(remove, path: str, failure) -> None:
    error = failure[1]
    if not isinstance(error, PermissionError):
        raise error
    info = entry_status(Path(path))
    if info is None or not info.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY:
        raise error
    if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
        raise error
    os.chmod(path, stat.S_IWRITE)
    remove(path)
