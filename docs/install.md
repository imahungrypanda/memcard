# Installing and testing Memcard

- **Steam Deck:** use the **Decky plugin**. Setup, syncing around games, and
  conflict prompts all happen in Game Mode (§2).
- **Bazzite PC:** use the **command line** plus a Steam launch option, until the
  desktop GUI exists (§3). Conflicts are settled in a terminal; until then the
  saves involved are left untouched.

Nothing is ever overwritten without a decision.

Rough time: 20 minutes for the first machine, 10 for the second.

---

## 0. Back up your saves first

This is new software touching your only copies. On each machine, before anything else:

```bash
# Steam Deck (adjust FATTY if your SD card is named differently)
tar -czhf ~/saves-backup-$(date +%F).tar.gz -C /run/media/deck/FATTY/Emulation saves

# Bazzite (adjust the path to wherever EmuDeck put Emulation/)
tar -czhf ~/saves-backup-$(date +%F).tar.gz -C ~/Emulation saves
```

`-h` follows EmuDeck's symlinks so the archive holds the real save files, not links.

---

## 1. Build it (on your Mac, once)

```bash
cd ~/Code/memcard
scripts/build-decky.sh         # -> dist/memcard-decky.zip (the plugin) and dist/memcard (the CLI)
```

`dist/memcard` is the whole command-line program, a single ~130 KB file. It needs only
the system `python3` (3.11+), `rsync` and `rclone`, so nothing gets installed into the
read-only OS. The plugin zip bundles the same file.

Get it onto each machine any way you like: USB stick, your Nextcloud, or `scp`
(on the Deck, set a password with `passwd` and run `sudo systemctl start sshd` first):

```bash
scp dist/memcard-decky.zip deck@steamdeck:~/Downloads/
scp dist/memcard bazzite-user@bazzite:~/memcard
```

---

## 2. Steam Deck / Steam Machine (Decky plugin)

You need [Decky Loader](https://decky.xyz) installed.

1. **Turn on developer mode in Decky:** Quick Access (`…` button) → Decky (plug icon) → ⚙
   Settings → **Developer mode** on.
2. **Install the plugin:** Decky ⚙ → **Developer** → **Install plugin from URL** →
   `https://github.com/imahungrypanda/memcard/releases/latest/download/memcard-decky.zip`.
   After that, Memcard checks for updates itself: you get an "update available" toast, or an
   **Update to …** button under **Updates** in its panel, and Decky asks you to confirm.
   If an update ever fails, Memcard can disappear from the list (Decky removes the old version
   first). Install from the URL above again; your settings and saves are untouched.
   (A local build works too: **Install plugin from ZIP file** → `memcard-decky.zip`.)
3. **Open Memcard** in the Quick Access menu and press **Set up Memcard**:
   - **Name this machine**, e.g. "Steam Deck" or "Steam Machine".
   - **Choose where your saves live.** For Nextcloud, first make an app password in a browser:
     **avatar → Settings → Security → Devices & sessions → Create new app password**.
     Then enter the username, the URL (`https://YOUR-SERVER/remote.php/dav/files/YOUR-USERNAME/`)
     and the app password, one at a time. Each one is checked before moving on.
   - If it says rclone isn't installed, press **Install rclone**. It downloads the official
     build, checks its checksum, and keeps it in Memcard's own folder.
   - Memcard writes a test file, reads it back and deletes it. It only says
     **Connected** if all three work.
4. Press **Find EmuDeck saves**, check the list, and press **Add**. Groups get mapped to
   your Steam shortcuts automatically.
5. Press **Sync now** for the first sync (on the first machine everything goes up).

From then on, pressing **Play** on a mapped game syncs first, asks about any conflict
in a pop-up, then starts the game. After you quit, it uploads the new saves. You'll see
a short toast each time. Turn this off with **Sync around games** in the panel.

Review the groups in `~/.config/memcard/config.toml` (Desktop Mode) if you want to
change what's synced; see the `unit` notes in §2.5 below.

**Without the plugin**, you can set the Deck up exactly like the PC: the rest of this
section is the terminal route, and §4 covers the Steam launch option.

### Terminal route

Switch to **Desktop Mode** and open **Konsole**.

### 2.1 Check the prerequisites

```bash
python3 --version   # needs 3.11 or newer
rsync --version     # ships with SteamOS
```

If either is missing or too old, stop and tell me the output. Don't try to
install system packages on SteamOS.

### 2.2 Install rclone and Memcard into your home folder

Both survive SteamOS updates.

```bash
mkdir -p ~/.local/bin
mv ~/memcard ~/.local/bin/memcard     # wherever you copied it to
chmod +x ~/.local/bin/memcard

# Make it available in Konsole from now on
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

memcard --version
memcard setup install-rclone          # skip if `rclone version` already works
```

`install-rclone` downloads the official static build, checks it against rclone's
published SHA-256 sums, and keeps it in `~/.local/share/memcard/bin/`, where Memcard
looks for it.

### 2.3 Name this machine

```bash
memcard init --name "Steam Deck"
```

The name appears in conflict prompts on your other machine ("last written by Steam Deck").

### 2.4 Connect your Nextcloud

Do this first in Nextcloud, in a browser: **avatar (top right) → Settings →
Security → Devices & sessions**, type `memcard` as the app name, then **Create new
app password**. Copy it. Your normal login password won't work.

Then:

```bash
memcard setup
```

It asks one thing at a time:

| Prompt | What to enter |
|---|---|
| Provider | `Nextcloud` |
| Name for this remote | `nextcloud` (fine as is) |
| Nextcloud username | your **user ID**, as in the URL below; not your email |
| WebDAV URL | `https://YOUR-SERVER/remote.php/dav/files/YOUR-USERNAME/` |
| App password | the one you just made (typing is hidden) |
| Folder on the remote | `memcard` (fine as is) |

It checks the URL before contacting the server. Then it writes a test file, reads it
back and deletes it. It only says "Connected" if all three work.

> Already have an rclone remote set up? `memcard setup` lists it first and offers to reuse it.

More detail and failure modes: [providers/nextcloud.md](providers/nextcloud.md).

### 2.5 Find your saves

```bash
memcard detect --emudeck /run/media/deck/FATTY/Emulation
```

This lists one group per emulator save folder, and how many of your Steam shortcuts
launch that emulator. Nothing is written yet. If the list looks right:

```bash
memcard detect --emudeck /run/media/deck/FATTY/Emulation --write
```

Then look over `~/.config/memcard/config.toml`. Things worth checking:

- **`unit`** is what must move together:
  - `file`: each file stands alone (RetroArch `.srm`).
  - `dir`: each top-level folder is one save (PPSSPP `SAVEDATA/<game>`).
  - `group`: everything moves together (Dolphin memory cards).
- Groups marked **⚠ unknown emulator** default to `group`, the safest choice.
- Delete any `[[group]]` you don't want synced.

### 2.6 First sync

```bash
memcard status      # dry run: what would go up/down, changes nothing
memcard sync
```

On the first machine everything is pushed up.

---

## 3. Bazzite PC

Same steps in a terminal (Ptyxis/Konsole). Differences:

- **Prerequisites:** `python3 --version`, `rsync --version` and `rclone version`.
  If rclone is already installed, skip the download in 2.2.
- **Name:** `memcard init --name "Bazzite"` (or whatever you'll recognise).
- **Setup:** pick the **same provider, server and folder** (`memcard`). Make a
  separate Nextcloud app password for this machine, so you can revoke one without
  breaking the other.
- **Detect:** point it at wherever EmuDeck lives here, e.g. `memcard detect --emudeck ~/Emulation`.

**Group ids must match on both machines.** They're how Memcard knows Deck's
`retroarch` is PC's `retroarch`. `detect` derives them from EmuDeck's folder names, so
they'll match if both machines use EmuDeck. Compare the two `config.toml` files to be sure.

### 3.1 The first sync on the second machine

Any save that exists on **both** machines with different contents is a conflict
the first time, because Memcard has no history yet to say which is newer. It won't
guess. Run `memcard status` first, then pick one approach.

**Interactive.** `memcard sync` prompts for each conflict. It shows both sides' size,
modified time and which machine wrote the remote copy. Then choose:

- **keep local:** this machine's copy wins everywhere.
- **keep remote:** the Deck's copy wins here.
- **keep both:** single files get the local copy renamed, e.g.
  `Game (Bazzite 2026-10-17 2130).srm`. For memory-card style groups, the local
  copy is kept in the trash as a named alternate.

**Whole groups at once.** If you know which machine is ahead:

```bash
memcard sync --resolve retroarch=remote     # take the Deck's RetroArch saves
memcard sync --resolve dolphin-gc=local     # keep this PC's Dolphin cards
```

Whatever you pick, the version that loses is copied to the trash first (§6).

---

## 4. Hook it into Steam (PC, or a Deck without the plugin)

The Decky plugin does this for you on the Deck. Everywhere else, a launch option makes
sync automatic. It pulls before a game starts and pushes after it fully exits.

In Steam (Game Mode works): select the game → **⚙ → Properties → Launch Options**,
and put this **in front of** whatever is already there:

```
/home/deck/.local/bin/memcard wrap -- %command%
```

On Bazzite, use your own path; `echo ~/.local/bin/memcard` prints it. Use the
full path: Steam doesn't search `~/.local/bin`.

Memcard reads the game's appid from Steam's environment and syncs only the groups
`detect` mapped to it. If an appid isn't mapped, the game launches with no sync and a
log line.

**What happens on launch**

1. Pre-launch sync. It's capped at `launch_timeout_s` (default 20 s); after that
   the game starts with local saves.
2. The game runs. Memcard sits in a blocking wait: no CPU and no file watching.
3. After exit, it waits for the emulator's Flatpak to be gone and for save files to
   stop changing (up to `exit_settle_s`, default 30 s).
4. Push at idle CPU and IO priority, so it won't slow the next game you start.

> **Steam ROM Manager:** re-running SRM can rewrite launch options. If your
> EmuDeck shortcuts come from SRM, either re-add the prefix after each SRM run, or set
> the parser's *Executable modifier* to `/home/deck/.local/bin/memcard wrap -- "${exePath}"`.
> I haven't been able to test the SRM route; see the checklist below.

### Optional: periodic background sync

Off by default. To turn it on:

```bash
memcard timer enable --minutes 30
```

This is a systemd timer that runs one short sync and exits. It skips any run while a
game is going. `memcard timer disable` turns it off.

---

## 5. Day to day

| You want to | Run |
|---|---|
| See what would sync | `memcard status` |
| Sync now | `memcard sync` (or `memcard sync retroarch`) |
| Settle conflicts Game Mode skipped | `memcard sync`; it prompts |
| See what happened | `memcard log` (file: `~/.local/state/memcard/memcard.log`) |
| Check the connection | `memcard remote test` |
| Add a new emulator | `memcard detect --emudeck … --write` |

Conflicts waiting for a decision are listed in `~/.local/state/memcard/pending.json`.

---

## 6. Getting saves back

Nothing is overwritten or deleted without a copy.

- **Local trash:** every overwrite or delete is copied first to
  `~/.local/share/memcard/trash/<group>/<timestamp>/`. The last 5 are kept per group
  (`trash_versions`). Keep-both alternates are never pruned.

  ```bash
  memcard trash list
  memcard trash restore retroarch 20261017T213000Z            # whole batch
  memcard trash restore retroarch 20261017T213000Z gba/FireRed.srm
  memcard sync                                                 # push the restored version
  ```

- **Cloud versions:** every sync that uploads something makes a new version of the
  group in the cloud. `memcard gc` prunes them to `history_versions` per group (20 in new
  configs; older configs say 5).
  List them, and put one game's save back on every machine:

  ```bash
  memcard history list retroarch                        # newest first, with what each changed
  memcard history list retroarch --unit FireRed.srm     # only versions that changed that save
  memcard history restore retroarch 12 --unit FireRed.srm
  ```

  A restore first syncs (so nothing unsynced is lost), then saves a new version in which
  that save is copied from version 12, then downloads it here. This machine's current
  copy goes to the trash first, your other machines get it on their next sync, and the
  version you replaced stays in the list, so you can undo it. `--all` instead of `--unit`
  puts back every save that existed in version 12 (newer saves are kept). Emulators that
  sync as one unit (Dolphin, Citra, Cemu, Ryujinx, xemu) always restore whole and need
  neither.

  To copy any version out as plain files instead:

  ```bash
  memcard export retroarch ~/rescue                 # current
  memcard export retroarch ~/rescue --generation 12
  ```

The remote stores files by content hash, so you won't see `.srm` names in
Nextcloud's web UI. That's what lets every sync land all at once or not at all. Use
`memcard export` to see the files.

---

## 7. Troubleshooting

| Symptom | Likely cause |
|---|---|
| `Save folder for 'x' isn't there` | SD card not mounted. The group was skipped and the remote untouched. |
| `Credentials rejected… app password` | Used the login password, or the app password was revoked. Re-run `memcard setup`. |
| `URL reached the server but not the file endpoint` | URL is missing `/remote.php/dav/files/<user>/`. |
| `This device is offline` | Saves are fine locally; the next sync catches up. |
| A game didn't sync | Check `memcard log`. Usually the appid isn't mapped: run `memcard detect --write` again. |
| `Every save in this group is missing on this machine` | Memcard refused to propagate a mass delete, usually from an unmounted card. Choose **keep remote** to restore. |

Add `-v` to any command to include raw rclone output when reporting a problem.

**Uninstall:** remove the launch-option prefixes, run `memcard timer disable`, then
delete `~/.local/bin/memcard`, `~/.config/memcard`, `~/.local/state/memcard`,
`~/.local/share/memcard` and `~/.cache/memcard`. Your saves are untouched. The
`memcard` folder on Nextcloud can be deleted from the web UI.

---

## 8. Test checklist: what I couldn't verify

The automated tests run real rsync and rclone against a local stand-in for the
cloud, with EmuDeck-shaped symlinks. These parts need your hardware:

- [ ] `python3 --version` on the Deck and Bazzite is 3.11+.
- [ ] Setup against your real Nextcloud: the URL check, the round trip, and the
      401 message if you deliberately paste your login password.
- [ ] `memcard detect` on the real `/run/media/deck/FATTY/Emulation` finds the
      emulators you use, and maps your Steam shortcuts to them.
- [ ] Pull on the Deck writes **through** EmuDeck's symlinks. After a sync, this should
      still print `->` arrows, not plain folders:
      `ls -l /run/media/deck/FATTY/Emulation/saves/*/`
- [ ] The launch option works for an SRM shortcut in Game Mode: the game starts, and
      `memcard log` shows `prelaunch` before and `postexit` after.
- [ ] Post-exit waits for the emulator: save in-game, quit, and the push includes that save.
- [ ] No noticeable slowdown starting or playing a game.
- [ ] A real conflict: play the same game on both machines without syncing, then resolve it.

**Decky plugin** (none of this has run on a real Deck yet):

- [ ] The plugin installs from the zip and the Memcard panel opens.
- [ ] Setup works end to end in Game Mode: the Steam keyboard in text fields, password
      fields hidden, Install rclone, and the "Connected" round trip.
- [ ] Pressing Play on a mapped SRM shortcut shows "Syncing saves before launch…" and
      the game then starts **once** (not twice, and not stuck in a cancel loop).
- [ ] A conflict before launch shows the pop-up, each button does what it says, and the
      game starts afterwards.
- [ ] After quitting, a "Saves synced" toast appears, and `memcard log` shows `postexit`.
      (If no toast appears, Steam may report appid 0 for shortcuts; tell me.)
- [ ] Updates: with an older version installed, the panel shows **Update to …**; pressing it
      brings up Decky's install prompt, and after confirming, Decky's plugin list shows the new version.
- [ ] **Check for updates** in the panel answers ("Up to date (0.1.x)"), not "Failed to fetch".
- [ ] The **Update** button is greyed out while a game runs or its saves are syncing.
- [ ] Toast titles name the game ("Memcard · <game>"), for ROM shortcuts too.
- [ ] An unmapped game and a regular Steam game launch exactly as before, with no delay.
- [ ] Pressing Play twice quickly gives a "Still syncing saves…" toast and one launch, not two.
- [ ] Resuming a running game from the library doesn't sync or cancel anything.
- [ ] While a game runs, the panel's Sync now is greyed out with a note.
- [ ] Reload the plugin mid-game (Decky ⚙ → reload, or reinstall the zip), then: Resume
      doesn't sync, and quitting still shows "Saves synced".
- [ ] Find EmuDeck saves → Add maps your shortcuts. Check what would sync and Sync now
      show sensible results.

Tell me which boxes fail, with `memcard log -n 100` output, and I'll fix them.
