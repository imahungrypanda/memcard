# iCloud Drive — **experimental**

iCloud works through rclone's `iclouddrive` backend, but it has limits Memcard
can't hide. Use it only if you have no other option, and check `memcard status`
from time to time.

## Known limitations

- **Sign-in expires every few weeks.** rclone holds a "trust token" that Apple
  expires roughly monthly. When it does, every sync fails with a credentials error.
  In Game Mode that looks like saves silently not syncing; the error is in `memcard log`.
  Fixing it needs a two-factor code, which can't be entered from Game Mode.
- **No in-place replace.** iCloud can't overwrite a file. rclone moves the old copy
  to *Recently Deleted* and uploads a new one. Memcard limits the damage: save data is
  stored write-once by content hash and never overwritten. Only the small manifest
  files are replaced, about two per group per sync. Even so, *Recently Deleted*
  fills up; empty it occasionally.
- **App-specific passwords don't work.** You need your real Apple ID password plus a
  two-factor code. **Passkey-only accounts aren't supported** by rclone.
- **Advanced Data Protection must be off** and web access must be on (below).

## 1. Allow web access

On an iPhone/iPad: **Settings → [your name] → iCloud → Access iCloud Data on the Web**
must be **on**. With **Advanced Data Protection** turned on, rclone can't reach iCloud Drive at all.

## 2. Create the remote with rclone (not Memcard setup)

The two-factor prompt needs an interactive terminal, so create the remote with rclone
itself, in Desktop Mode or on the PC:

```bash
rclone config
# n) New remote → name: icloud → Storage: iclouddrive
# Apple ID: you@example.com
# Password: your Apple ID password
# Then enter the 6-digit code that appears on your Apple device.
```

## 3. Point Memcard at it

```bash
memcard setup      # pick "icloud" from the list of existing remotes
```

Memcard runs its write/read/delete test before accepting it. The `memcard` folder
appears in iCloud Drive.

## 4. When it stops working (every few weeks)

```bash
rclone config reconnect icloud:
memcard remote test
```

## Failure modes

| Message | Cause | Fix |
|---|---|---|
| *iCloud's sign-in expired…* | Trust token expired | `rclone config reconnect icloud:` in Desktop Mode |
| Setup fails with a 401 immediately | App-specific password used, or a passkey-only Apple ID | Use the account password; passkey-only accounts can't be used |
| 403 / access denied | Advanced Data Protection on, or web access off | Turn on *Access iCloud Data on the Web*; ADP must be off |
