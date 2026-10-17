"""Reader for Steam's binary VDF (``shortcuts.vdf``: the non-Steam game list)."""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAP, STRING, INT32, FLOAT32, POINTER, WSTRING, COLOR, UINT64, END = 0, 1, 2, 3, 4, 5, 6, 7, 8


def _cstr(data: bytes, i: int) -> tuple[str, int]:
    end = data.index(b"\0", i)
    return data[i:end].decode("utf-8", "replace"), end + 1


def loads(data: bytes) -> dict[str, Any]:
    def parse_map(i: int) -> tuple[dict[str, Any], int]:
        out: dict[str, Any] = {}
        while i < len(data):
            kind = data[i]
            i += 1
            if kind == END:
                return out, i
            key, i = _cstr(data, i)
            if kind == MAP:
                out[key], i = parse_map(i)
            elif kind == STRING:
                out[key], i = _cstr(data, i)
            elif kind in (INT32, POINTER, COLOR):
                out[key] = struct.unpack_from("<i", data, i)[0]
                i += 4
            elif kind == FLOAT32:
                out[key] = struct.unpack_from("<f", data, i)[0]
                i += 4
            elif kind == UINT64:
                out[key] = struct.unpack_from("<Q", data, i)[0]
                i += 8
            else:
                raise ValueError(f"unknown VDF type {kind} at byte {i - 1}")
        return out, i

    return parse_map(0)[0]


@dataclass(frozen=True)
class Shortcut:
    appid: int
    name: str
    exe: str
    launch_options: str

    @property
    def command(self) -> str:
        return f"{self.exe} {self.launch_options}"


def shortcuts(path: Path) -> list[Shortcut]:
    root = loads(path.read_bytes())
    entries = root.get("shortcuts", {})
    out = []
    for e in entries.values():
        get = lambda k: e.get(k, e.get(k.lower(), ""))  # noqa: E731 - Steam is inconsistent about case
        out.append(Shortcut(get("appid") & 0xFFFFFFFF, get("AppName"), get("Exe"), get("LaunchOptions")))
    return out


def find_shortcut_files(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    roots = [
        home / ".local/share/Steam/userdata",
        home / ".steam/steam/userdata",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam/userdata",
    ]
    seen, out = set(), []
    for root in roots:
        for p in sorted(root.glob("*/config/shortcuts.vdf")) if root.is_dir() else []:
            real = p.resolve()
            if real not in seen:
                seen.add(real)
                out.append(p)
    return out
