# Memcard — design proposal

Status: **approved 2026-10-17** with the recommended answers to D1–D4. memcard-core is built; §8 lists where the build differs from this proposal.

Follow-up designs: [cloud versions / `memcard history`](history.md) (approved 2026-11-18).

Covers the three things the build prompt asks for first: repo layout, state/manifest
format, and core module boundaries. It also records the decisions that fall out of
them, and the ones that need your call (§6).

---

## 1. Repo layout

One monorepo. The core is the only place sync logic lives; `decky/` and `gui/` hold
no logic beyond rendering and relaying.

```
memcard/
├── core/                         memcard-core: Python package `memcard` + `memcard` CLI
│   ├── pyproject.toml            Python ≥3.11, stdlib + vendored tomlkit only
│   ├── src/memcard/              (modules in §3)
│   └── tests/
│       ├── unit/                 pure tests, no binaries: planner matrix, config, vdf, error mapping
│       └── integration/          real rsync + rclone (`local` backend) against tmp dirs
├── decky/                        memcard-decky
│   ├── plugin.json, package.json (package.json pulls a static rclone into bin/)
│   ├── main.py                   backend: spawns `memcard --json …`, relays events to frontend
│   └── src/                      React/TS: QAM panel, conflict modal, setup wizard
├── gui/                          memcard-gui (toolkit TBD, §6)
│   ├── src/memcard_gui/
│   └── flatpak/io.github.imahungrypanda.Memcard.yml   bundles python, rsync, rclone
├── docs/
│   ├── design.md                 this file
│   └── providers/                nextcloud.md, google-drive.md, box.md, icloud.md, webdav-generic.md
├── scripts/                      vendor/build helpers (fetch rclone, bundle core into decky)
├── tasks/                        todo.md, lessons.md
└── .github/workflows/ci.yml      Linux CI: GNU rsync + rclone, runs both test tiers
```

**How the UIs talk to core: the CLI, with `--json`.** Every UI action is a `memcard`
subcommand that emits newline-delimited JSON events (progress, conflict, error, done).
Reasons:

- It enforces the rule "if a behavior can only be exercised through a UI, it's in the
  wrong place" mechanically — the UI *can't* do anything the CLI can't.
- Cancellation is just SIGTERM to a child process (the engine stops at safe points).
- The Decky backend can run the sync as the `deck` user regardless of how Decky runs
  the plugin, and the GUI runs it inside the same Flatpak sandbox as `memcard wrap`.

---

## 2. State and manifest format

### 2.1 Where state lives (per machine)

All roots overridable with `MEMCARD_HOME`. Inside the Flatpak these resolve to
`~/.var/app/<id>/…`, so on Bazzite the Steam launch wrapper is invoked as
`flatpak run --command=memcard <id> wrap …` to share the same state.

| What | Path |
|---|---|
| Config (user-editable) | `$XDG_CONFIG_HOME/memcard/config.toml` |
| Memcard's own rclone config | `$XDG_CONFIG_HOME/memcard/rclone.conf` (or a path to the user's existing one) |
| Last-synced snapshots | `$XDG_STATE_HOME/memcard/snapshots/<group>.json` |
| Log | `$XDG_STATE_HOME/memcard/memcard.log` (rotating) |
| Lock | `$XDG_STATE_HOME/memcard/sync.lock` (flock; launch hook vs periodic vs manual) |
| Versioned trash | `$XDG_DATA_HOME/memcard/trash/<group>/<UTC timestamp>/<relpath>` |
| Staging + object cache | `$XDG_CACHE_HOME/memcard/{staging/<run-id>, objects/<aa>/<sha256>}` |

### 2.2 `config.toml` — per machine, never synced

The **group id** is the only cross-machine identity. Paths and appids are per machine
— and appids *have* to be: a non-Steam shortcut's appid is derived from its exe path
and name, so the same game gets a different appid on the Deck and on Bazzite.

```toml
[machine]
id   = "c0a8e1f2-…"          # generated on first run; don't edit
name = "Steam Deck"          # shown in conflict prompts ("last written by Steam Deck")

[remote]
name   = "nextcloud"         # an rclone remote name — nothing provider-specific here
folder = "memcard"
# rclone_config = "~/.config/rclone/rclone.conf"   # to reuse an existing remote

[sync]
trash_versions   = 5
history_versions = 5         # remote generations kept per group
periodic_minutes = 0         # 0 = off
launch_timeout_s = 20        # pre-launch sync gives up and launches with local saves after this
exit_settle_s    = 30        # max wait after exit for the emulator to finish writing

[[group]]
id    = "retroarch"
label = "RetroArch (all systems)"
path  = "/run/media/deck/FATTY/Emulation/saves/retroarch/saves"
unit  = "file"               # each .srm resolves independently (see 2.5)
include = ["*.srm", "*.sav", "*.rtc"]   # no "/" = match the file name at any depth
emulator = "org.libretro.RetroArch"   # flatpak id, used to wait for full exit
appids = []                  # empty = manual/periodic only; still fully syncable

[[group]]
id    = "dolphin-gc"
label = "Dolphin GameCube memory cards"
path  = "/run/media/deck/FATTY/Emulation/saves/dolphin/GC"
unit  = "group"              # all files move together
emulator = "org.DolphinEmu.dolphin-emu"
appids = [3141592653, 2718281828]

[[group]]
id    = "pokemon-firered"    # per-game group carved out of "retroarch"
file  = "/run/media/deck/FATTY/Emulation/saves/retroarch/saves/Pokemon FireRed Version.srm"
unit  = "group"
emulator = "org.libretro.RetroArch"
appids = [3141592653]
```

Validation at load: a file may belong to only one group; the most specific path wins and
the broader group excludes it automatically. Edited through `tomlkit` so the GUI and
`detect --write` keep the user's comments.

### 2.3 Remote layout — content-addressed, one atomic commit per group

```
<remote>:<folder>/
  memcard-remote.json                 {"format": 1} — guards against pointing at the wrong folder
  groups/<group-id>/HEAD.json         current manifest = the commit point
  groups/<group-id>/history/<gen>.json  last N manifests (remote-side versioning; browsed and restored by `memcard history`, see history.md)
  objects/<aa>/<sha256>               file contents, immutable, write-once
```

Why content-addressed rather than a mirrored tree:

- **Never half-written.** Objects go up first; nothing references them until `HEAD.json`
  is replaced in a single PUT. An interrupted push leaves only unreferenced objects,
  which `memcard gc` cleans up. A mirrored tree can't make a multi-file memory card
  appear atomically.
- **Resumable for free.** Objects already uploaded are skipped (`rclone copy --size-only`;
  same name ⇒ same content, a truncated upload has a different size).
- **iCloud's trash problem mostly goes away.** Objects are never overwritten — only the
  small `HEAD.json` is replaced per sync, so trash accumulates per sync, not per file.
- Cost: the remote isn't browsable as save files in the Nextcloud web UI. `memcard export`
  can materialize any generation to a folder. **(Decision D1)**

### 2.4 Manifests

Remote `HEAD.json` (hashes computed locally by whichever machine committed):

```json
{
  "format": 1,
  "group": "dolphin-gc",
  "generation": 18,
  "parent": 17,
  "committed_at": "2026-10-17T18:22:01Z",
  "committed_by": "c0a8e1f2-…",
  "machines": { "c0a8e1f2-…": "Steam Deck", "77be09aa-…": "Bazzite" },
  "files": {
    "MemoryCardA.USA.raw": {
      "sha256": "9f2c…", "size": 16777216,
      "mtime": "2026-10-17T18:20:44Z",
      "written_by": "c0a8e1f2-…", "written_at": "2026-10-17T18:22:01Z"
    }
  }
}
```

`mtime` is **display-only** (conflict prompt). It never drives a decision.

Local snapshot `snapshots/<group>.json` — what both sides agreed on at the last
successful sync of this group on this machine:

```json
{
  "format": 1,
  "group": "dolphin-gc",
  "remote_generation": 18,
  "synced_at": "2026-10-17T18:22:01Z",
  "files": { "MemoryCardA.USA.raw": { "sha256": "9f2c…", "size": 16777216 } }
}
```

Hashing: SHA-256 of every file, every sync, no mtime/size cache. Saves are KB–MB, and
a cache keyed on FAT32's 2-second mtimes is exactly the bug we're avoiding.

### 2.5 The decision rules (what the planner implements)

Per file, with `L` = local hash, `R` = remote hash, `S` = snapshot hash, absent = `∅`:

| case | condition | file verdict |
|---|---|---|
| converged | `L == R` (incl. both ∅) | no-op (refresh snapshot) |
| local changed | `L != S`, `R == S` | push (∅ local → push delete) |
| remote changed | `L == S`, `R != S` | pull (∅ remote → local delete) |
| both changed | `L != S`, `R != S`, `L != R` | **conflict** |
| first sync, one side only | no snapshot, one side ∅ | push or pull |
| first sync, both differ | no snapshot, `L != R` | **conflict** |

Per **unit** (a group is one unit, or one unit per file/dir when `unit = "file"|"dir"`):

- all files no-op → no-op
- only push (+ no-ops) → push the unit
- only pull (+ no-ops) → pull the unit
- any conflict, **or a mix of push and pull** → the whole unit is a conflict

The push/pull mix rule is what stops a Dolphin card from being assembled half from each
machine. `unit` exists so that playing FireRed on the Deck and Emerald on the PC isn't a
conflict just because both are in the RetroArch group. **(Decision D2)**

"Converged ⇒ no-op" is also what makes crash recovery fall out of the planner rather
than needing a journal: if a push committed `HEAD` but died before writing the snapshot,
next run sees `L == R` and just records it. Same for a pull that died halfway.

Resolution options for a conflicting unit: **keep local** (push), **keep remote** (pull),
**keep both**. For `unit = "file"` keep-both renames, e.g.
`Pokemon FireRed Version (Bazzite 2026-10-17).srm`. For multi-file units, renaming files
inside a memory-card folder would just give the emulator garbage, so keep-both takes the
remote and stores the local side as a named alternate (`memcard trash restore` can
bring it back).

---

## 3. Core module boundaries

Arrows mean "may import". `planner` is pure and depends on nothing that does IO.

```
cli ──► engine ──► planner (pure)
 │        │──► scan ──► transport.rsync
 │        │──► store.remote ──► transport.rclone
 │        │──► store.snapshot, store.trash
 │        └──► events
 ├──► setup ──► providers, transport.rclone, errors
 ├──► detect ──► vdf, config
 └──► triggers ──► engine
everything ──► model, config, paths, errors, log
```

| module | owns | does not |
|---|---|---|
| `model` | dataclasses: `FileState`, `Manifest`, `Snapshot`, `Group`, `Unit`, `Plan`, `UnitAction`, `Conflict` | any IO |
| `config` | load/validate/save `config.toml`, overlap rules, path expansion | touch the remote |
| `paths` | XDG / `MEMCARD_HOME` resolution, Deck vs desktop, Game Mode detection | |
| `scan` | enumerate group files (follow symlinks, cycle-safe), stage out, hash | decide anything |
| `planner` | §2.5 rules: `(local, remote, snapshot, units) → Plan` | IO of any kind |
| `transport.rsync` | the two rsync invocations, flags centralized (see below) | sync logic |
| `transport.rclone` | run rclone with Memcard's config; structured result (exit code, stderr) | sync logic, error wording |
| `store.remote` | read HEAD, upload objects, compare-and-swap commit, fetch+verify objects, gc | decide direction |
| `store.snapshot` | atomic read/write (write tmp + fsync + rename) | |
| `store.trash` | copy-before-overwrite/delete, retain N versions, list/restore | |
| `engine` | lock → scan → read remote → plan → await resolutions → apply → commit; cancel token at safe points | UI wording |
| `errors` | `MemcardError(code, message, hint, detail)`; rclone stderr/exit-code → actionable text | |
| `providers` | per-provider metadata: fields, examples, OAuth?, experimental?, settings menu paths, URL checks | |
| `setup` | wizard as a step state machine the UIs render; create/update remote (never silently create); round-trip probe | |
| `detect` | scan EmuDeck `Emulation/saves`, propose groups; match Steam shortcuts → appids | write config without `--write` |
| `vdf` | binary `shortcuts.vdf` reader | |
| `triggers` | `wrap` (pull → exec → wait for full exit → push), periodic loop | |
| `events` | NDJSON event schema shared by CLI and both UIs | |
| `cli` | argparse, human output vs `--json` | logic |

### Transport flags (centralized in `transport.rsync`)

- **Stage out:** `rsync -r --copy-links --ignore-times <saves>/ <fresh staging>/` —
  dereferences EmuDeck's symlinks into the real Flatpak files. Into a fresh dir each run,
  because rsync's default size+mtime quick-check would otherwise skip a changed same-size
  `.srm` on FAT32.
- **Restore:** `rsync -r --keep-dirlinks --ignore-times --files-from=<unit files> <staging>/ <saves>/` —
  writes *through* symlinked dirs. Integration test asserts the symlink is still a
  symlink and the Flatpak-side file changed. A symlinked *file* (not dir) is resolved to its
  real path before restore, since `--keep-dirlinks` only covers dirs.
- Deletes and the pre-write trash copy are done by core, not by rsync `--delete`, so they
  can't escape a unit's scope.

### Push and pull, end to end

- **Push:** stage out → hash → plan → upload missing objects → re-read `HEAD`; if
  `generation != planned parent`, abort and replan (someone else pushed) → write
  `history/<gen>.json` then `HEAD.json` → write snapshot.
- **Pull:** read `HEAD` → fetch missing objects into the local object cache, verify SHA-256 →
  assemble staged tree → trash the local files about to change → rsync restore → re-hash
  to verify → write snapshot.

### CLI surface

```
memcard init                         machine id + name
memcard setup                        guided wizard (terminal)
memcard setup providers|remotes|validate|apply|use    non-interactive steps the UIs drive (secrets via stdin)
memcard remote test                  round-trip probe
memcard detect [--emudeck PATH] [--write]
memcard status [GROUP…]              per-unit verdicts without changing anything
memcard sync [GROUP…] [--resolve GROUP[:UNIT]=local|remote|both] [--non-interactive] [--periodic]
memcard wrap [--appid N] -- CMD…     Steam launch-option wrapper (appid defaults to SteamGameId)
memcard groups                       groups + appids + pending conflicts, offline (a UI's mapping source)
memcard status|sync --appid N        only the groups mapped to a Steam appid
memcard sync --appid N --before-launch|--after-exit [--launch-id ID]    launch hooks for a UI that controls the launch (Decky)
memcard trash list|restore
memcard gc
memcard export GROUP DIR [--generation N]
memcard timer enable|disable         systemd oneshot timer running `sync --periodic`
memcard log
global: --json   exit codes: 0 ok · 2 conflicts pending · 3 offline/unreachable · 4 config · 1 other
```

---

## 4. Triggers

Recommended: **`memcard wrap` as the Steam launch option on both machines**
(`memcard wrap --appid 3141592653 -- %command%`). It blocks the launch naturally until
the pull is done, and pushes only after the child exits *and* the emulator's Flatpak
instance is gone from `flatpak ps` *and* the group's files stop changing between two
stat passes (size + mtime; a settle signal only, never a sync decision; timeout configurable). That's the "fully exited" guarantee.

- On the Deck, the Decky plugin installs/removes that launch option on the shortcuts you
  map, and acts as the wrapper's UI: the wrapper posts conflicts/progress to a local
  socket the plugin backend watches, and the QAM shows the prompt with a Skip button.
- On Bazzite, the wrapper opens `memcard-gui --resolve` for conflicts.
- Offline/timeout on launch: launch with local saves and log a notice. The three-way
  model makes this safe — if the remote did move, the exit push becomes a conflict, not
  an overwrite.

**(Decision D3)** — the alternative is Decky's app-lifecycle hooks, which I don't believe
can reliably *block* a launch. I'd spike that before committing either way.

### Performance budget: nothing runs while a game is running

Memcard must cost nothing during gameplay. Concretely:

- **No resident process during play.** `wrap` stays alive only as the game's parent so it
  can see the exit, and it does that with a blocking `waitpid`: 0% CPU, no polling, no
  file watchers. The quiescence check (`flatpak ps`, re-hashing) only starts *after* exit.
- **All hashing and transfer happens before launch or after exit**, never mid-session.
- **Periodic sync is off by default.** When it's on, it skips any tick while a game
  launched through `memcard wrap` or a mapped emulator's Flatpak is running, and stops at
  its next safe point if one starts mid-pass. (Games that are neither wrapped nor mapped
  aren't detected.) It runs as a oneshot (systemd user timer), not
  a long-lived Python loop.
- **Anything in the background runs at idle priority:** `nice -n 19` and
  `ionice -c3` on rsync, rclone and the hashing pass, so a sync that overlaps a launch
  can't take I/O from the game on a slow SD card.
- **Decky plugin is idle at rest:** the backend only spawns `memcard` on an event or a
  button press, and the frontend renders nothing outside the QAM panel. No polling timers.
- **Scope reads to what changed hands.** `wrap --appid` syncs only the groups mapped to
  that appid, not every group.
- **Cheap startup.** Stdlib-only imports on the hot path (`wrap`, `sync`); tomlkit and the
  setup/detect code load lazily. Target: `memcard status` on a typical group under ~200 ms
  on a Deck.

Tests: a benchmark in `tests/integration` that times `wrap` overhead before launch and
after exit on a fixture tree, so regressions show up in CI.

---

## 5. Setup

- `providers` holds per-provider metadata so both wizards render the same steps:
  field, example, secret?, validator. Nextcloud's URL is checked **before** calling rclone
  for the `/remote.php/dav/files/<username>/` suffix, with a suggested fix.
- Round-trip probe: write `.memcard-probe-<uuid>`, read it back, compare, delete. Anything
  less is not "connected".
- Error mapping uses rclone's exit code + stderr patterns → `errors`. 401 / 404 / timeout per
  the prompt; timeout splits *offline* (no route / DNS fails for everything) from
  *server unreachable* (DNS ok, connect to provider host fails). Raw stderr goes to the log
  and behind a "details" disclosure, never as the only message.
- Remotes: the wizard lists existing rclone remotes to reuse. Editing config runs
  `rclone config update <name>` in place; creation is only on an explicit "new remote"
  step with a user-chosen name and collision check.
- OAuth on the Deck: `providers` flags Drive/Box as OAuth; in Game Mode the wizard shows
  the `rclone authorize "drive"` instructions for a desktop and accepts the token by paste
  **or** from a file on SD/USB (pasting a long JSON blob with the Steam keyboard is
  miserable). A LAN hand-off (`memcard send-token <deck> <code>`) is a possible stretch.
- iCloud: `experimental: true` in metadata, surfaced as a badge in both UIs and its doc.

---

## 6. Decisions needed

| # | Question | Recommendation |
|---|---|---|
| D1 | Remote layout: content-addressed (atomic, resumable, not browsable) vs mirrored tree (browsable, can be half-written) | Content-addressed + `memcard export` |
| D2 | Add a `unit` setting (`group` / `file` / `dir`) so per-system groups don't conflict across different games | Yes; `detect` picks per emulator (RetroArch → file, PPSSPP SAVEDATA → dir, Dolphin GC → group) |
| D3 | Launch/exit triggers via `memcard wrap` launch option (Decky as its UI) vs Decky lifecycle hooks | `wrap`, after a short spike on Decky's APIs |
| D4 | UIs call core via `memcard --json` subprocess vs importing the library in-process | Subprocess |

From the prompt's "left open" list: **GTK4 vs Qt** stays open until core exists. On
**snapshot on the remote for a third machine** — the remote `HEAD` already records the
current remote state and per-file `written_by`, so a third machine can join today using
the first-sync rules (remote-only files pull, differing files conflict once). A per-machine
snapshot on the remote would only add "which machines are behind" reporting; I'd defer it.

---

## 7. Dev environment notes

- macOS `/usr/bin/rsync` is openrsync, not GNU rsync; integration tests need
  `brew install rsync rclone`. Linux CI is the source of truth.
- Target Python 3.11 (SteamOS system python and Decky's bundled runtime).

---

## 8. Changes made while building memcard-core

| Proposal | As built | Why |
|---|---|---|
| `paths = [...]` per group | `path = "<dir>"` **or** `file = "<one save>"` | One root per group keeps relative paths unambiguous across machines. Use two groups for two folders. |
| `unit = "dir"` = "each directory" | Each **top-level entry** (file or folder) is a unit | Handles PCSX2, which mixes single-file and folder memory cards |
| Restore relies on `--keep-dirlinks` | Also resolves symlinks itself and gives rsync a symlink-free destination | rsync ≥ 3.4 (the CVE-2024-12084 hardening) refuses to traverse a symlinked dir when creating parent dirs, even with `--keep-dirlinks`. Found by the integration tests. |
| Pre-launch = pull, post-exit = push | Both are a full two-way sync; the planner picks the direction | One code path. A push left pending from an offline session goes up before launch, and conflicts are still caught. |
| `--appid N` required | Defaults to the appid Steam passes in `SteamGameId` | The launch option is the same text for every game |
| (new) | **Mass-delete guard:** if one side has lost *every* file the snapshot knew, those units become conflicts | An unmounted SD card or a reset remote folder must never propagate as "delete everything" |
| `rclone config create` | Setup writes Memcard's own `rclone.conf` directly (0600), with passwords obscured via `rclone obscure -` on stdin | Keeps secrets off the command line, where `ps` would show them. Remotes in the user's own rclone.conf are reused, never edited. |
| NDJSON events | Every `--json` line is `{"event": …}`; each command ends with `result` or `error` | One shape for the UIs to parse |
| (new) | `state/pending.json` lists groups with undecided conflicts | Game Mode syncs leave conflicts for later; UIs and `status` need to know |
| (new) | GC skips unreferenced objects younger than a day, requires the folder marker, and aborts on any listing failure | Another machine's in-flight push may be about to commit them; a failed listing must never make live objects look unreferenced |
| (new) | gc lists every group's `HEAD.json` (size + modtime) before reading manifests and again before each batch of 200 deletes, and stops if any changed. After each commit, the objects the new HEAD references that its parent didn't are listed (`RemoteStore.missing`, one `lsf --files-from` call) and missing ones are uploaded again, with no deadline or cancel. Uploads go through a fresh copy, never a hard link. | A push can reuse an old unreferenced object that `upload()` skips as already present, and a gc elsewhere may delete it before or after our HEAD lands (#2). gc's own window (minutes, reading every group) is closed by its HEAD re-checks; a commit landing just before a batch is covered by the committing machine's check. What's left is a commit during one batch's delete call. rclone carries the source mtime to backends that store one, which gc reads as the upload time: pushes stage through rsync and get fresh mtimes, but a re-upload from the object cache (history restore) would look collectable. |
| (new) | Manifests and snapshots are validated on read: paths must be relative with no `..`/empty/`.` parts, object ids must be 64 lowercase hex | Anyone who can write the cloud folder could otherwise make a pull write or delete files outside the group |
| `copyto` for HEAD.json | `rcat` for every JSON document, and HEAD is read back after commit | `copyto` skips "matching" files, and on WebDAV vendor=other that means same size: equal-length commits were silently dropped. Found in review; engine tests now also run against `rclone serve webdav`. |
| (new) | Dangling symlinks and unreadable folders are reported as *unknown*; the planner leaves those paths alone on every side | An unplugged drive behind a symlink must not look like deleted saves |
| Deadline covers the whole sync | The last safe point is right before a local restore; from there the unit is written whole. SIGTERM sets a cancel flag checked at safe points. | A timeout or cancel mid-restore would have left half a memory card, and `wrap` would launch the game on it |
| (new) | Before overwriting, each pull verifies the trash copy still matches the planned local state, or replans | A save written after the scan (an emulator running outside `wrap`) would otherwise be lost |
| D3: Decky installs `memcard wrap` into launch options | The Decky plugin intercepts Steam's launch (cancel → `sync --before-launch` → relaunch) and runs `sync --after-exit` on Steam's exit event; `wrap` stays for Bazzite and manual use | Steam ROM Manager rewrites shortcut launch options on every parse, and `%command%` expansion for shortcuts is unverified. Intercepting leaves shortcuts untouched and gives the conflict prompt a real modal. UI launches use `running/app-<appid>` markers holding a caller-chosen launch id (12 h expiry) so periodic sync still backs off. `--after-exit` with a non-matching id is ignored (a late/duplicate exit report after a relaunch), and its sync stops at a safe point if another launch starts. **Contract for UIs:** every `--before-launch` is followed by an `--after-exit` with the same id, including when the launch is abandoned; otherwise background sync is suppressed for that app until the marker expires. |
| Config editing via tomlkit | Reading is stdlib `tomllib`; tomlkit is imported only when writing | Hot paths (`wrap`, `sync`) stay stdlib-only |
| (new) | Ships as one ~100 KB zipapp (`scripts/build-zipapp.sh`) | Nothing to install on an immutable OS |
| (new) | `memcard setup install-rclone` downloads rclone's official static build over HTTPS, checks it against the SHA256SUMS published next to it (integrity, not a defence against a compromised site), and keeps it in Memcard's data dir, which `find_tool` prefers | SteamOS has no rclone and no package manager; the Decky plugin needs a one-button install, and "update" must take effect |
| (new) | A plain `memcard sync` refuses while a game is running (a launch marker, or a mapped emulator's Flatpak) | A restore under a running emulator is overwritten when it next saves, or pushes a half-flushed save. Syncing around games belongs to the launch hooks. |
| Decky plugin ships memcard via `remote_binary` | The plugin zip bundles the same zipapp as `bin/memcard`, run with the system python3 | Keeps the plugin and the CLI it drives at the same version; Decky's own interpreter is a PyInstaller bundle |
