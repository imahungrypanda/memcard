#!/usr/bin/env bash
# Build dist/memcard-decky.zip: install it from Decky's developer settings
# ("Install plugin from ZIP file"). It bundles the memcard zipapp as bin/memcard,
# so the plugin and the CLI it drives are always the same version.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
"$root/scripts/build-zipapp.sh"

cd "$root/decky"
npx -y pnpm@9 install --frozen-lockfile
npx -y pnpm@9 run typecheck
npx -y pnpm@9 run build

out="$root/decky/out"
pkg="$out/Memcard"
rm -rf "$out"
mkdir -p "$pkg/dist" "$pkg/bin"
cp dist/index.js "$pkg/dist/"
cp main.py plugin.json package.json README.md "$pkg/"
cp "$root/LICENSE" "$pkg/"
cp -R py_modules "$pkg/py_modules"
find "$pkg/py_modules" -name __pycache__ -prune -exec rm -rf {} +
cp "$root/dist/memcard" "$pkg/bin/memcard"
chmod 755 "$pkg/bin/memcard"

rm -f "$root/dist/memcard-decky.zip"
(cd "$out" && zip -qr "$root/dist/memcard-decky.zip" Memcard)
echo "Built $root/dist/memcard-decky.zip ($(du -h "$root/dist/memcard-decky.zip" | cut -f1))"
