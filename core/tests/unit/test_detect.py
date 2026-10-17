from pathlib import Path

import pytest

from memcard import detect, vdf
from tests.vdf_helpers import shortcuts_vdf

SRM_SHORTCUT = {
    "appid": -1234567890,  # Steam stores it as signed int32
    "AppName": "Pokemon FireRed Version",
    "Exe": '"/run/media/deck/FATTY/Emulation/tools/launchers/retroarch.sh"',
    "LaunchOptions": "-L mgba_libretro.so \"'/run/media/deck/FATTY/Emulation/roms/gba/Pokemon FireRed Version.gba'\"",
    "tags": {"0": "GBA"},
}
DOLPHIN_SHORTCUT = {"appid": 77, "appname": "Wind Waker", "exe": "/x/launchers/dolphin-emu.sh", "LaunchOptions": ""}


def test_parse_shortcuts_vdf(tmp_path):
    f = tmp_path / "shortcuts.vdf"
    f.write_bytes(shortcuts_vdf([SRM_SHORTCUT, DOLPHIN_SHORTCUT]))
    s = vdf.shortcuts(f)
    assert s[0].appid == (-1234567890) & 0xFFFFFFFF
    assert s[0].name == "Pokemon FireRed Version"
    assert s[1].name == "Wind Waker" and s[1].exe.endswith("dolphin-emu.sh")


def test_bad_vdf_raises_value_error():
    with pytest.raises(ValueError):
        vdf.loads(b"\x09junk\0")


@pytest.fixture
def emulation(tmp_path):
    saves = tmp_path / "Emulation/saves"
    for d in ["retroarch/saves", "retroarch/states", "dolphin/GC", "dolphin/Wii", "dolphin/StateSaves",
              "ppsspp/saves", "ppsspp/states", "newemu/saves", "empty"]:
        (saves / d).mkdir(parents=True)
    return tmp_path / "Emulation"


def test_scan_proposes_one_group_per_save_folder(emulation):
    props = {p.id: p for p in detect.scan_saves(detect.find_saves_dir(emulation))}
    assert set(props) == {"retroarch", "dolphin-gc", "dolphin-wii", "ppsspp", "newemu"}
    assert props["retroarch"].unit == "file" and "*.srm" in props["retroarch"].include
    assert props["dolphin-gc"].unit == "group" and props["dolphin-gc"].emulator == "org.DolphinEmu.dolphin-emu"
    assert props["ppsspp"].unit == "dir"
    assert not props["newemu"].known and props["newemu"].unit == "group"


def test_shortcuts_map_to_their_emulators_groups(emulation):
    props = detect.scan_saves(emulation / "saves")
    detect.match_shortcuts(props, [vdf.Shortcut(1, "FireRed", "/l/retroarch.sh", "x.gba"), vdf.Shortcut(2, "WW", "/l/dolphin-emu.sh", "")])
    by = {p.id: p.appids for p in props}
    assert by["retroarch"] == [1]
    assert by["dolphin-gc"] == [2] and by["dolphin-wii"] == [2]
    assert by["ppsspp"] == []  # still a group, synced manually


def test_find_saves_dir_accepts_either_level(emulation):
    assert detect.find_saves_dir(emulation) == emulation / "saves"
    assert detect.find_saves_dir(emulation / "saves") == emulation / "saves"
    assert detect.find_saves_dir(Path("/nonexistent")) is None
