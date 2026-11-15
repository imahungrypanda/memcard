// Is there a newer Memcard release? Pure (no @decky imports) so Node can test it.
//
// Releases come from GitHub (release.yml publishes one per green build of main). We don't use a
// custom Decky store: Decky has a single store URL, so a custom one would replace the official
// store for every other plugin. Instead the plugin checks GitHub itself and hands the zip to
// Decky's own installer, which asks the player to confirm and verifies the SHA-256.

export const REPO = "imahungrypanda/memcard";
export const RELEASES_API = `https://api.github.com/repos/${REPO}/releases/latest`;
export const ASSET = "memcard-decky.zip";

export interface Update {
  version: string;
  artifact: string; // zip URL
  hash: string; // sha256 hex of the zip; Decky won't unpack a download that doesn't match
  page: string; // release notes
}

const VERSION = /^v?(\d+)\.(\d+)\.(\d+)$/;

/** Numeric x.y.z comparison; returns null if either side isn't a plain version. */
export function compareVersions(a: string, b: string): number | null {
  const x = VERSION.exec(a.trim());
  const y = VERSION.exec(b.trim());
  if (!x || !y) return null;
  for (let i = 1; i <= 3; i++) {
    const d = Number(x[i]) - Number(y[i]);
    if (d) return Math.sign(d);
  }
  return 0;
}

/** The installable update described by a GitHub "latest release" response, or null. */
export function parseRelease(json: unknown): Update | null {
  if (!json || typeof json !== "object") return null;
  const r = json as Record<string, unknown>;
  if (r.draft || r.prerelease || typeof r.tag_name !== "string") return null;
  const m = VERSION.exec(r.tag_name);
  if (!m) return null;
  const assets = Array.isArray(r.assets) ? (r.assets as Record<string, unknown>[]) : [];
  const zip = assets.find((a) => a && a.name === ASSET);
  if (!zip || typeof zip.browser_download_url !== "string" || typeof zip.digest !== "string") return null;
  const hash = /^sha256:([0-9a-f]{64})$/.exec(zip.digest)?.[1];
  // Only ever install from this repo's own release downloads.
  const prefix = `https://github.com/${REPO}/releases/download/`;
  if (!hash || !zip.browser_download_url.startsWith(prefix)) return null;
  return {
    version: `${m[1]}.${m[2]}.${m[3]}`,
    artifact: zip.browser_download_url,
    hash,
    page: typeof r.html_url === "string" ? r.html_url : `https://github.com/${REPO}/releases`,
  };
}

/** The update to offer, if the release is newer than what's installed. */
export function availableUpdate(installed: string | null | undefined, release: unknown): Update | null {
  const u = parseRelease(release);
  if (!u) return null;
  // An unknown or non-release installed version (a local build) is offered the release too.
  const cmp = installed ? compareVersions(u.version, installed) : null;
  return cmp === null || cmp > 0 ? u : null;
}
