"""Fetch rclone for hosts where installing packages isn't an option (SteamOS).

Downloads the official static build, checks it against rclone's published
SHA256SUMS, and puts the single binary in Memcard's own data dir, where
`transport.proc.find_tool` looks. Nothing outside that dir is touched.
"""

from __future__ import annotations

import hashlib
import io
import os
import platform
import re
import subprocess
import urllib.request
import zipfile
from pathlib import Path

from .errors import MemcardError
from .paths import Dirs

BASE = os.environ.get("MEMCARD_RCLONE_BASE", "https://downloads.rclone.org/")
ARCH = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
MAX_ZIP = 200 * 1024 * 1024


def _fetch(url: str, limit: int = MAX_ZIP) -> bytes:
    try:
        with urllib.request.urlopen(url, timeout=60) as r:
            data = r.read(limit + 1)
    except OSError as e:
        raise MemcardError("Couldn't download rclone.", hint="Check the internet connection and try again.", detail=f"{url}: {e}") from e
    if len(data) > limit:
        raise MemcardError("The rclone download was unexpectedly large; refusing it.", detail=url)
    return data


def bin_dir(dirs: Dirs) -> Path:
    return dirs.data / "bin"


def install_rclone(dirs: Dirs) -> dict:
    if platform.system() != "Linux":
        raise MemcardError("Installing rclone this way only works on Linux.", hint="Install rclone with your package manager.")
    arch = ARCH.get(platform.machine())
    if not arch:
        raise MemcardError(f"No rclone build for this CPU ({platform.machine()}).")

    version = _fetch(BASE + "version.txt", 1024).decode().split()[-1].strip()
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise MemcardError("rclone's download site returned an unexpected version.", detail=version[:100])
    name = f"rclone-{version}-linux-{arch}.zip"
    sums = _fetch(f"{BASE}{version}/SHA256SUMS", 1024 * 1024).decode(errors="replace")
    expected = next((line.split()[0] for line in sums.splitlines() if line.endswith(f"  {name}")), None)
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise MemcardError("Couldn't find rclone's published checksum for this download.", detail=name)

    data = _fetch(f"{BASE}{version}/{name}")
    if hashlib.sha256(data).hexdigest() != expected:
        raise MemcardError("The rclone download didn't match its published checksum; not installing it.", detail=name)

    member = f"{name.removesuffix('.zip')}/rclone"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        binary = z.read(member)
    dest_dir = bin_dir(dirs)
    dest_dir.mkdir(parents=True, exist_ok=True)
    tmp = dest_dir / ".rclone.tmp"
    tmp.write_bytes(binary)
    tmp.chmod(0o755)
    check = subprocess.run([str(tmp), "version"], capture_output=True, text=True)
    if check.returncode != 0 or version not in check.stdout:
        tmp.unlink(missing_ok=True)
        raise MemcardError("The downloaded rclone didn't run.", detail=check.stderr[-1000:])
    dest = dest_dir / "rclone"
    os.replace(tmp, dest)
    return {"version": version, "path": str(dest)}
