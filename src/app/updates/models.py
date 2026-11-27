# SPDX-License-Identifier: GPL-3.0-or-later

"""Validate stable releases and their full or optional matching delta assets."""

import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from app.metadata import FILE_MANIFEST_NAME as MANIFEST_NAME, INSTALLER_NAME, REPOSITORY, UPDATER_NAME as HELPER_NAME


VERSION = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)", re.ASCII)
CHECKSUM = re.compile(r"sha256:([a-fA-F0-9]{64})", re.ASCII)
DOWNLOAD_HOSTS = {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    url: str
    size: int
    sha256: str
    delta: Asset | None = None
    file_manifest: Asset | None = None

    @property
    def full_asset(self) -> Asset:
        return Asset(INSTALLER_NAME, self.url, self.size, self.sha256)


def version_tuple(tag: str) -> tuple[int, int, int]:
    match = VERSION.fullmatch(tag) if isinstance(tag, str) else None
    if match is None:
        raise ValueError("GitHub did not provide a supported release version.")
    return tuple(int(part) for part in match.groups())


def https_host(url: str) -> str:
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "https" or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.port not in (None, 443) or parts.fragment
        ):
            return ""
        return parts.hostname
    except (TypeError, ValueError):
        return ""


def allowed_download_url(url: str, original: str) -> bool:
    host = https_host(url)
    if host in DOWNLOAD_HOSTS:
        return True
    return host == "github.com" and url == original


def _parse_asset(asset: dict, tag: str, name: str) -> Asset:
    if asset.get("state") != "uploaded":
        raise ValueError("GitHub did not provide a compatible Windows x64 installer.")
    size = asset.get("size")
    if type(size) is not int or not 0 < size <= 2 ** 31 - 1:
        raise ValueError("GitHub did not provide a valid installer size.")
    digest = asset.get("digest")
    checksum = CHECKSUM.fullmatch(digest) if isinstance(digest, str) else None
    if checksum is None:
        raise ValueError("GitHub did not provide a valid installer checksum.")
    url = asset.get("browser_download_url")
    expected = f"https://github.com/{REPOSITORY}/releases/download/{tag}/{name}"
    if not isinstance(url, str) or url != expected or https_host(url) != "github.com":
        raise ValueError("GitHub returned an unexpected installer download URL.")
    return Asset(name, url, size, checksum[1].lower())


def _optional_asset(assets: list, tag: str, name: str, maximum: int) -> Asset | None:
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == name]
    if len(matches) != 1:
        return None
    try:
        asset = _parse_asset(matches[0], tag, name)
    except ValueError:
        return None
    return asset if asset.size <= maximum else None


def _matching_delta(assets: list, tag: str, current_version: str | None, full: Asset) -> Asset | None:
    if not isinstance(current_version, str):
        return None
    try:
        if version_tuple("v" + current_version) >= version_tuple(tag):
            return None
    except ValueError:
        return None
    name = f"QtPackageTool-Update-{current_version}-to-{tag[1:]}-x64.zip"
    return _optional_asset(assets, tag, name, full.size - 1)


def parse_release(payload: dict, current_version: str | None = None) -> Release:
    if not isinstance(payload, dict):
        raise ValueError("GitHub returned invalid release information.")
    if payload.get("draft") is not False or payload.get("prerelease") is not False:
        raise ValueError("This update is not a published stable release.")
    tag = payload.get("tag_name")
    version_tuple(tag)
    assets = payload.get("assets")
    matches = [asset for asset in assets if isinstance(asset, dict) and asset.get("name") == INSTALLER_NAME] if isinstance(assets, list) else []
    if len(matches) != 1:
        raise ValueError("GitHub did not provide a compatible Windows x64 installer.")
    full = _parse_asset(matches[0], tag, INSTALLER_NAME)
    notes = payload.get("body") or ""
    if not isinstance(notes, str):
        raise ValueError("GitHub returned invalid release information.")
    manifest = _optional_asset(assets, tag, MANIFEST_NAME, 1024 * 1024)
    delta = _matching_delta(assets, tag, current_version, full) if manifest is not None else None
    return Release(tag[1:], notes, full.url, full.size, full.sha256, delta, manifest)
