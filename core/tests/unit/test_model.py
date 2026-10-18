import pytest

from memcard.model import FileEntry, Manifest, check_relpath

GOOD = "a" * 64


def doc(files):
    return {"format": 1, "group": "g", "generation": 1, "parent": None, "committed_at": "t", "committed_by": "m", "files": files}


@pytest.mark.parametrize("path", ["", "/etc/passwd", "../x", "a/../../b", "a//b", "./a", "a/.", "a\\b", "a\0b", "a/"])
def test_unsafe_manifest_paths_are_rejected(path):
    with pytest.raises(ValueError):
        Manifest.from_dict(doc({path: {"sha256": GOOD, "size": 1}}))


@pytest.mark.parametrize("path", ["s.srm", "gba/Pokemon FireRed.srm", "Card A/x..y", ".hidden", "ULUS10041/DATA.BIN"])
def test_ordinary_paths_are_accepted(path):
    assert check_relpath(path) == path


@pytest.mark.parametrize("sha", ["../../victim", "A" * 64, "a" * 63, "", None, 5])
def test_object_ids_must_be_lowercase_sha256(sha):
    with pytest.raises(ValueError):
        FileEntry.from_dict({"sha256": sha, "size": 1})
