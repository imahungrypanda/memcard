import hashlib
import io
import zipfile

import pytest

from memcard import install
from memcard.errors import MemcardError
from memcard.paths import Dirs


@pytest.fixture
def site(tmp_path, monkeypatch):
    """A fake downloads.rclone.org on disk, served via file:// URLs."""
    v = "v9.9.9"
    root = tmp_path / "site"
    (root / v).mkdir(parents=True)
    (root / "version.txt").write_text(f"rclone {v}\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"rclone-{v}-linux-amd64/rclone", f"#!/bin/sh\necho 'rclone {v}'\n")
        z.writestr(f"rclone-{v}-linux-amd64/README.txt", "docs")
    data = buf.getvalue()
    (root / v / f"rclone-{v}-linux-amd64.zip").write_bytes(data)
    (root / v / "SHA256SUMS").write_text(
        "-----BEGIN PGP SIGNED MESSAGE-----\n\n"
        f"{hashlib.sha256(data).hexdigest()}  rclone-{v}-linux-amd64.zip\n"
    )
    monkeypatch.setattr(install, "BASE", f"file://{root}/")
    monkeypatch.setattr(install.platform, "system", lambda: "Linux")
    monkeypatch.setattr(install.platform, "machine", lambda: "x86_64")
    return root, v


def test_installs_only_the_verified_binary(site):
    info = install.install_rclone(Dirs.resolve())
    dest = install.bin_dir(Dirs.resolve()) / "rclone"
    assert info["version"] == "v9.9.9" and info["path"] == str(dest)
    assert dest.stat().st_mode & 0o111
    assert sorted(p.name for p in dest.parent.iterdir()) == ["rclone"]


def test_checksum_mismatch_installs_nothing(site):
    root, v = site
    (root / v / f"rclone-{v}-linux-amd64.zip").write_bytes(b"tampered")
    with pytest.raises(MemcardError, match="checksum"):
        install.install_rclone(Dirs.resolve())
    assert not install.bin_dir(Dirs.resolve()).exists()


def test_odd_version_string_is_refused(site):
    root, _ = site
    (root / "version.txt").write_text("rclone ../../etc\n")
    with pytest.raises(MemcardError, match="unexpected version"):
        install.install_rclone(Dirs.resolve())


def test_installed_rclone_is_found(site, monkeypatch):
    from memcard.transport import proc

    monkeypatch.setattr(proc.shutil, "which", lambda name: None)
    install.install_rclone(Dirs.resolve())
    assert proc.find_tool("rclone") == str(install.bin_dir(Dirs.resolve()) / "rclone")


def test_empty_version_file_is_a_clean_error(site):
    root, _ = site
    (root / "version.txt").write_text("")
    with pytest.raises(MemcardError, match="unexpected version"):
        install.install_rclone(Dirs.resolve())


def test_memcards_own_rclone_wins_over_path(site, monkeypatch, tmp_path):
    from memcard.transport import proc

    other = tmp_path / "old-rclone"
    monkeypatch.setattr(proc.shutil, "which", lambda name: str(other))
    install.install_rclone(Dirs.resolve())
    assert proc.find_tool("rclone") == str(install.bin_dir(Dirs.resolve()) / "rclone")


def test_downloads_identify_as_memcard(monkeypatch):
    """downloads.rclone.org refuses Python's default User-Agent with a 403."""
    seen = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(request, timeout):
        seen.append(request.get_header("User-agent"))
        return Response(b"rclone v1.0.0")

    monkeypatch.setattr(install.urllib.request, "urlopen", fake_urlopen)
    assert install._fetch("https://downloads.rclone.org/version.txt", 1024) == b"rclone v1.0.0"
    assert seen == [install.USER_AGENT]
    assert seen[0].startswith("memcard/")
