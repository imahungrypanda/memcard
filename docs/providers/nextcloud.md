# Nextcloud

Memcard talks to Nextcloud over WebDAV through rclone. Any Nextcloud server works,
whether self-hosted or from a provider.

## 1. Create an app password

In a browser, signed in to Nextcloud:

**avatar (top right) → Settings → Security (left sidebar) → Devices & sessions**

Type an app name (e.g. `memcard-deck`), then **Create new app password**. Copy the
password shown; Nextcloud won't show it again. Make one per machine, so you can
revoke one without breaking the other.

Your normal login password does **not** work here once two-factor login is on, and
it's a bad idea even when it would.

## 2. Find your username and URL

The URL has a fixed shape:

```
https://<server>/remote.php/dav/files/<username>/
```

Example: for user `alice` on `cloud.example.com`:

```
https://cloud.example.com/remote.php/dav/files/alice/
```

`<username>` is your **user ID**, which isn't always what you type to log in (that
can be your email). To see the exact URL, open the **Files** app → **Files settings**
(bottom left) → **WebDAV**. It shows the full address, ending in your user ID.

If Nextcloud is installed under a sub-path, keep it: `https://example.com/nextcloud/remote.php/dav/files/alice/`.

## 3. Run setup

```bash
memcard setup      # choose Nextcloud
```

Enter the username, the URL and the app password. Memcard checks the URL shape
before contacting the server, then writes, reads back and deletes a test file.

## 4. Verify

```bash
memcard remote test
```

In the Nextcloud web UI you'll see a `memcard` folder containing
`memcard-remote.json`, `groups/` and `objects/`. Files are stored by content hash,
not by save name. Use `memcard export` to see named files.

## Failure modes

| Message | Cause | Fix |
|---|---|---|
| *URL reached the server but not the file endpoint* | URL is just `https://cloud.example.com`, or a page URL like `/index.php/apps/files/` | Use the `/remote.php/dav/files/<username>/` form |
| *Credentials rejected. Nextcloud requires an app password* | Login password used, the app password was revoked, or the username in the URL doesn't match | Make a new app password; copy the URL from **Files settings → WebDAV** |
| *The URL ends in files/bob/ but your username is alice* | The user ID in the URL differs from the username field | Use the same user ID in both |
| Slow logins, then 401s after several failures | Nextcloud's brute-force protection is throttling your IP after the bad attempts | Wait a few minutes, or ask the admin to reset it, then retry with a correct app password |
