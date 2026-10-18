# Memcard

Two-way game save sync for Linux handhelds and desktops, backed by your own cloud
storage through rclone. Put the Steam Deck down, pick up on the PC, keep playing.

- **Never guesses.** Three-way comparison (this machine, the remote, last sync).
  When both sides changed, it asks; it never does last-write-wins and never trusts
  FAT32 or WebDAV timestamps.
- **Never destroys.** Every overwrite or delete is copied to a local trash first;
  the remote keeps recent versions too.
- **Never half-writes.** A sync lands on the remote all at once or not at all, and an
  interrupted sync finishes when it next runs.
- **Understands EmuDeck.** Follows its symlinks into Flatpak data dirs, writes
  through them on restore, and maps Steam shortcuts to emulators by appid.
- **Stays out of the way.** Nothing runs while you play.

| Part | Status |
|---|---|
| `memcard-core`: library + CLI (`core/`) | ✅ built, tested |
| `memcard-decky`: Steam Deck Game Mode plugin | next |
| `memcard-gui`: desktop app (Flatpak) for Bazzite | after Decky |

**Start here:** [docs/install.md](docs/install.md) · Design: [docs/design.md](docs/design.md) ·
Providers: [Nextcloud](docs/providers/nextcloud.md), [Google Drive](docs/providers/google-drive.md),
[Box](docs/providers/box.md), [WebDAV](docs/providers/webdav-generic.md),
[iCloud (experimental)](docs/providers/icloud.md)

## Development

```bash
cd core
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest            # integration tests need GNU rsync + rclone (macOS: brew install rsync rclone)
../scripts/build-zipapp.sh  # -> dist/memcard
```
