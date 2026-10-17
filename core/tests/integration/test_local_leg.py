"""Real rsync against an EmuDeck-shaped tree full of symlinks."""

import os
from pathlib import Path

import pytest

from memcard import scan
from memcard.config import Group
from memcard.errors import LocalPathMissing
from memcard.store.trash import Trash
from memcard.transport import rsync

pytestmark = pytest.mark.integration


@pytest.fixture
def emudeck(tmp_path):
    """sd/Emulation/saves/retroarch/saves -> flatpak/retroarch/saves, like EmuDeck does it."""
    real = tmp_path / "flatpak/retroarch/saves"
    (real / "gba").mkdir(parents=True)
    (real / "gba/FireRed.srm").write_bytes(b"A" * 128)
    (real / "loop").symlink_to(real)  # must not hang the scan
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "real-name.srm").write_bytes(b"linked")
    (real / "linked.srm").symlink_to(elsewhere / "real-name.srm")
    emu = tmp_path / "sd/Emulation/saves/retroarch"
    emu.mkdir(parents=True)
    (emu / "saves").symlink_to(real)
    return tmp_path, emu, real


def test_scan_follows_symlinks_without_looping(emudeck):
    _, emu, _ = emudeck
    g = Group(id="ra", root=emu / "saves", unit="file", include=["*.srm"])
    files = scan.scan(g)
    assert set(files) == {"gba/FireRed.srm", "linked.srm"}
    assert files["gba/FireRed.srm"].size == 128


def test_missing_root_is_reported_not_treated_as_empty(tmp_path):
    with pytest.raises(LocalPathMissing):
        scan.walk(Group(id="g", root=tmp_path / "unmounted"))


def test_stage_dereferences_symlinks(emudeck, tmp_path):
    _, emu, _ = emudeck
    out = tmp_path / "staging"
    rsync.stage(emu, ["saves/gba/FireRed.srm", "saves/linked.srm"], out)
    assert not (out / "saves").is_symlink()
    assert (out / "saves/gba/FireRed.srm").read_bytes() == b"A" * 128
    assert not (out / "saves/linked.srm").is_symlink()
    assert (out / "saves/linked.srm").read_bytes() == b"linked"


def test_restore_writes_through_symlinked_directory(emudeck, tmp_path):
    _, emu, real = emudeck
    src = tmp_path / "incoming"
    (src / "saves/gba").mkdir(parents=True)
    (src / "saves/gba/FireRed.srm").write_bytes(b"B" * 128)
    rsync.restore(src, ["saves/gba/FireRed.srm"], emu)
    assert (emu / "saves").is_symlink(), "restore replaced the symlink and detached the emulator"
    assert (real / "gba/FireRed.srm").read_bytes() == b"B" * 128


def test_restore_writes_through_symlinked_file(emudeck, tmp_path):
    root, emu, real = emudeck
    src = tmp_path / "incoming"
    src.mkdir()
    (src / "linked.srm").write_bytes(b"new linked")
    rsync.restore(src, ["linked.srm"], emu / "saves")
    assert (real / "linked.srm").is_symlink()
    assert (root / "elsewhere/real-name.srm").read_bytes() == b"new linked"
    assert not (root / "elsewhere/linked.srm").exists()


def test_same_size_same_mtime_change_is_not_skipped(tmp_path):
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    dest.mkdir()
    (src / "s.srm").write_bytes(b"new!")
    (dest / "s.srm").write_bytes(b"old!")
    os.utime(src / "s.srm", (1_000_000, 1_000_000))
    os.utime(dest / "s.srm", (1_000_000, 1_000_000))
    rsync.restore(src, ["s.srm"], dest)
    assert (dest / "s.srm").read_bytes() == b"new!"


def test_trash_keeps_n_versions_and_never_prunes_kept_alternates(tmp_path):
    f = tmp_path / "s.srm"
    f.write_bytes(b"x")
    trash = Trash(tmp_path / "trash", keep=2)
    kept = trash.new_batch("g", kept=True)
    trash.put(kept, f, "s.srm")
    for _ in range(4):
        b = trash.new_batch("g")
        trash.put(b, f, "s.srm")
    trash.prune("g")
    batches = trash.batches("g")
    assert sum(not b.kept for b in batches) == 2
    assert any(b.kept for b in batches)
    assert batches[0].files() == ["s.srm"]
