# Memcard — cloud versions (`memcard history`)

Status: **approved 2026-11-18**; updated after review. Built in #16 (gc race) and the PRs that follow. Progress is tracked in `tasks/todo.md` under **Next**.

Mock of the Decky flow: <https://claude.ai/artifact/Gx9LLozbY7ic3hQHFJ221D>. The link is private to the
owner's claude.ai account, so it won't open for everyone. Section 6 describes the same screens in words.

---

## 1. Why

The worst thing a save syncer can do is spread a broken save to every machine: a corrupted
memory card, a save overwritten by a fresh game, a bad conflict choice. Memcard already keeps
what's needed to undo that:

- Every commit writes `groups/<id>/history/<gen>.json` next to `HEAD.json` (design §2.3).
- `RemoteStore.history()` and `manifest_at()` read those versions.
- `memcard export GROUP DIR --generation N` copies one of them to a plain folder.

Nothing lets you **see** the versions ("which one is from before it broke?") or **put one back**
without copying files by hand, and none of it is reachable in Game Mode. This feature adds both.

## 2. Things that shape the design

- **A group holds many games.** RetroArch is one group with `unit = file` (`detect.py` `KNOWN`).
  Restoring a whole version would roll back every RetroArch game. Restore has to work **per unit**,
  and the list has to say **what changed** in each version.
- **Every push is a version.** Play three other games and the version you want is several entries
  down. A list of bare generation numbers is useless; each entry needs a date, a machine and the
  saves it changed.
- **History is only pruned by a manual `memcard gc`.** `history_versions` only applies when gc runs,
  and the machine that runs it decides the number. Today history grows without limit. The list
  therefore needs a limit of its own.
- **Restore re-references old objects.** That makes the gc race in #2 more likely: a gc elsewhere
  can delete an object between our read and our commit. The #2 fix lands first and covers restores.

## 3. Decisions

1. **A restore reaches every machine.** It commits a *new* cloud version in which the chosen saves
   are copied from the old one. This machine then syncs and pulls them, and the other machines
   get them on their next sync.
   - Nothing is lost: the version you replaced stays in history, and this machine's copies go
     to the trash first.
   - The conflict model doesn't change. The planner sees an ordinary remote change.
   - *Rejected:* "restore on this machine only". The next sync would fight it, and it would need
     new conflict rules.
2. **Restore picks units, not whole versions.**
   - **`file` and `dir` groups:** you name the saves with `--unit KEY` (repeatable), or pass `--all`
     explicitly. One RetroArch group holds every RetroArch game, so rolling all of them back is never
     the default. `--all` restores every unit that existed in that version; saves added since are kept.
   - **`group` groups:** always restore whole, and take no `--unit`.
   - **Units that didn't exist then are refused** ("wasn't saved yet then"). Restore is not a back
     door for deleting saves.
   - **Files added later inside a restored unit are removed**: a `dir` unit's folder or a `group`
     unit's whole tree goes back exactly as it was. The confirm screen says so, and those files stay
     in the trash and in history.
3. **A unit is restored with every path the cloud has for it.** That includes paths this machine's
   `include`/`exclude` patterns leave out. Otherwise a machine with broader patterns would pull back
   half a unit: the paths this machine wants from the old version, the rest from now.
4. **Sync, restore, sync, all under one lock.**
   - The first sync pushes unsynced local changes, so they become a version of their own.
   - `--resolve` applies to the first sync only. If it doesn't come back clean (a conflict, an error,
     a missing folder), nothing is restored, and the result carries the conflict like `sync`.
   - The second sync uses no decisions. A conflict there (a save changed on this machine during the
     restore) is left for the user, never settled automatically over the restore.
5. **Never under a running game.**
   - Restore is refused while a game runs (`GameRunning`, the same check as a plain `sync`).
   - If a game starts during a restore, it stops at the engine's next safe point (`abort_if`).
     After the commit, that means before any local write. The message says the cloud already has
     the restored version and this machine gets it on its next sync.
   - **Browsing is allowed during a game**, at idle priority (`lower_priority`): it's three small
     rclone reads. The panel only disables **Restore**.
6. **New configs keep 20 versions per group** (`history_versions` default and template go from 5 to 20).
   Manifests are a few KB, and unchanged saves share objects, so each version costs only the saves
   it changed. Existing configs keep the value they already wrote, and pruning only happens when
   someone runs `memcard gc`, so the UI doesn't promise a number.
7. **Restores are labelled.**
   - Manifests get an optional `restored_from: <gen>`. `FORMAT` stays 1, because older clients
     ignore unknown keys.
   - Restored file entries keep their hash, size and mtime, but are stamped `written_by` the restoring
     machine at the restore time. That's when the cloud got them back, and what a later conflict
     prompt should say.
8. **The entry point is each Save groups row** in the Decky panel ("Versions ›"). There's no
   separate Tools button with a group picker.
9. **`memcard history list --json` says, per version, which saves differ from the current version**,
   so the detail screen needs no second command (§5.4).
10. **A restore that would look like "the cloud lost every save" is refused.** That's a restore
    whose *result* shares no file paths with the current HEAD. Every machine's mass-delete guard would read it as a
    wiped remote and offer to upload the saves again, undoing the restore. `memcard export` remains
    the way out.

## 4. What "one game" means for each emulator

Restore works per **unit**, the same thing a sync keeps together (`Group.unit_key`):

| `unit` | Emulators (EmuDeck defaults) | What one restore puts back |
|---|---|---|
| `file` | RetroArch, DuckStation, melonDS, mGBA, ScummVM | One save file. Usually one game (`Super Metroid (Japan, USA).srm`), but not always: RetroArch keeps a game's `.rtc` beside its `.srm` (restore both), and DuckStation can use shared memory cards |
| `dir` | PPSSPP, RPCS3, PCSX2 | One top-level entry. That's a game folder on PPSSPP and RPCS3. On **PCSX2 it's a memory card**, so every game on that card goes back |
| `group` | Dolphin, PrimeHack, Citra/Azahar, Cemu, Ryujinx, xemu | The emulator's whole save folder. These are one unit on purpose, so a save is never put back half from one version and half from another |

## 5. Core

### 5.1 Restore, step by step

`Engine.restore(group, gen, units, decide, abort_if)` holds `sync_lock` for the whole run. `sync()`
is split into a lock wrapper and `_sync_locked()`, so both syncs below get the full sync treatment:
events, `pending.json`, and skipped and error handling.

1. `_sync_locked([group], decide)`: an ordinary sync of this group. If it isn't `ok` with no
   conflicts, stop and return its result.
2. Safe point (deadline, SIGTERM, `abort_if`).
3. Read `HEAD` and `manifest_at(gen)`. Refuse `gen >= head`, a missing version, or one whose
   `group`/`generation` don't match its file name.
4. `history.restore_files(head.files, old.files, units, group, machine, now)`. Start from HEAD. For
   every path in the chosen units, across all machines' paths, take the old entry (re-stamped), or
   drop the path if the old version lacks it.
   - Refuses unknown or absent units, nothing to change, and §3.10.
5. `store.fetch()` only the objects the restore brings back. This proves they exist and are intact,
   and leaves them in the local cache for the pull.
6. Commit `Manifest(generation=head+1, parent=head, restored_from=gen)` through the usual
   compare-and-swap.
7. `_ensure_objects`: check the newly referenced objects are on the remote (`RemoteStore.missing`),
   and re-upload any missing ones from the cache. This runs with no deadline or cancel (§5.5).
8. `_sync_locked([group], no_decisions)`. The planner sees `L == S`, `R != S` and pulls, trashing
   the current copies first.

`RemoteChanged` during steps 3–6 retries from step 3, up to `ATTEMPTS`. Every step is either
idempotent or a single atomic write, so an interrupted restore is either not visible at all (it
died before the commit), or finished by the next sync (it died after).

### 5.2 Listing

- `Engine.history(group, limit=20, unit=None)` reads the newest `limit + 1` manifests and diffs each
  one against its parent.
- **HEAD is authoritative.** `commit()` writes `history/<gen>.json` before `HEAD.json`, so a history
  file newer than HEAD is a commit that never landed (it died, or lost the compare-and-swap). The
  list skips those, and uses HEAD itself for the current entry.
- It reads them with **one** `rclone copy --files-from --no-traverse` into a fresh temp dir
  (`RemoteStore.manifests`). A list is three rclone calls however long it is: `cat HEAD.json`,
  `lsf history/`, `copy`.
- A version pruned between the listing and the copy is skipped; rclone exits 0 for a missing
  `--files-from` entry (checked on local and WebDAV).
- Every manifest goes through `Manifest.from_dict`, because everything read from the remote is
  untrusted. Bad data raises `RemoteCorrupt`.
- `--unit KEY` keeps only the versions, among the newest `limit`, that changed that unit.
- `changes` is `null` for the oldest version when its parent has already been pruned.
- Nothing is filtered by this machine's `wants()`. The list is the cloud's view, matching §3.3.

### 5.3 New pure module `history.py`

No IO and no clock, like `planner.py`:

- `diff(prev, cur, group) -> list[Change] | None`: path, unit key, `added | modified | deleted`,
  and the new size. `None` when the parent is unknown.
- `compare(version, head, group) -> list[UnitDiff]`: per unit, the files whose content differs from
  HEAD, with their "then" and "now" entries. `restorable` is true when the unit exists in that version.
- `restore_files(head, old, units, group, machine_id, now)`: §5.1 step 4.

### 5.4 CLI

```
memcard history list GROUP [--limit 20] [--unit KEY]
memcard history restore GROUP GEN (--unit KEY... | --all) [--resolve GROUP[:UNIT]=…] [--non-interactive]
```

`list` and `restore` subcommands, like `trash list|restore`. A bare `history GROUP` can't be parsed
next to a `restore` subcommand, and `restore` is a valid group id. For `group`-unit groups,
`--unit` and `--all` are unnecessary.

`memcard history list retroarch --json`, abridged:

```json
{
  "group": "retroarch", "unit": "file", "head": 48, "more": true,
  "machines": {"id-deck": "Steam Deck", "id-pc": "Steam Machine"},
  "versions": [
    {
      "generation": 45,
      "committed_at": "2026-11-17T21:40:12Z",
      "committed_by": "id-deck",
      "machine": "Steam Deck",
      "restored_from": null,
      "current": false,
      "changes": [
        {"unit": "Zelda.srm", "path": "Zelda.srm", "change": "modified", "size": 8192}
      ],
      "differs": [
        {"unit": "Zelda.srm", "restorable": true,
         "files": [{"path": "Zelda.srm",
                    "then": {"size": 8192, "written_at": "2026-11-17T21:40:12Z", "written_by": "id-deck", "machine": "Steam Deck"},
                    "now":  {"size": 8192, "written_at": "2026-11-17T22:41:03Z", "written_by": "id-deck", "machine": "Steam Deck"}}]}
      ]
    }
  ]
}
```

- Machine ids appear next to names, so a UI can say "this machine" when the id is its own. The
  top-level `machines` map merges the listed manifests' maps, with HEAD's names winning.
- Ids missing from `machines` are named "another machine".

`memcard history restore … --json` ends with:

```json
{"results": [<sync before>, <sync after>], "restored": {"group": "retroarch", "generation": 49, "restored_from": 45, "units": ["Zelda.srm"]}}
```

- `results` has the `sync` result shape, one entry per sync that ran.
- `restored` is `null` when nothing was restored. That happens when the first sync came back with a
  conflict or an error; `results` then has one entry.
- **Exit codes:**
  - 0: done
  - 2: a conflict (before, so nothing restored; or after, with the restore committed)
  - 3: offline
  - 4: config. A bad or missing `--unit`, a missing version, `--unit` on a `group` group, or a
    `file`/`dir` group without `--unit` or `--all`.
  - 1: everything else. `game_running` (code `game_running`), `cancelled` (a game started; the
    message says whether the cloud already has the restore), and "nothing to restore" or
    "already the current version".

### 5.5 The gc race (#2), fixed first

Two halves:

- **gc backs off.** gc lists every group's `HEAD.json` (size and modtime, one
  `lsjson --max-depth 2` call) before reading any manifest, and again right before deleting. If any
  changed or appeared, it deletes nothing and asks to be run again. This covers gc's own long window
  (reading every group, then listing every object).
- **A commit checks its objects.** After any commit, push or restore, `RemoteStore.missing(shas)`
  lists the objects the new HEAD references that its parent didn't. That's one
  `lsf -R --files-from` call, which only lists those prefix folders. Missing objects are uploaded
  again: from staging for a push, from the object cache for a restore.
  - It runs with no deadline or cancel. Past the commit point, a later sync sees `L == R` and never
    re-uploads.
  - This covers the few calls between gc's second listing and its delete.
- **Fresh mtimes.** Uploads are fresh copies, never hard links. rclone carries the source mtime to
  backends that store one, and gc's grace period reads it as the upload time. A re-upload from the
  cache would otherwise look old.

## 6. Decky: Cloud versions

The mock linked at the top shows this flow. Wording follows the plugin's rules: say "this machine" and
"the cloud", never "Deck", and every button says what it does.

1. **Panel.** Each Save groups row opens that group's versions ("Versions ›"). The row doesn't show a
   version count: `groups` is offline, and the panel shouldn't hit the network just to draw itself.
2. **Version list** (`history list GROUP --json`). Newest first. Each row shows the date, the machine
   ("this machine" when the id is its own), and the saves it changed ("A Link to the Past changed").
   The current version has a **Current** pill, and restores have a **Restore** pill plus
   "Restored from …".
   - For `file` and `dir` groups, a dropdown ("Show versions that changed …") re-runs the command
     with `--unit`.
3. **Version detail** (from the same JSON's `differs`).
   - **`file` and `dir` groups:** one toggle per save that differs from now, each with
     "In this version: Yesterday, 21:40 · Steam Deck → Now: Yesterday, 22:41 · this machine".
     The toggles start **on for the saves this version changed** (or the filtered save), never all
     of them. Saves that didn't exist yet are listed greyed out. Restore passes one `--unit` per toggle.
   - **`group` groups:** no toggles. It says the saves are restored together, and lists the files that
     change or get removed.
4. **Confirm.** For example: "**A Link to the Past** goes back to the version from **Yesterday, 21:40**
   (Steam Deck). This machine's current copy goes to Memcard's trash first. Your other machines get the
   restored save on their next sync. The version you're replacing stays in Cloud versions, so you can
   undo this."
   - A PCSX2 memory card adds: "Mcd001 is a memory card. Every game saved on it goes back to that version."
   - When a `dir` or `group` restore removes files that didn't exist then, it lists them.
5. **Restoring.** The steps are shown: syncing first, saving the restored version to the cloud,
   downloading it. Then a toast ("Memcard · RetroArch: Restored A Link to the Past from Yesterday, 21:40…"),
   and the list reloads with the new version on top.
   - Conflicts from the first sync go through the existing `askResolutions`. The restore then runs
     again with `--resolve …`.
6. **Blocked states.** The **Restore** button is disabled, with the reason shown, while a game runs
   ("You can restore after you quit the game") or while the group has a pending conflict
   ("Resolve it in the Memcard panel first"). Browsing stays available.

Code layout mirrors the conflict prompt:

- `src/historyText.ts` holds the wording and date labels. It's pure and tested in `tests/history.test.ts`.
- `src/history.tsx` only renders.
- `runner.py` adds `history` to `ALLOWED`.

## 7. Rollout

| PR | Scope |
|---|---|
| #16 | `fix(core)`: both halves of the gc race (§5.5); closes #2 |
| 2 | `feat(core)`: `history.py`, `RemoteStore.manifests`, `Engine.history`/`restore` (with `sync()` split into `_sync_locked`), CLI, `restored_from`, `history_versions = 20`; docs (`CLAUDE.md`, design §8, install §6) |
| 3 | `feat(decky)`: Cloud versions modal, allowlist, `historyText.ts` + tests, install §8 hardware checklist |

## 8. Tests

- **Unit** (`tests/unit/test_history.py`):
  - `diff` and `compare` for each unit kind.
  - `restore_files` restores whole units, including paths this machine doesn't sync, and stamps the
    restorer.
  - It refuses absent and unknown units, no-op restores, and restores whose result shares no paths with HEAD.
- **Integration** (`tests/integration/test_cloud_versions.py`, on both the `local` backend and WebDAV):
  - Two machines. A pushes v1 and v2 of units X and Y, then restores X@v1. B syncs and gets X@v1 and Y@v2.
  - The list shows `restored_from`, `differs`, `--unit` filtering, `limit`/`more`, and a pruned parent
    (`changes: null`).
  - A dirty local save is pushed by the first sync, appears as its own version, and can be restored back.
  - A conflict stops the restore before anything changes; `--resolve` lets it proceed.
  - A commit landing mid-restore is retried and kept.
  - A restored object lost to gc is re-uploaded from the cache.
  - A game starting after the commit stops before any local write.
- **CLI** (`tests/integration/test_cli.py`):
  - `--json` list and restore end in `result`.
  - `--unit`/`--all` is required for file groups.
  - Refused while a game runs; config errors exit 4.
- **#2:** an object deleted between upload and commit is back after the sync, also when the sync is
  cancelled right after the commit. gc deletes nothing when a HEAD changes under it.
- **Decky:** `node --test` for `historyText.ts`; `test_runner.py` for the allowlist.
- **Hardware** (install §8): restore one RetroArch save from the Deck, and check the Steam Machine picks it
  up on its next sync. Also check that the dropdown and toggles work with the controller.
