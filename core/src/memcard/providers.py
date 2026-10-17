"""What Memcard knows about each storage provider: setup fields, examples, and error wording.

Nothing here changes how sync works. Every provider goes through the same
rclone remote; this only makes setup and error messages specific.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Field:
    key: str  # rclone option name
    prompt: str
    example: str = ""
    secret: bool = False
    help: str = ""


@dataclass(frozen=True)
class Provider:
    key: str
    label: str
    rclone_type: str
    fields: tuple[Field, ...] = ()
    fixed: dict[str, str] = field(default_factory=dict)  # rclone options set without asking
    oauth: bool = False
    experimental: bool = False
    manual_setup: str = ""  # set when setup must happen in `rclone config` (e.g. 2FA prompts)
    auth_hint: str = ""
    url_example: str = ""
    doc: str = ""
    validate: Callable[[str, str, dict[str, str]], str | None] | None = None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "oauth": self.oauth,
            "experimental": self.experimental,
            "manual_setup": self.manual_setup,
            "doc": self.doc,
            "fields": [f.__dict__ for f in self.fields],
        }


def _check_nextcloud(key: str, value: str, earlier: dict[str, str]) -> str | None:
    if key != "url":
        return None
    user = earlier.get("user") or "USERNAME"
    if not re.match(r"^https?://", value):
        return f"Start the URL with https://, e.g. https://cloud.example.com/remote.php/dav/files/{user}/"
    m = re.search(r"/remote\.php/dav/files/([^/]+)/?$", value)
    if m:
        if earlier.get("user") and m.group(1) != earlier["user"]:
            return f"The URL ends in files/{m.group(1)}/ but your username is {user}. They must match."
        return None
    base = re.sub(r"/(remote\.php.*|index\.php.*|apps/.*)?$", "", value.rstrip("/"))
    return (
        "That's the server address, not the file endpoint rclone needs. "
        f"Use: {base}/remote.php/dav/files/{user}/"
    )


def _check_url(key: str, value: str, earlier: dict[str, str]) -> str | None:
    if key == "url" and not re.match(r"^https?://", value):
        return "Start the URL with https:// (or http:// on a local network)."
    return None


def _token_check(key: str, value: str, earlier: dict[str, str]) -> str | None:
    if key == "token" and not value.strip().startswith("{"):
        return 'Paste the whole token: the line starting with {"access_token" that rclone authorize printed.'
    return None


def _authorize_field(backend: str) -> Field:
    return Field(
        "token",
        f'Token from `rclone authorize "{backend}"`',
        example='{"access_token":"…","token_type":"Bearer","refresh_token":"…","expiry":"…"}',
        secret=True,
        help=(
            f'On any computer with a web browser, install rclone and run: rclone authorize "{backend}". '
            "Sign in, then copy the token it prints and paste it here (or save it to a file and load that file)."
        ),
    )


PROVIDERS: dict[str, Provider] = {
    p.key: p
    for p in [
        Provider(
            key="nextcloud",
            label="Nextcloud",
            rclone_type="webdav",
            fixed={"vendor": "nextcloud"},
            fields=(
                Field("user", "Nextcloud username", example="alice"),
                Field(
                    "url",
                    "WebDAV URL",
                    example="https://cloud.example.com/remote.php/dav/files/alice/",
                    help="Your server address followed by /remote.php/dav/files/<username>/",
                ),
                Field(
                    "pass",
                    "App password",
                    example="xxxxx-xxxxx-xxxxx-xxxxx-xxxxx",
                    secret=True,
                    help="Not your login password. Create one in Nextcloud: Settings → Personal → Security → "
                    "Devices & sessions → Create new app password.",
                ),
            ),
            auth_hint="Nextcloud requires an app password, not your account password. Generate one at "
            "Settings → Personal → Security → Devices & sessions → Create new app password.",
            url_example="https://cloud.example.com/remote.php/dav/files/USERNAME/",
            doc="docs/providers/nextcloud.md",
            validate=_check_nextcloud,
        ),
        Provider(
            key="webdav",
            label="WebDAV (other)",
            rclone_type="webdav",
            fixed={"vendor": "other"},
            fields=(
                Field("url", "WebDAV URL", example="https://dav.example.com/files/"),
                Field("user", "Username", example="alice"),
                Field("pass", "Password", secret=True, help="Use an app-specific password if your server offers them."),
            ),
            auth_hint="Check the username and password. Servers with two-factor login usually need an "
            "app-specific password instead of your normal one.",
            url_example="https://dav.example.com/files/ (the folder URL your server documents for WebDAV)",
            doc="docs/providers/webdav-generic.md",
            validate=_check_url,
        ),
        Provider(
            key="drive",
            label="Google Drive",
            rclone_type="drive",
            fixed={"scope": "drive"},
            fields=(_authorize_field("drive"),),
            oauth=True,
            auth_hint='Google rejected the saved token, or it expired. Run `rclone authorize "drive"` on a computer '
            "with a browser and paste the new token into Memcard setup.",
            doc="docs/providers/google-drive.md",
            validate=_token_check,
        ),
        Provider(
            key="box",
            label="Box",
            rclone_type="box",
            fields=(_authorize_field("box"),),
            oauth=True,
            auth_hint='Box rejected the saved token, or it expired. Run `rclone authorize "box"` on a computer '
            "with a browser and paste the new token into Memcard setup.",
            doc="docs/providers/box.md",
            validate=_token_check,
        ),
        Provider(
            key="icloud",
            label="iCloud Drive",
            rclone_type="iclouddrive",
            experimental=True,
            manual_setup="iCloud sign-in asks for a two-factor code, so create the remote on a desktop with "
            "`rclone config` (choose iclouddrive), then pick it here as an existing remote.",
            auth_hint="iCloud's sign-in expired. This happens every few weeks. On a desktop, run "
            "`rclone config reconnect REMOTE:` and enter the two-factor code. Memcard can't do this from Game Mode.",
            doc="docs/providers/icloud.md",
        ),
    ]
}


def generic(rclone_type: str) -> Provider:
    return Provider(key=rclone_type, label=rclone_type, rclone_type=rclone_type)


def detect(remote_config: dict[str, str]) -> Provider:
    """Guess the provider from an existing rclone remote's config section."""
    rtype = remote_config.get("type", "")
    if rtype == "webdav":
        return PROVIDERS["nextcloud" if remote_config.get("vendor") == "nextcloud" else "webdav"]
    for p in PROVIDERS.values():
        if p.rclone_type == rtype:
            return p
    return generic(rtype)


def get(key: str | None) -> Provider | None:
    return PROVIDERS.get(key) if key else None
