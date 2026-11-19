# Memcard — cloud versions (`memcard history`)

Status: **approved 2026-11-18**, not built yet. Progress is tracked in `tasks/todo.md` under **Next**.

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
  so today history grows without limit. The list therefore needs a limit of its own.
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
2. **Restore picks units, not whole versions.** `--unit KEY` can be repeated. Without `--unit`,
   every unit is restored, and that's the only choice for `unit = group` emulators.
   - A unit that doesn't exist in the chosen version is refused ("not saved yet then").
     Restore is not a back door for deleting saves.
   - The exception is `unit = group`, where the whole group is one unit. Files added after that
     version are removed. The confirm screen says so, and those files stay in the trash and in history.
3. **Sync, restore, sync, all under one lock.**
   - The first sync pushes unsynced local changes, so they become a version too instead of only
     sitting in the trash.
   - If the group still has an undecided conflict, restore refuses: "Resolve the conflict first."
4. **Restore refuses while a game runs** (`GameRunning`, the same check as a plain `sync`).
   **Browsing is allowed during a game.** Listing only reads from the cloud, so the panel keeps
   Cloud versions open and only disables **Restore**, with a note.
5. **New configs keep 20 versions per group** (`history_versions` default goes from 5 to 20).
   Manifests are a few KB, and unchanged saves share objects, so each version costs only the saves
   it changed. Existing configs keep the value they already wrote.
6. **Restores are labelled.** Manifests get an optional `restored_from: <gen>`. `FORMAT` stays 1,
   because older clients ignore unknown keys. The list shows "Restored from Yesterday, 21:40".
7. **The entry point is each Save groups row** in the Decky panel ("Versions ›"). There's no
   separate Tools button with a group picker.
8. **`memcard history --json` says, per version, which saves differ from the current version**, so
   the detail screen needs no second command (§5).

## 4. What "one game" means for each emulator

Restore works per **unit**, the same thing a sync keeps together (`Group.unit_key`):

| `unit` | Emulators (EmuDeck defaults) | What one restore puts back |
|---|---|---|
| `file` | RetroArch, DuckStation, melonDS, mGBA, ScummVM | One save file, which is one game (`Super Metroid (Japan, USA).srm`) |
| `dir` | PPSSPP, RPCS3, PCSX2 | One top-level entry. That's a game folder on PPSSPP and RPCS3. On **PCSX2 it's a memory card**, so every game on that card goes back |
| `group` | Dolphin, PrimeHack, Citra/Azahar, Cemu, Ryujinx, xemu | The emulator's whole save folder. These are one unit on purpose, so a save is never put back half from one version and half from another |

## 5. Core

### 5.1 Restore, step by step

`Engine.restore(group, gen, units, decide)` holds `sync_lock` for the whole run. The lock isn't
re-entrant, so it calls `_sync_group` directly, not `sync()`.

1. `_sync_group`: an ordinary sync of this group, which pushes local changes.
2. Refuse if the group has unresolved conflicts.
3. Read `HEAD` and `manifest_at(gen)`.
4. `history.restore_files(head.files, old.files, units, group)`. Start from HEAD. For every path
   this machine's `group.wants()` in the chosen units, take the old version's entry, or drop the
   path if the old version doesn't have it. Paths in other units, or not wanted here, are untouched.
5. `store.fetch()` the old version's objects. This proves they exist and are intact, and leaves them
   in the local cache for the pull.
6. Commit `Manifest(generation=head+1, parent=head, restored_from=gen, committed_by=this machine)`
   through the usual compare-and-swap.
7. Check that the new HEAD's objects are on the remote (`RemoteStore.missing`, from the #2 fix), and
   re-upload any missing ones from the cache.
8. `_sync_group` again. The planner sees `L == S`, `R != S` and pulls, trashing the current copies first.

`RemoteChanged` during step 6 retries from step 3, up to `ATTEMPTS`, as a sync does. Every step is
either idempotent or a single atomic write, so an interrupted restore is either not visible at all
(it died before the commit), or finished by the next sync (it died after).

### 5.2 Listing

- `Engine.history(group, limit=20, unit=None)` reads the newest `limit + 1` manifests and diffs each
  one against its parent.
- It reads them with **one** `rclone copy --files-from` into scratch
  (`RemoteStore.manifests(group, gens)`). A list is three rclone calls however long it is:
  `lsf history/`, `copy`, `cat HEAD.json`.
- Every manifest goes through `Manifest.from_dict`, because everything read from the remote is
  untrusted. Bad data raises `RemoteCorrupt`.
- `--unit KEY` keeps only the versions that changed that unit.
- `changes` is `null` for the oldest version when its parent has already been pruned.

### 5.3 New pure module `history.py`

No IO and no clock, like `planner.py`:

- `diff(prev, cur, group) -> list[Change]`: path, unit key, `added | modified | deleted`, size,
  filtered by `group.wants()`.
- `compare(version, head, group)`: per unit, the files whose content differs from HEAD, with their
  "then" and "now" entries. A unit is `restorable` only if it exists in the version, unless the
  group's unit is `group`.
- `restore_files(...)`: §5.1 step 4. Raises on an unknown unit, a unit not in that version, or
  nothing to change.

### 5.4 CLI

```
memcard history GROUP [--limit 20] [--unit KEY]
memcard history restore GROUP GEN [--unit KEY]... [--resolve GROUP[:UNIT]=local|remote|both]
```

`history restore` ends with the same `results` shape as `sync`. A UI can reuse its toasts and
conflict handling, and the exit codes are the same: 2 conflicts, 3 offline, 4 config.

`memcard history retroarch --json`, abridged:

```json
{
  "group": "retroarch", "unit": "file", "head": 48, "more": true,
  "versions": [
    {
      "generation": 45,
      "committed_at": "2026-11-17T21:40:12Z",
      "machine": "Steam Deck",
      "restored_from": null,
      "current": false,
      "changes": [
        {"unit": "Legend of Zelda, The - A Link to the Past (USA).srm",
         "path": "Legend of Zelda, The - A Link to the Past (USA).srm",
         "change": "modified", "size": 8192}
      ],
      "differs": [
        {"unit": "Legend of Zelda, The - A Link to the Past (USA).srm", "restorable": true,
         "files": [{"path": "Legend of Zelda, The - A Link to the Past (USA).srm",
                    "then": {"size": 8192, "written_at": "2026-11-17T21:40:12Z", "machine": "Steam Deck"},
                    "now":  {"size": 8192, "written_at": "2026-11-17T22:41:03Z", "machine": "Steam Deck"}}]}
      ]
    }
  ]
}
```

`machine` names come from the manifest's `machines` map. Ids that aren't in it are shown as "another machine".

### 5.5 The gc race (#2), fixed first

After any commit, push or restore, `RemoteStore.missing(shas)` lists just the objects the new HEAD
references that its parent didn't. It's one rclone call, usually for a handful of objects. Any missing
object is uploaded again: from the staging folder for a push, or from the object cache for a restore.
This closes #2 for all commits, not only restores.

## 6. Decky: Cloud versions

The mock linked at the top shows this flow. Wording follows the plugin's rules: say "this machine" and
"the cloud", never "Deck", and every button says what it does.

1. **Panel.** Each Save groups row opens that group's versions ("Versions ›"). The row doesn't show a
   version count: `groups` is offline, and the panel shouldn't hit the network just to draw itself.
2. **Version list** (`history GROUP --json`). Newest first. Each row shows the date, the machine, and
   the saves it changed ("A Link to the Past changed"). The current version has a **Current** pill, and
   restores have a **Restore** pill plus "Restored from …".
   - For `file` and `dir` groups, a dropdown ("Show versions that changed …") re-runs the command
     with `--unit`.
   - Under the list: "Memcard keeps the last 20 versions of each group."
3. **Version detail** (from the same JSON's `differs`).
   - **`file` and `dir` groups:** one toggle per save that differs from now, each with
     "In this version: Yesterday, 21:40 · Steam Deck → Now: Yesterday, 22:41 · Steam Deck".
     The toggles start **on for the saves this version changed** (or the filtered save), never all
     of them. Saves that didn't exist yet are listed greyed out.
   - **`group` groups:** no toggles. It says the saves are restored together and lists the files that
     change or get removed.
4. **Confirm.** For example: "**A Link to the Past** goes back to the version from **Yesterday, 21:40**
   (Steam Deck). This machine's current copy goes to Memcard's trash first. Your other machines get the
   restored save on their next sync. The version you're replacing stays in Cloud versions, so you can
   undo this."
   - A PCSX2 memory card adds: "Mcd001 is a memory card. Every game saved on it goes back to that version."
5. **Restoring.** The steps are shown: syncing first, saving the restored version to the cloud,
   downloading it. Then a toast ("Memcard · RetroArch: Restored A Link to the Past from Yesterday, 21:40…"),
   and the list reloads with the new version on top.
   - Conflicts reported by the pre-sync go through the existing `askResolutions` → `sync --resolve`.
6. **Blocked states.** The **Restore** button is disabled, with the reason shown, while a game runs
   ("Restoring waits until you quit the game") or while the group has a pending conflict
   ("Resolve it in the Memcard panel first"). Browsing stays available.

Code layout mirrors the conflict prompt:

- `src/historyText.ts` holds the wording and date labels. It's pure and tested in `tests/history.test.ts`.
- `src/history.tsx` only renders.
- `runner.py` adds `history` to `ALLOWED`.

## 7. Rollout

| PR | Scope |
|---|---|
| 1 | `fix(core)`: re-upload objects a commit references but the remote lost (closes #2) |
| 2 | `feat(core)`: `history.py`, `RemoteStore.manifests`/`missing`, `Engine.history`/`restore`, CLI, `restored_from`, `history_versions = 20`; docs (`CLAUDE.md`, design §8, install §6) |
| 3 | `feat(decky)`: Cloud versions modal, allowlist, `historyText.ts` + tests, install §8 hardware checklist |

## 8. Tests

- **Unit** (`tests/unit/test_history.py`):
  - `diff` table.
  - `compare` and `restore_files` for each unit kind, with `wants()` filtering.
  - A unit missing from the version is refused, and a restore that changes nothing is refused.
- **Integration** (`tests/integration/test_history.py`, on both the `local` backend and WebDAV):
  - Two machines. A pushes v1 and v2 of units X and Y, then restores X@v1. B syncs and gets X@v1 and Y@v2.
  - The list shows `restored_from`.
  - A dirty local save is pushed by the pre-sync and shows up as its own version.
  - A pending conflict makes restore refuse.
  - A commit landing mid-restore causes a replan.
  - After gc prunes the parent, the oldest entry's `changes` is `null`.
  - The `--json` stream ends in `result`.
- **#2:** an object deleted between upload and commit is back after the sync, and a second machine pulls cleanly.
- **Decky:** `node --test` for `historyText.ts`; `test_runner.py` for the allowlist.
- **Hardware** (install §8): restore one RetroArch save from the Deck, and check the Steam Machine picks it
  up on its next sync. Also check that the dropdown and toggles work with the controller.
