# WebDAV (other servers)

For any WebDAV server that isn't Nextcloud: ownCloud, a Synology/QNAP NAS, Apache
or nginx `mod_dav`, Fastmail Files, and others. Memcard uses rclone's `webdav`
backend with vendor `other`.

## 1. Credentials

Use whatever the server uses for WebDAV. Where the server offers **app-specific
passwords** (Fastmail, ownCloud with 2FA), use one. A normal password with two-factor
login on fails with a 401.

## 2. URL format

The URL must be the **WebDAV endpoint of a folder**, not a web page. Examples:

| Server | URL |
|---|---|
| ownCloud | `https://owncloud.example.com/remote.php/dav/files/alice/` |
| Synology (WebDAV Server package, HTTPS) | `https://nas.local:5006/home/` |
| Fastmail Files | `https://myfiles.fastmail.com/` |
| Apache/nginx DAV | whatever `Location` you configured, e.g. `https://dav.example.com/files/` |

## 3. Run setup

```bash
memcard setup      # choose "WebDAV (other)"
```

## 4. Verify

```bash
memcard remote test
```

This writes, reads back and deletes a test file. A `memcard` folder appears on the server.

## Failure modes

| Message | Cause | Fix |
|---|---|---|
| *URL reached the server but not the file endpoint* (404) | Path is wrong or WebDAV isn't enabled at that path | Check the server's WebDAV docs for the exact path |
| *Credentials rejected* (401) | Wrong password, or 2FA requires an app password | Create an app-specific password |
| *rclone couldn't open this remote* / 405 Method Not Allowed | URL points at a normal web page, not a DAV endpoint | Use the DAV URL, not the web UI's |
| TLS / certificate errors on a home NAS | Self-signed certificate | Give the NAS a real certificate (e.g. Let's Encrypt via the NAS's settings). Memcard doesn't turn off certificate checks. |
