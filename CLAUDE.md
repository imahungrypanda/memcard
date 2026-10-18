# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Memcard: two-way game save sync for Linux (Steam Deck/SteamOS + Bazzite desktop) over the user's own cloud via rclone. Replaces Ludusavi for EmuDeck emulator saves. Three deliverables share one core: `core/` (Python library + `memcard` CLI, **built**), `decky/` (Steam Deck Game Mode plugin, not started), `gui/` (Flatpak desktop app, not started). Full design and as-built deviations: `docs/design.md` (§8). Plan/progress: `tasks/todo.md`.

**Rule:** all sync logic lives in `core/`. The UIs only drive the CLI with `--json` and render its events. If a behavior can only be exercised through a UI, it's in the wrong place.

## Commands

All Python work happens in `core/` with its venv:

```bash
cd core
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'   # first time
.venv/bin/pytest -q                                           # full suite (~20 s)
.venv/bin/pytest -q tests/unit                                # fast, no binaries needed
.venv/bin/pytest -q tests/integration/test_engine.py -k crash # single file / test
.venv/bin/memcard --help
../scripts/build-zipapp.sh    # -> dist/memcard: one ~100 KB file, tomlkit bundled
```

- Integration tests (`pytestmark = pytest.mark.integration`) shell out to real **GNU rsync** and **rclone**. They auto-skip when either is missing (`tests/conftest.py`). On macOS `/usr/bin/rsync` is openrsync, so use `brew install rsync rclone`. Linux CI (`.github/workflows/ci.yml`, Python 3.11 + 3.13) is authoritative.
- An autouse fixture runs every test in a temp cwd with its own `MEMCARD_HOME`. Keep it that way: tests must never write into the repo or `~`.
- No linter is configured.
- Target Python ≥ 3.11 (stdlib `tomllib`). Hot paths (`wrap`, `sync`, `status`) must stay stdlib-only. `tomlkit` is imported lazily, only when writing config.

## Architecture (core/src/memcard)

Pipeline per save group: `scan` (hash local) → `store.remote.head` (remote manifest) → `store.snapshot` (last-agreed state) → `planner` (decide) → `engine` (apply) → write snapshot.

- **`planner.py` is pure** (no IO, no clock) and holds the whole conflict model. Per file, with L/R/S = local/remote/snapshot hashes: `L==R` no-op, `R==S` push, `L==S` pull, otherwise conflict. Absent files are just another state, so deletes and first sync need no special cases. Files group into **units** (config `unit = group|file|dir`; `dir` = each top-level entry). A unit mixing push and pull is a conflict, so a memory card is never assembled half from each machine. `_guard_mass_delete` turns "one side lost every file" (unmounted SD, reset remote) into a conflict. `actions_for` turns decisions into ordered actions (`copy_local`/`stash_local` first, for keep-both).
- **Decisions use SHA-256 only.** mtimes are display-only (FAT32 and WebDAV lie). There's no hash cache keyed on mtime/size. Don't add one.
- **Remote layout is content-addressed** (`store/remote.py`): `objects/<aa>/<sha256>` are write-once, and `groups/<id>/HEAD.json` is the commit point, replaced last with a compare-and-swap on `generation`. `history/<gen>.json` holds remote versioning. A `memcard-remote.json` marker is claimed before *any* write, so Memcard never writes into a folder that isn't its own. GC skips unreferenced objects younger than a day.
- **No journal.** Recovery relies on idempotent steps, atomic writes (`paths.atomic_write`) and the planner treating `L==R` as done. Re-running sync finishes any interrupted one. `RemoteChanged`/`LocalChanged` make the engine replan (up to 3 attempts).
- **rclone is used only one-directionally** (`copy`, `copyto`, `cat`, `lsjson`, `delete`). Never `bisync`: it has its own state and conflict rules. Hash locally and never trust backend hashes.
- **rsync (`transport/rsync.py`) owns all local-leg flags.** Stage-out uses `--copy-links` (dereference EmuDeck symlinks into Flatpak data). Restore must write *through* symlinks: rsync ≥ 3.4 won't traverse a symlinked dir even with `--keep-dirlinks`, so `real_destination()` resolves the path first. Both directions use `--ignore-times`. Never restore in a way that replaces a symlink with a real dir/file.
- **Before any local overwrite/delete**, the engine copies the file to `store/trash.py`. Batches are pruned to `trash_versions`; `-kept` keep-both alternates are never pruned.
- **`config.py`** is per-machine TOML. The **group id** is the only cross-machine identity; paths and appids are local (non-Steam shortcut appids differ per machine). A group has `path` (dir) **or** `file`. More specific groups are carved out of broader ones automatically. Include/exclude patterns without `/` match the basename at any depth. `Group.wants()` filters local, remote and snapshot alike.
- **`errors.py`**: every user-facing error carries `message` + `hint`, with raw tool output in `detail`. `map_rclone_error` turns rclone failures into provider-specific text, with wording from `providers.py` (auth_hint, url_example). Exit codes: 2 conflicts pending, 3 offline/unreachable/auth, 4 config.
- **`triggers.py`** has a performance contract: nothing runs while a game runs. `wrap` blocks in `waitpid`, then waits for the emulator's Flatpak to exit and writes to settle (stat-only), then syncs at idle priority (`proc.lower_priority`). Periodic sync is a systemd oneshot that skips while any game runs. The appid comes from `SteamGameId` (top 32 bits for non-Steam shortcuts).
- **`setup.py`** writes Memcard's own `rclone.conf` (0600) directly, with secrets obscured via stdin so they never hit argv. It never edits the user's own rclone.conf, never creates a remote on update, and never overwrites one on create. "Connected" requires `probe()`: write, read back, delete.
- **`cli.py` / `events.py`**: with `--json`, every stdout line is `{"event": ...}`, and each command ends with a `result` or `error` event. This is the UI protocol; keep it stable. `--resolve GROUP[:UNIT]=local|remote|both` settles conflicts non-interactively. Undecided conflicts are recorded in `state/pending.json`.
- **`detect.py` + `vdf.py`** scaffold groups from EmuDeck `Emulation/saves/<emu>/<sub>` (known emulators get `unit`/Flatpak id) and map Steam `shortcuts.vdf` entries to groups by which emulator the shortcut's command launches.

State locations come from `paths.Dirs` (XDG, or everything under `MEMCARD_HOME`).
