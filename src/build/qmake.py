# SPDX-License-Identifier: GPL-3.0-or-later

"""qmake configuration arguments and evaluated Makefile metadata."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .paths import deployment_path
from .qml import QML_SOURCE_COPY


# Record the evaluated build settings and stage out-of-tree destinations before
# the Makefile generator can create them.
QMAKE_SETTINGS = r"""qtpackage_build_settings = release
CONFIG(debug, debug|release): qtpackage_build_settings = debug
qtpackage_build_settings += $$QML_IMPORT_PATH
write_file($$OUT_PWD/.qt-settings-$$BUILD_PASS, qtpackage_build_settings)
!isEmpty(RESOURCES): write_file($$OUT_PWD/.qt-resources-$$BUILD_PASS, RESOURCES)

!isEmpty(DESTDIR) {
    qtpackage_relative = $$relative_path($$DESTDIR, $$OUT_PWD)
    contains(qtpackage_relative, "^(\\.\\.(/.*)?|[A-Za-z]:.*|/.*)$") {
        !write_file($$OUT_PWD/.qt-destdir-$$BUILD_PASS, DESTDIR): error(Could not save DESTDIR)
        !isEmpty(QMAKE_POST_LINK) {
            qtpackage_absolute = $$absolute_path($$DESTDIR, $$OUT_PWD)
            qtpackage_boundary = "(?=$|[/\\\\\"])"
            qtpackage_link_command = $$join(QMAKE_POST_LINK, " ")
            qtpackage_paths = "(?:$$re_escape($$qtpackage_relative)|$$re_escape($$shell_path($$qtpackage_relative)))$${qtpackage_boundary}"
            qtpackage_link_command ~= "s>$${qtpackage_paths}>.qt-deployment>gi"
            qtpackage_paths = "(?:$$re_escape($$DESTDIR)|$$re_escape($$qtpackage_absolute)|$$re_escape($$shell_path($$qtpackage_absolute)))$${qtpackage_boundary}"
            qtpackage_link_command ~= "s>$${qtpackage_paths}>$$OUT_PWD/.qt-deployment>gi"
            QMAKE_POST_LINK = $$qtpackage_link_command
        }
        DESTDIR = .qt-deployment
    }
}"""

QMAKE_INTERMEDIATE_NAMES = {
    ".qt-intermediates",
    "makefile", "makefile.release", "makefile.debug", ".qmake.stash", ".qt-shadow.pri",
    ".qt-settings-", ".qt-settings-release", ".qt-settings-debug",
    ".qt-resources-", ".qt-resources-release", ".qt-resources-debug",
    ".qt-destdir-", ".qt-destdir-release", ".qt-destdir-debug",
    ".qt-qml-check.qrc", ".qt-selected-qml", QML_SOURCE_COPY,
}


@dataclass(frozen=True)
class QMakeTarget:
    makefile: Path
    artifact: Path
    import_paths: list[str]
    resource_collections: list[str]
    publication_dir: Path


def is_qmake_intermediate(name: str) -> bool:
    """Whether a file in the qmake output folder is build input rather than package content."""
    name = name.casefold()
    # MinGW link scripts and the resource script generated for RC_ICONS or VERSION.
    return name in QMAKE_INTERMEDIATE_NAMES or name.startswith("object_script.") or name.endswith("_resource.rc")


def read_qmake_file(path: Path, *, for_make: bool = False) -> str:
    contents = path.read_bytes()
    try:
        text = contents.decode("utf-8")
    except UnicodeDecodeError:
        return contents.decode("mbcs")

    if for_make and not contents.isascii():
        # qmake writes UTF-8. MinGW make and nmake read paths using the ANSI code page.
        try:
            encoded = text.encode("mbcs")
        except UnicodeEncodeError as exc:
            raise ValueError(
                f"{path.name} contains characters that the Windows code page cannot represent."
            ) from exc
        if encoded != contents:
            path.write_bytes(encoded)
    return text


def configure_arguments(project: Path, build_root: Path, is_release: bool) -> list[str]:
    (build_root / ".qt-shadow.pri").write_text(QMAKE_SETTINGS, encoding="ascii")
    config_type = "release" if is_release else "debug"
    qmake_input = str(project)
    if not qmake_input.isascii() and project.drive == build_root.drive:
        relative_input = os.path.relpath(project, build_root)
        if relative_input.isascii():
            qmake_input = relative_input
    args = [
        "-o",
        "Makefile",
        "CONFIG-=release",
        "CONFIG-=debug",
        f"CONFIG+={config_type}",
        qmake_input,
        "-after",
        "OBJECTS_DIR=.qt-intermediates/obj",
        "MOC_DIR=.qt-intermediates/moc",
        "RCC_DIR=.qt-intermediates/rcc",
        "UI_DIR=.qt-intermediates/ui",
        "include($$OUT_PWD/.qt-shadow.pri):QTPACKAGE_SHADOW=1",
    ]
    return args


def read_target(root: Path, output_root: Path, is_release: bool) -> QMakeTarget:
    makefile = deployment_path(root, "Makefile")
    contents = read_qmake_file(makefile, for_make=True)
    target_pattern = r"(?m)^DESTDIR_TARGET[ \t]*=[ \t]*(.+)$"
    target = re.search(target_pattern, contents)
    mode = "release" if is_release else "debug"
    build_pass = ""
    if target is None and (root / f"Makefile.{mode.capitalize()}").is_file():
        build_pass = mode.capitalize()
        makefile = deployment_path(root, f"Makefile.{build_pass}")
        target = re.search(
            target_pattern, read_qmake_file(makefile, for_make=True)
        )
    if not target:
        raise ValueError("qmake did not generate a single executable target.")
    settings_file = deployment_path(root, f".qt-settings-{build_pass}")
    if not settings_file.is_file():
        raise ValueError("qmake did not report the generated build settings.")
    settings = read_qmake_file(settings_file).splitlines()
    actual_mode = settings[0].strip() if settings else ""
    if actual_mode != mode:
        raise ValueError(f"Project build mode {actual_mode!r} conflicts with requested {mode!r}.")
    import_paths = settings[1:]
    resources_file = deployment_path(root, f".qt-resources-{build_pass}")
    resource_collections = (
        read_qmake_file(resources_file).splitlines() if resources_file.is_file() else []
    )

    relative = Path(target.group(1).strip().strip('"'))
    if relative.is_absolute():
        normalized = Path(os.path.normpath(relative))
        if not normalized.is_relative_to(root):
            raise ValueError(f"Executable target is outside the output directory: {normalized}")
        relative = normalized.relative_to(root)
    if relative.anchor or relative.suffix.lower() != ".exe":
        raise ValueError(f"Invalid executable target in {makefile.name}: {relative}")
    deployment_path(root, relative.as_posix())

    metadata = deployment_path(root, f".qt-destdir-{build_pass}")
    publication = relative.parent
    if metadata.is_file():
        intended = Path(read_qmake_file(metadata).strip().strip('"'))
        intended = intended if intended.is_absolute() else root / intended
        normalized = Path(os.path.normpath(intended))
        if not normalized.is_relative_to(output_root):
            raise ValueError(f"Executable target is outside the output directory: {normalized}")
        publication = normalized.relative_to(output_root)
    if not publication.parts:
        publication = Path(mode)
    publication_dir = deployment_path(output_root, publication.as_posix())
    return QMakeTarget(makefile, relative, import_paths, resource_collections, publication_dir)
