# Memcard — build plan

See `docs/design.md`. Nothing below Phase 0 starts until the design is approved.

## Phase 0 — design
- [x] Repo layout, manifest format, module boundaries (`docs/design.md`)
- [ ] Approval + answers to D1–D4

## Phase 1 — memcard-core + CLI (with tests)
- [ ] Scaffold `core/` (pyproject, vendored tomlkit, pytest, Linux CI)
- [ ] `model`, `paths`, `config` (+ overlap validation tests)
- [ ] `planner` + table test covering every conflict-matrix row, deletions, first sync, converged, unit aggregation (push/pull mix → conflict)
- [ ] `store.snapshot` (atomic write), `store.trash` (retention N)
- [ ] `transport.rsync` + integration tests: copy-links stage-out, keep-dirlinks restore keeps symlink, same-size/same-mtime change not skipped
- [ ] `transport.rclone` + `store.remote` (objects, CAS commit, fetch+verify, gc) against rclone `local` backend
- [ ] `engine` (lock, cancel, replan on generation race) + interruption/recovery tests
- [ ] `errors` + provider error mapping tests (401/404/timeout/offline)
- [ ] `providers`, `setup` (wizard state machine, round-trip probe, no silent remote creation)
- [ ] `detect` + `vdf` (EmuDeck tree fixture, shortcuts.vdf fixture)
- [ ] `triggers` (`wrap` via blocking waitpid, quiescence wait after exit, periodic as oneshot timer that skips while a game runs, nice/ionice)
- [ ] Overhead benchmark for `wrap` pre-launch / post-exit
- [ ] `cli` with `--json` NDJSON events + exit codes
- [ ] `docs/providers/*.md`

## Phase 2 — memcard-decky
- [ ] Spike: Decky launch-option install + wrapper↔plugin socket (D3)

## Phase 3 — memcard-gui
- [ ] Pick GTK4 vs Qt; Flatpak manifest with rsync + rclone bundled
