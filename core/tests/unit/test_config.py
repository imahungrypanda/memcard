import tomllib

import pytest

from memcard import config
from memcard.errors import ConfigError


def write(tmp_path, body):
    p = tmp_path / "config.toml"
    p.write_text('[machine]\nid = "m1"\nname = "Deck"\n' + body)
    return p


def test_initial_template_round_trips(tmp_path):
    p = tmp_path / "c" / "config.toml"
    mid = config.write_initial(p, "Steam Deck")
    cfg = config.load(p)
    assert cfg.machine_id == mid
    assert cfg.machine_name == "Steam Deck"
    assert cfg.remote is None
    assert cfg.groups == []
    assert cfg.sync.trash_versions == 5


def test_initial_refuses_to_overwrite(tmp_path):
    p = tmp_path / "config.toml"
    config.write_initial(p, "Deck")
    with pytest.raises(ConfigError):
        config.write_initial(p, "Deck")


def test_group_needs_exactly_one_of_path_or_file(tmp_path):
    with pytest.raises(ConfigError, match="exactly one"):
        config.load(write(tmp_path, '[[group]]\nid = "a"\n'))
    with pytest.raises(ConfigError, match="exactly one"):
        config.load(write(tmp_path, '[[group]]\nid = "a"\npath = "/x"\nfile = "/x/y"\n'))


def test_bad_unit_and_id_rejected(tmp_path):
    with pytest.raises(ConfigError, match="unit"):
        config.load(write(tmp_path, '[[group]]\nid = "a"\npath = "/x"\nunit = "bogus"\n'))
    with pytest.raises(ConfigError, match="id"):
        config.load(write(tmp_path, '[[group]]\nid = "Has Spaces"\npath = "/x"\n'))


def test_duplicate_ids_and_paths_rejected(tmp_path):
    with pytest.raises(ConfigError, match="share the id"):
        config.load(write(tmp_path, '[[group]]\nid = "a"\npath = "/x"\n[[group]]\nid = "a"\npath = "/y"\n'))
    with pytest.raises(ConfigError, match="same path"):
        config.load(write(tmp_path, '[[group]]\nid = "a"\npath = "/x"\n[[group]]\nid = "b"\npath = "/x"\n'))


def test_specific_group_is_carved_out_of_broader_one(tmp_path):
    cfg = config.load(write(tmp_path, """
[[group]]
id = "retroarch"
path = "/saves/retroarch"
unit = "file"
include = ["*.srm"]

[[group]]
id = "firered"
file = "/saves/retroarch/gba/FireRed.srm"
appids = [42]
"""))
    ra, fr = cfg.group("retroarch"), cfg.group("firered")
    assert ra.carve_outs == ["gba/FireRed.srm"]
    assert not ra.wants("gba/FireRed.srm")
    assert ra.wants("gba/Emerald.srm")
    assert not ra.wants("gba/Emerald.state")
    assert fr.wants("FireRed.srm") and not fr.wants("Other.srm")
    assert cfg.groups_for_appid(42) == [fr]


def test_patterns_without_slash_match_basename_at_any_depth():
    g = config.Group(id="g", root=config.Path("/r"), include=["*.srm"], exclude=["bak/*"])
    assert g.wants("a/b/c.srm")
    assert not g.wants("bak/c.srm")


def test_unit_keys():
    root = config.Path("/r")
    assert config.Group(id="g", root=root, unit="group").unit_key("a/b") == ""
    assert config.Group(id="g", root=root, unit="file").unit_key("a/b") == "a/b"
    assert config.Group(id="g", root=root, unit="dir").unit_key("a/b") == "a"
    assert config.Group(id="g", root=root, single="x", unit="file").unit_key("x") == ""


def test_writers_preserve_comments(tmp_path):
    p = tmp_path / "config.toml"
    config.write_initial(p, "Deck")
    config.set_remote(p, "nextcloud", "memcard", provider="nextcloud")
    config.append_groups(p, [{"id": "ppsspp", "path": "/s/ppsspp", "unit": "dir", "appids": []}])
    config.set_appids(p, "ppsspp", [7, 3, 7])
    text = p.read_text()
    assert "# generated; don't edit" in text
    cfg = config.load(p)
    assert cfg.remote.spec == "nextcloud:memcard"
    assert cfg.group("ppsspp").appids == [3, 7]
    config.set_remote(p, "other", "/saves/")
    assert config.load(p).remote.spec == "other:/saves"
    assert "provider" not in tomllib.loads(p.read_text())["remote"]
