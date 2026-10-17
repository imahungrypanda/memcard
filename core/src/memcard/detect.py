"""Scaffold save groups from an EmuDeck ``Emulation/saves`` tree.

Every emulator folder found becomes a proposed group, known or not. Known
emulators get a sensible ``unit`` (what must move together) and their Flatpak
id. Steam shortcuts that launch an emulator get mapped to its groups by appid,
so launching any RetroArch game syncs RetroArch saves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .vdf import Shortcut

# saves/<emulator>/<subdir> -> (unit, include, flatpak id)
KNOWN: dict[str, tuple[str, list[str], str | None]] = {
    "retroarch/saves": ("file", ["*.srm", "*.sav", "*.rtc", "*.mcr", "*.mcd", "*.eep", "*.fla"], "org.libretro.RetroArch"),
    "dolphin/GC": ("group", [], "org.DolphinEmu.dolphin-emu"),
    "dolphin/Wii": ("group", [], "org.DolphinEmu.dolphin-emu"),
    "primehack/GC": ("group", [], "io.github.shiiion.primehack"),
    "primehack/Wii": ("group", [], "io.github.shiiion.primehack"),
    "ppsspp/saves": ("dir", [], "org.ppsspp.PPSSPP"),
    "pcsx2/saves": ("dir", [], "net.pcsx2.PCSX2"),
    "duckstation/saves": ("file", [], "org.duckstation.DuckStation"),
    "melonds/saves": ("file", [], "net.kuribo64.melonDS"),
    "mgba/saves": ("file", [], "io.mgba.mGBA"),
    "scummvm/saves": ("file", [], "org.scummvm.ScummVM"),
    "citra/saves": ("group", [], "org.citra_emu.citra"),
    "azahar/saves": ("group", [], "org.azahar_emu.Azahar"),
    "rpcs3/saves": ("dir", [], "net.rpcs3.RPCS3"),
    "cemu/saves": ("group", [], "info.cemu.Cemu"),
    "ryujinx/saves": ("group", [], "org.ryujinx.Ryujinx"),
    "xemu/saves": ("group", [], "app.xemu.xemu"),
}
# Save states are large, emulator-version-specific and rarely what people mean by "saves".
SKIP_SUBDIRS = {"states", "statesaves", "screenshots", "cache"}


@dataclass
class Proposal:
    id: str
    label: str
    path: Path
    unit: str
    include: list[str]
    emulator: str | None
    keyword: str
    known: bool
    appids: list[int] = field(default_factory=list)
    games: list[str] = field(default_factory=list)

    def to_config(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "path": str(self.path),
            "unit": self.unit,
            "include": self.include,
            "emulator": self.emulator,
            "appids": sorted(self.appids),
        }

    def to_dict(self) -> dict:
        return {**self.to_config(), "known": self.known, "games": self.games}


def find_saves_dir(start: Path | None = None) -> Path | None:
    """Accepts an Emulation dir, a saves dir, or nothing (then looks in the usual places)."""
    if start:
        for cand in (start, start / "saves", start / "Emulation/saves"):
            if cand.is_dir() and cand.name == "saves":
                return cand
        return None
    candidates = [Path.home() / "Emulation/saves"]
    for media in (Path("/run/media"), Path("/run/media/deck"), Path("/media")):
        if media.is_dir():
            candidates += sorted(media.glob("*/Emulation/saves")) + sorted(media.glob("*/*/Emulation/saves"))
    return next((c for c in candidates if c.is_dir()), None)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def scan_saves(saves: Path) -> list[Proposal]:
    out = []
    for emu_dir in sorted(p for p in saves.iterdir() if p.is_dir()):
        subdirs = sorted(p for p in emu_dir.iterdir() if p.is_dir() and p.name.lower() not in SKIP_SUBDIRS)
        if not subdirs:
            continue
        for sub in subdirs:
            key = f"{emu_dir.name}/{sub.name}"
            unit, include, flatpak = KNOWN.get(key, ("group", [], None))
            only_one = len(subdirs) == 1 or sub.name.lower() == "saves"
            gid = _slug(emu_dir.name if only_one else f"{emu_dir.name}-{sub.name}")
            label = emu_dir.name if only_one else f"{emu_dir.name} {sub.name}"
            out.append(Proposal(gid, label, sub, unit, list(include), flatpak, emu_dir.name.lower(), key in KNOWN))
    return out


def match_shortcuts(proposals: list[Proposal], shortcuts: list[Shortcut]) -> None:
    """Map each Steam shortcut to every group of the emulator its command line launches."""
    for s in shortcuts:
        command = s.command.lower()
        for p in proposals:
            if re.search(rf"\b{re.escape(p.keyword)}\b", command) and s.appid not in p.appids:
                p.appids.append(s.appid)
                p.games.append(s.name)
