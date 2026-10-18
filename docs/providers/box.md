# Box

Box signs in with OAuth in a web browser. **Game Mode on the Steam Deck has no
usable browser**, so the sign-in happens on another computer and you paste the
result into Memcard.

## 1. Get a token on a computer with a browser

On any computer, [install rclone](https://rclone.org/install/), then run:

```bash
rclone authorize "box"
```

Sign in to Box in the browser that opens and grant access. rclone prints a token.
Copy everything from `{` to `}`.

**Do this once per machine.** Box rotates its refresh token on every use. If the
Deck and the PC share one token, whichever refreshes first invalidates the other, and
sync on that machine silently starts failing with 401s.

## 2. Give it to Memcard

```bash
memcard setup      # choose Box
```

Paste the token, or type `@/path/to/token-file` to load it from a file (handy with
the Steam keyboard). Delete that file afterwards.

## 3. Verify

```bash
memcard remote test
```

A `memcard` folder appears in **All Files**.

## Failure modes

| Message | Cause | Fix |
|---|---|---|
| *Credentials rejected. Box rejected the saved token…* on one machine only | Both machines were set up with the same token (see above) | Run `rclone authorize "box"` again for that machine and update its remote in setup |
| *Credentials rejected* after a long break | Box refresh tokens expire after about 60 days unused | Re-authorize and update the remote |
| 403 *refused access to this folder* | A managed (work/school) Box account where admins restrict third-party apps | Use a personal account, or ask the admin to allow rclone |
