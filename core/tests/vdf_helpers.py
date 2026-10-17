import struct


def encode(d: dict) -> bytes:
    out = b""
    for k, v in d.items():
        key = k.encode() + b"\0"
        if isinstance(v, dict):
            out += b"\x00" + key + encode(v) + b"\x08"
        elif isinstance(v, int):
            out += b"\x02" + key + struct.pack("<i", v)
        else:
            out += b"\x01" + key + v.encode() + b"\0"
    return out


def shortcuts_vdf(entries: list[dict]) -> bytes:
    return encode({"shortcuts": {str(i): e for i, e in enumerate(entries)}}) + b"\x08"
