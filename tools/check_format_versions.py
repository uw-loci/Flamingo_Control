#!/usr/bin/env python3
"""Check whether the file-format ecosystem has moved past what we pin.

Three of our storage decisions are waiting on upstream releases, and none of
them announce themselves in a way we would notice:

* **OME-NGFF 1.0.** We write OME-Zarr v0.5 today. The spec roadmap points at
  1.0 around the end of 2026, and that release is the gate on revisiting
  xarray and Icechunk (``claude-reports/2026-10-04-xarray-icechunk-todo.md``,
  outside this repo).
* **zarr 4.** ``requirements.txt`` pins ``zarr>=3.1.4,<4``. A major bump needs
  a deliberate look, not a silent resolver upgrade.
* **Icechunk.** Reported for reference. It is already past 2.0, so maturity is
  not the gate -- Fiji not reading an Icechunk store is, and that will not show
  up as a version number.

Run from CI on a schedule: the script exits non-zero when something we are
waiting for has actually landed, so a failed scheduled run is the alert. A
network or parse failure exits 0 -- an unreachable PyPI is not news.

    python tools/check_format_versions.py
"""

import json
import os
import sys
import urllib.error
import urllib.request

TIMEOUT_S = 30
USER_AGENT = "Flamingo_Control format-version watch"


def _get_json(url: str):
    """Fetch and decode JSON, or return None if anything goes wrong."""
    headers = {"User-Agent": USER_AGENT}
    # Unauthenticated GitHub API calls are rate limited per IP, and CI runners
    # share IPs. Without a token the NGFF check would be the first thing to get
    # throttled -- silently, since a failed fetch is treated as "no news".
    token = os.environ.get("GITHUB_TOKEN")
    if token and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"  could not read {url}: {exc}")
        return None


def _release_parts(text: str):
    """Leading numeric components of a version, or None if it is a prerelease.

    ``"0.5.2"`` -> ``(0, 5, 2)``; ``"0.6rc0"`` and ``"1.0.0a1"`` -> None, so a
    release candidate never trips the alert.
    """
    parts = []
    for chunk in text.strip().lstrip("v").split("."):
        if not chunk.isdigit():
            return None
        parts.append(int(chunk))
    return tuple(parts) if parts else None


def pypi_version(package: str):
    data = _get_json(f"https://pypi.org/pypi/{package}/json")
    if not data:
        return None
    return data.get("info", {}).get("version")


def newest_github_release_tag(repo: str):
    """Highest non-prerelease tag in a GitHub repo, as (text, parts)."""
    data = _get_json(f"https://api.github.com/repos/{repo}/tags?per_page=100")
    if not isinstance(data, list):
        return None, None
    best_text, best_parts = None, None
    for tag in data:
        name = tag.get("name", "")
        parts = _release_parts(name)
        if parts is None:
            continue
        if best_parts is None or parts > best_parts:
            best_text, best_parts = name, parts
    return best_text, best_parts


def main() -> int:
    alerts = []

    print("OME-NGFF spec (ome/ngff tags) -- we write OME-Zarr v0.5")
    tag, parts = newest_github_release_tag("ome/ngff")
    if parts is None:
        print("  no readable tag; skipping")
    else:
        print(f"  newest released tag: {tag}")
        if parts[0] >= 1:
            alerts.append(
                f"OME-NGFF {tag} is out. This is the release the xarray and "
                "Icechunk evaluation was deferred to: re-read "
                "claude-reports/2026-10-04-xarray-icechunk-todo.md, and check "
                "what Fiji/BigDataViewer reads before changing the output format."
            )

    print("zarr (PyPI) -- requirements.txt pins zarr>=3.1.4,<4")
    version = pypi_version("zarr")
    if version is None:
        print("  no version; skipping")
    else:
        print(f"  newest: {version}")
        parts = _release_parts(version)
        if parts and parts[0] >= 4:
            alerts.append(
                f"zarr {version} is out, past our <4 pin. Nothing upgrades on "
                "its own, but the pin now holds back a major version."
            )

    # Reference only, no alert. Icechunk was already past 2.0 when this watch
    # was written, so its maturity is not what gates us -- Fiji not reading an
    # Icechunk store is, and no version number will announce that changing.
    print("icechunk (PyPI) -- reference only; the gate is Fiji, not maturity")
    print(f"  newest: {pypi_version('icechunk') or 'unknown'}")

    print("ngff-zarr (PyPI) -- reference; the dask exclusion lives in requirements.txt")
    print(f"  newest: {pypi_version('ngff-zarr') or 'unknown'}")

    if not alerts:
        print("\nNothing we are waiting for has landed.")
        return 0

    print("\n" + "=" * 70)
    for line in alerts:
        print(f"ALERT: {line}")
    print("=" * 70)
    return 1


if __name__ == "__main__":
    sys.exit(main())
