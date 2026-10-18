#!/usr/bin/env bash
# Build dist/memcard: one self-contained file that runs on any Python >= 3.11.
# No pip install needed on the target, which matters on SteamOS and Bazzite
# (immutable root filesystem). tomlkit is bundled inside.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
build="$(mktemp -d)"
trap 'rm -rf "$build"' EXIT

python3 -m pip install --quiet --disable-pip-version-check --no-compile --target "$build" tomlkit
cp -R "$root/core/src/memcard" "$build/memcard"
cp "$root/core/src/memcard/__main__.py" "$build/__main__.py"
find "$build" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$build"/*.dist-info

mkdir -p "$root/dist"
python3 -m zipapp "$build" -p "/usr/bin/env python3" -o "$root/dist/memcard" -c
chmod +x "$root/dist/memcard"
echo "Built $root/dist/memcard ($(du -h "$root/dist/memcard" | cut -f1))"
