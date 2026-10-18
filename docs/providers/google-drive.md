# Google Drive

Google Drive signs in with OAuth in a web browser. **Game Mode on the Steam Deck has
no usable browser**, so the sign-in happens on another computer and you paste the
result into Memcard. The same flow works in Desktop Mode, or on Bazzite.

## 1. Get a token on a computer with a browser

On any computer (Mac, Windows, Linux), [install rclone](https://rclone.org/install/), then run:

```bash
rclone authorize "drive"
```

A browser opens. Sign in to the Google account that should hold your saves and allow
access. Back in the terminal, rclone prints a token between two marker lines:

```
Paste the following into your remote machine --->
{"access_token":"ya29…","token_type":"Bearer","refresh_token":"1//0…","expiry":"2026-10-17T22:00:00Z"}
<---End paste
```

Copy everything from `{` to `}` (one line).

## 2. Give it to Memcard

```bash
memcard setup      # choose Google Drive
```

When asked for the token, paste it. Typing a long token with the Steam keyboard is
painful, so you can instead save it to a file (USB stick, SD card, or a synced folder)
and type `@` plus the path:

```
Token from `rclone authorize "drive"`: @/run/media/deck/USB/drive-token.json
```

Delete that file afterwards; it's a credential.

## 3. Verify

```bash
memcard remote test
```

A `memcard` folder appears in **My Drive**.

## Failure modes

| Message | Cause | Fix |
|---|---|---|
| *Paste the whole token…* | Only part of the line was pasted, or the marker lines were included | Paste exactly from `{` to `}` |
| *Credentials rejected. Google rejected the saved token, or it expired* | Access revoked at myaccount.google.com → Security → Third-party access, or the account password changed | Run `rclone authorize "drive"` again and re-run setup, choosing **update** for the existing remote |
| Token works for a week then stops | You set up your own Google Cloud OAuth client that's still in **Testing** mode; Google expires those tokens after 7 days | Publish the OAuth app, or use rclone's default client (just run `rclone authorize "drive"` as above) |
| Occasional `rateLimitExceeded` in the log | rclone's shared client ID is rate-limited across all rclone users | Usually harmless for small save files. If it persists, create your own client ID per the [rclone Drive docs](https://rclone.org/drive/#making-your-own-client-id). |
