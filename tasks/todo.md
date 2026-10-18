# Memcard — build plan

See `docs/design.md`.

## Phase 0 — design
- [x] Repo layout, manifest format, module boundaries (`docs/design.md`)
- [x] Approval + answers to D1–D4 (recommended options, 2026-10-17)

## Phase 1 — memcard-core + CLI (with tests)
- [x] Scaffold `core/` (pyproject, pytest, Linux CI)
- [x] `model`, `paths`, `config` (+ overlap validation tests)
- [x] `planner` + table test covering every conflict-matrix row, deletions, first sync, converged, unit aggregation (push/pull mix → conflict)
- [x] `store.snapshot` (atomic write), `store.trash` (retention N)
- [x] `transport.rsync` + integration tests: copy-links stage-out, restore keeps symlinks, same-size/same-mtime change not skipped
- [x] `transport.rclone` + `store.remote` (objects, CAS commit, fetch+verify, gc) against rclone `local` backend
- [x] `engine` (lock, cancel, replan on generation race) + interruption/recovery tests
- [x] `errors` + provider error mapping tests (401/404/timeout/offline)
- [x] `providers`, `setup` (field checks, round-trip probe, no silent remote creation)
- [x] `detect` + `vdf` (EmuDeck tree fixture, shortcuts.vdf fixture)
- [x] `triggers` (`wrap` via blocking waitpid, settle after exit, periodic oneshot timer that skips while a game runs, idle priority)
- [x] Overhead check for `wrap` (no-change round trip ≈1.1 s, all of it outside gameplay)
- [x] `cli` with `--json` NDJSON events + exit codes
- [x] `docs/providers/*.md`, `docs/install.md`
- [x] Single-file zipapp build (`scripts/build-zipapp.sh`)
- [ ] Hardware test pass (checklist in `docs/install.md` §8)

## Phase 2 — memcard-decky
Decision (2026-10-18): the plugin intercepts Steam's game launch (cancel → sync → relaunch) and
pushes on Steam's exit event, instead of writing `memcard wrap` into launch options. SRM rewrites
launch options, and `%command%` expansion for shortcuts is unverified. `wrap` stays for Bazzite.

### 2a. Core additions (PR: feat/core-plugin-hooks)
- [x] `memcard groups --json`: groups with appids, plus machine/remote info (the plugin's mapping source)
- [x] `sync --appid N` / `status --appid N`: the groups mapped to an appid
- [x] `sync --before-launch`: launch_timeout_s deadline + a running marker that lasts until `--after-exit`
- [x] `sync --after-exit`: idle priority, wait for the emulator to exit and writes to settle, then sync
- [x] `wrap` reuses the same before/after functions; periodic skips while an app-marker is live
- [x] Tests + docs (CLAUDE.md, design §8)

### 2b. Plugin (PR: feat/decky, stacked on 2a)
- [ ] Scaffold from decky-plugin-template (@decky/ui, @decky/api, @decky/rollup, pnpm 9); no root flag
- [ ] Backend main.py: run bundled `bin/memcard` with system python3, clean env, allowlisted subcommands,
      stream NDJSON events via decky.emit, cancel with SIGTERM; install rclone (sha256-verified) on request
- [ ] Launch interceptor: GameActionStart → mapped appid? cancel → before-launch sync → conflict modal → relaunch (one-shot skip)
- [ ] Exit: lifetime notification (appid fallback: last launched) → after-exit sync → toast
- [ ] QAM panel: status, Sync now, pending conflicts → resolve modal, auto-sync toggle, detect saves
- [ ] Setup wizard: existing remotes, provider fields one at a time, validate, apply, OAuth token paste/file, mapped errors
- [ ] Build script → installable zip (bundles the memcard zipapp); backend unit tests; tsc + rollup build in CI
- [ ] docs/install.md: Deck section uses the plugin

## Phase 3 — memcard-gui
- [ ] Pick GTK4 vs Qt; Flatpak manifest with rsync + rclone bundled

## Review — Phase 1
- 105 tests (unit + integration with real rsync 3.5.1 / rclone 1.75.2). Integration tests skip automatically if either binary is missing.
- Found during the build: rsync ≥ 3.4 won't traverse symlinked dirs on restore even with
  `--keep-dirlinks`, so restore now resolves real destinations itself. That's exactly
  EmuDeck's layout, and SteamOS is likely to ship rsync 3.4+.
- Unverified until it runs on real hardware: SteamOS/Bazzite Python version, `flatpak ps`
  output, Steam's `%command%` on SRM shortcuts, real Nextcloud/OAuth providers.
- History note: commit d28bc73 accidentally included test output under `core/private/`
  (removed in 04d6577). Squash-merge the branch to keep it out of `main`.
